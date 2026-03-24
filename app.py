import json
import base64
import io
import re
import os
import time
import streamlit as st
import groq

# ---------- 将背景图片转换为 Base64 嵌入 CSS ----------
def get_base64_of_image(image_path):
    try:
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode()
    except FileNotFoundError:
        return None

bg_base64 = get_base64_of_image("background.jpg")
if bg_base64 is None:
    st.warning("Background image not found. Using solid light background.")
    bg_css = "background-color: #f0f0f0;"
else:
    bg_css = f"background-image: url('data:image/jpeg;base64,{bg_base64}');"

# Page config
st.set_page_config(
    layout="wide",
    page_title="LVING PDF Assistant",
    initial_sidebar_state="collapsed",
    menu_items=None
)

# ---------- 初始化语言状态 ----------
if "language" not in st.session_state:
    st.session_state.language = "Chinese"  # 默认中文

# ---------- 加载所有 Level 数据（根据语言） ----------
@st.cache_data
def load_level_data(language):
    levels = {}
    suffix = "_en" if language == "English" else ""
    for i in range(1, 4):
        try:
            filename = f"level{i}{suffix}.json"
            with open(filename, "r", encoding="utf-8") as f:
                levels[f"Level {i}"] = json.load(f)
        except FileNotFoundError:
            st.error(f"{filename} not found. Please ensure all level files exist.")
            st.stop()
    return levels

levels_data = load_level_data(st.session_state.language)

# ---------- Groq 客户端 ----------
client = groq.Client(api_key=os.environ.get("GROQ_API_KEY") or st.secrets["GROQ_API_KEY"])

# ---------- 加载 Kokoro TTS ----------
@st.cache_resource
def load_kokoro():
    try:
        from kokoro_onnx import Kokoro
        model_path = "kokoro-chinese/model_static.onnx"
        voices_path = "kokoro-chinese/voices"
        if os.path.exists(model_path) and os.path.exists(voices_path):
            return Kokoro(model_path, voices_path)
        return None
    except Exception:
        return None

# ---------- 语音转文字（Whisper）----------
def transcribe_audio(audio_bytes):
    try:
        transcription = client.audio.transcriptions.create(
            file=("audio.wav", audio_bytes, "audio/wav"),
            model="whisper-large-v3",
        )
        return transcription.text
    except Exception as e:
        st.error(f"语音识别失败: {e}")
        return None

# ---------- 判断文本是否含中文 ----------
def has_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fff]', text))

# ---------- 文字转语音 ----------
def text_to_speech(text):
    kokoro = load_kokoro()
    if kokoro is not None:
        try:
            import soundfile as sf
            voice = "zf_001" if has_chinese(text) else "af_sol"
            samples, sample_rate = kokoro.create(text, voice=voice, speed=1.0)
            buf = io.BytesIO()
            sf.write(buf, samples, sample_rate, format="WAV")
            buf.seek(0)
            return buf.read(), "audio/wav"
        except Exception as e:
            print(f"Kokoro TTS error: {e}")
            pass
    # Fallback: Groq Orpheus
    try:
        response = client.audio.speech.create(
            model="canopylabs/orpheus-v1-english",
            voice="autumn",
            input=text,
            response_format="wav",
        )
        return response.read(), "audio/wav"
    except Exception as e:
        print(f"Orpheus TTS error: {e}")
        return None, None

# ---------- 构建系统提示 ----------
def build_system_prompt(levels):
    prompt = """You are a language learning assistant helping students learn Languages.
You have access to learning materials across 3 levels covering grammar, vocabulary, and conversation.
Keep your answers concise, clear, and helpful. Focus on what the user is currently studying. No emojis!"""
    return prompt

system_prompt = build_system_prompt(levels_data)

# ---------- 初始化状态 ----------
if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "system", "content": system_prompt}]
if "level" not in st.session_state:
    st.session_state.level = None
if "path" not in st.session_state:
    st.session_state.path = []
if "chat_open" not in st.session_state:
    st.session_state.chat_open = False
if "last_audio_id" not in st.session_state:
    st.session_state.last_audio_id = None
if "pending_tts" not in st.session_state:
    st.session_state.pending_tts = None  # (bytes, fmt)

# ========== 对话总结相关状态 ==========
if "conversation_summary" not in st.session_state:
    st.session_state.conversation_summary = ""
if "conv_history" not in st.session_state:
    st.session_state.conv_history = []
if "user_msg_count" not in st.session_state:
    st.session_state.user_msg_count = 0

# ========== 自动参考相关状态 ==========
if "auto_ref_pushed" not in st.session_state:
    st.session_state.auto_ref_pushed = False
if "current_recommendations" not in st.session_state:
    st.session_state.current_recommendations = None

# ========== 卡片翻转状态 ==========
if "flip_states" not in st.session_state:
    st.session_state.flip_states = {}

# ========== 搜索相关状态 ==========
if "search_keyword" not in st.session_state:
    st.session_state.search_keyword = ""
if "search_results" not in st.session_state:
    st.session_state.search_results = []

# ---------- 获取当前页面全部内容 ----------
def get_current_page_full_content():
    if not st.session_state.level or not st.session_state.path:
        return None
    data = levels_data[f"Level {st.session_state.level}"]
    node = data
    for key in st.session_state.path:
        node = node.get(key, {})
        if not node:
            return None
    parts = []
    location = " > ".join(st.session_state.path)
    parts.append(f"The user is currently viewing: {location}")
    if "name" in node:
        parts.append(f"Section: {node['name']}")
    if "notes" in node and node["notes"]:
        parts.append(f"Notes: {node['notes']}")
    if "examples" in node and node["examples"]:
        parts.append("Example sentences:\n" + "\n".join(f"  - {e}" for e in node["examples"]))
    if "vocabulary" in node and node["vocabulary"]:
        parts.append("Vocabulary:\n" + "\n".join(f"  - {v}" for v in node["vocabulary"]))
    return "\n".join(parts)

# ========== 全局搜索函数 ==========
def search_in_node(node, path_list, level_num, keyword):
    """递归搜索节点，返回匹配项列表"""
    matches = []
    keyword_lower = keyword.lower()
    
    # 搜索 name
    if "name" in node and keyword_lower in node["name"].lower():
        matches.append({
            "level": level_num,
            "path": path_list,
            "type": "Section",
            "content": node["name"]
        })
    
    # 搜索 notes
    if "notes" in node and keyword_lower in node["notes"].lower():
        # 截取前后文
        content = node["notes"][:200] + "..." if len(node["notes"]) > 200 else node["notes"]
        matches.append({
            "level": level_num,
            "path": path_list,
            "type": "Note",
            "content": content
        })
    
    # 搜索 examples
    if "examples" in node:
        for idx, ex in enumerate(node["examples"]):
            if keyword_lower in ex.lower():
                matches.append({
                    "level": level_num,
                    "path": path_list,
                    "type": "Example",
                    "content": ex,
                    "index": idx
                })
    
    # 搜索 vocabulary
    if "vocabulary" in node:
        for idx, item in enumerate(node["vocabulary"]):
            if keyword_lower in item.lower():
                matches.append({
                    "level": level_num,
                    "path": path_list,
                    "type": "Vocabulary",
                    "content": item,
                    "index": idx
                })
    
    # 递归搜索子节点
    for key, value in node.items():
        if isinstance(value, dict) and key not in ("name", "notes", "examples", "vocabulary"):
            matches.extend(search_in_node(value, path_list + [key], level_num, keyword))
    
    return matches

def global_search(keyword):
    """全局搜索所有级别"""
    if not keyword.strip():
        return []
    results = []
    for level_num in range(1, 4):
        level_key = f"Level {level_num}"
        if level_key in levels_data:
            root_node = levels_data[level_key]
            # 根节点可能是一个包含 LEVEL_I, LEVEL_II 等键的字典
            # 遍历顶层所有键（这些键就是路径的起始）
            for root_key, root_value in root_node.items():
                if isinstance(root_value, dict):
                    results.extend(search_in_node(root_value, [root_key], level_num, keyword))
    return results

# ========== 自动生成参考消息 ==========
def auto_generate_reference(level, full_page_content, path_string):
    topic = ""
    if "Section:" in full_page_content:
        import re
        match = re.search(r"Section: (.+)", full_page_content)
        if match:
            topic = match.group(1)
    if not topic:
        parts = path_string.split(" > ")
        topic = parts[-1] if parts else "general"

    notes = ""
    if "Notes:" in full_page_content:
        notes_match = re.search(r"Notes: (.+?)(?:Example|Vocabulary|$)", full_page_content, re.DOTALL)
        if notes_match:
            notes = notes_match.group(1).strip()[:200]

    # 根据语言选择不同的提示和链接模板
    if st.session_state.language == "Chinese":
        prompt = f"""You are a Chinese learning assistant. The user is at Level {level} studying: "{topic}".

Topic summary: {notes if notes else "Basic Chinese learning topic"}

Your task:
- Generate 3-4 high-quality learning resources using fixed trusted platforms
- DO search the web
- Use the topic keyword to build real, valid search links
- Keep it concise
- No emojis!

Use these rules to generate links:
- YouTube: https://www.youtube.com/results?search_query=关键词+in+chinese
- Quizlet: https://quizlet.com/search?query=关键词(中文)+chinese
- StackExchange: https://chinese.stackexchange.com/search?q=关键词(only 1 main keyword)

Example format:
【Recommended Resources】

- YouTube: Beginner explanation video  
  [Watch](https://www.youtube.com/results?search_query=chinese+grammar+chinese)

- Quizlet: Flashcards for practice  
  [Practice](https://quizlet.com/search?query=chinese+grammar+chinese)

- Chinese StackExchange: Community Q&A discussion  
  [Explore](https://chinese.stackexchange.com/search?q=chinese+grammar)

Now generate for: {topic}
"""
    else:  # English
        prompt = f"""You are an English learning assistant. The user is at Level {level} studying: "{topic}".

Topic summary: {notes if notes else "Basic English learning topic"}

Your task:
- Generate 3-4 high-quality learning resources using fixed trusted platforms
- DO search the web
- Use the topic keyword to build real, valid search links
- Keep it concise
- No emojis!

Use these rules to generate links:
- YouTube: https://www.youtube.com/results?search_query=advanced english+key_word
- Quizlet: https://quizlet.com/search?query=advanced english+key_words+vocabulary
- StackExchange: https://english.stackexchange.com/search?q=only1_key_word

Example format:
【Recommended Resources】

- YouTube: Beginner explanation video  
  [Watch](https://www.youtube.com/results?search_query=english+grammar+english+learning)

- Quizlet: Flashcards for practice  
  [Practice](https://quizlet.com/search?query=english+grammar+english+vocabulary)

- English StackExchange: Community Q&A discussion  
  [Explore](https://english.stackexchange.com/search?q=english+grammar)

Now generate for: {topic}
"""
    max_retries = 2
    retry_delay = 3
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=500,
            )
            ref_text = response.choices[0].message.content.strip()
            return ref_text
        except Exception as e:
            error_str = str(e).lower()
            if "413" in error_str or "too large" in error_str:
                return f"**Resources for {topic}**\n\nPlease use the AI chat to ask for specific learning resources."
            if "rate" in error_str or "429" in error_str:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
            return None
    return None

# ========== 自动推送参考消息 ==========
def auto_push_reference(level, path_string):
    if st.session_state.auto_ref_pushed:
        return
    full_page_content = get_current_page_full_content()
    if full_page_content:
        ref_msg = auto_generate_reference(level, full_page_content, path_string)
        if ref_msg:
            st.session_state.current_recommendations = ref_msg
        st.session_state.auto_ref_pushed = True

# ========== AI 回复函数（修改版：每次调用都注入当前语言和页面内容） ==========
def get_ai_reply(user_input):
    st.session_state.messages.append({"role": "user", "content": user_input})
    st.session_state.user_msg_count += 1
    st.session_state.conv_history.append({"role": "user", "content": user_input})

    # 获取当前页面内容
    full_page = get_current_page_full_content()

    # 构建上下文：复制对话历史，并动态插入当前语言、页面内容、对话总结
    context_msgs = st.session_state.messages.copy()

    # 1. 插入当前语言信息（紧跟在原始系统提示之后）
    if st.session_state.language:
        lang_msg = {"role": "system", "content": f"The user is currently learning {st.session_state.language}."}
        context_msgs.insert(1, lang_msg)

    # 2. 插入当前页面内容（如果有）
    if full_page:
        # 根据语言信息是否插入，决定插入位置
        insert_idx = 2 if st.session_state.language else 1
        context_msgs.insert(insert_idx, {"role": "system", "content": full_page})

    # 3. 插入对话总结（如果有）
    if st.session_state.conversation_summary:
        summary_msg = {
            "role": "system",
            "content": f"[Previous conversation summary]\n{st.session_state.conversation_summary}"
        }
        # 计算插入位置：在语言和页面内容之后
        base = 1
        if st.session_state.language:
            base += 1
        if full_page:
            base += 1
        context_msgs.insert(base, summary_msg)

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=context_msgs,
            temperature=0.7,
            max_tokens=512,
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        reply = f"[Error: {e}]"

    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.session_state.conv_history.append({"role": "assistant", "content": reply})

    # TTS生成（错误不会传播到页面）
    try:
        audio_bytes, fmt = text_to_speech(reply)
        if audio_bytes:
            st.session_state.pending_tts = (audio_bytes, fmt)
    except Exception as e:
        print(f"TTS error in get_ai_reply: {e}")

    # 每隔5轮用户消息生成总结
    if st.session_state.user_msg_count % 5 == 0 and st.session_state.user_msg_count > 0:
        generate_and_save_summary()

# ========== 生成并保存对话总结 ==========
def generate_and_save_summary():
    if not st.session_state.conv_history:
        return

    conv_text = "\n".join([f"{m['role'].capitalize()}: {m['content']}" for m in st.session_state.conv_history])

    summary_prompt = f"""The following is a conversation between a user and an AI Chinese learning assistant.
Please provide a concise summary (2-3 sentences) covering the main topics discussed.

Conversation:
{conv_text}

Summary:"""
    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": summary_prompt}],
            temperature=0.5,
            max_tokens=200,
        )
        new_summary = response.choices[0].message.content.strip()

        if st.session_state.conversation_summary:
            st.session_state.conversation_summary += "\n\n" + new_summary
        else:
            st.session_state.conversation_summary = new_summary

        with open("conversation_summary.txt", "w", encoding="utf-8") as f:
            f.write(st.session_state.conversation_summary)

        st.session_state.conv_history = []
    except Exception as e:
        st.warning(f"Failed to generate summary: {e}")

# ---------- CSS样式 ----------
st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@200;300;400;500;600;700;800&display=swap');

    .stApp {{
        {bg_css}
        background-size: cover;
        background-position: center;
        background-attachment: fixed;
        font-family: 'Manrope', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }}

    * {{
        font-family: 'Manrope', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
    }}

    .stApp {{
        background-color: rgba(255, 255, 255, 0.5) !important;
        background-blend-mode: overlay !important;
    }}

    /* 隐藏Streamlit顶部黑框和工具栏 */
    header[data-testid="stHeader"] {{
        display: none !important;
    }}
    .stDeployButton {{
        display: none !important;
    }}
    section[data-testid="stSidebar"] {{
        display: none !important;
    }}
    #MainMenu {{
        display: none !important;
    }}
    footer {{
        display: none !important;
    }}

    /* 隐藏弹窗和对话框 */
    div[role="dialog"] {{
        display: none !important;
    }}
    div[data-testid="stModal"] {{
        display: none !important;
    }}
    .stAlert {{
        display: none !important;
    }}

    /* 隐藏所有覆盖层和遮罩 */
    div[data-baseweb="drawer"] {{
        display: none !important;
    }}
    div[data-baseweb="modal"] {{
        display: none !important;
    }}
    div[class*="overlay"] {{
        display: none !important;
    }}
    div[class*="backdrop"] {{
        display: none !important;
    }}
    div[class*="Overlay"] {{
        display: none !important;
    }}
    div[style*="position: fixed"][style*="inset: 0"] {{
        pointer-events: none !important;
        background: transparent !important;
    }}

    /* 聊天输入框背景透明 */
    div[data-testid="stChatInput"] textarea,
    div[data-testid="stChatInput"] > div {{
        background-color: transparent !important;
        background: transparent !important;
    }}

    /* 聊天消息容器背景透明 */
    div[data-testid="stChatMessage"] {{
        background-color: rgba(240, 240, 240, 0.4) !important;
        backdrop-filter: blur(5px);
    }}

    /* 输入框区域整体背景透明 */
    .stChatInputContainer,
    div[data-testid="stChatInputContainer"] {{
        background-color: transparent !important;
    }}

    div[data-testid="stAppViewBlockContainer"] {{
        background: transparent !important;
    }}

    /* 语言选择器容器样式 */
    .language-selector {{
        position: fixed;
        top: 20px;
        right: 20px;
        z-index: 1000;
        background: rgba(255, 255, 255, 0.95);
        padding: 10px 20px;
        border-radius: 25px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        display: flex;
        align-items: center;
        gap: 10px;
    }}

    .language-selector label {{
        font-family: 'Manrope', sans-serif;
        font-weight: 700;
        color: #000000;
        margin: 0;
        font-size: 16px;
    }}

    .language-selector div[data-baseweb="select"] {{
        background-color: white !important;
    }}
    .language-selector div[data-baseweb="select"] > div {{
        background-color: white !important;
        color: #ffffff !important;
        border: 1px solid #ccc !important;
        font-family: 'Manrope', sans-serif !important;
        font-size: 16px !important;
        font-weight: 600 !important;
    }}
    .language-selector div[data-baseweb="popover"] {{
        z-index: 1001 !important;
        display: block !important;
    }}
    div[role="listbox"] {{
        background-color: white !important;
        color: #ffffff  !important;
        display: block !important;
    }}
    div[role="option"] {{
        color: #ffffff !important;
        font-weight: 500 !important;
    }}

    /* 主标题 */
    h1 {{
        text-align: left;
        color: #000000;
        font-family: 'Manrope', sans-serif;
        font-size: 300px;
        font-weight: 800;
        word-break: break-word;
        max-width: 100%;
        margin-bottom: 40px;
        letter-spacing: normal;
        line-height: 1.1;
    }}

    @media (max-width: 768px) {{
        h1 {{
            font-size: 96px;
        }}
    }}

    /* Level按钮 */
    button[kind="primary"],
    .stButton button {{
        background-color: rgba(255,255,255,0.4) !important;
        color: #000000 !important;
        font-family: 'Manrope', sans-serif !important;
        font-size: 100px !important;
        font-weight: 800 !important;
        padding: 30px !important;
        transition: all 0.3s ease !important;
        box-shadow: 0 4px 8px rgba(0,0,0,0.2) !important;
        letter-spacing: normal !important;
    }}

    .stButton button > div {{
        font-size: 92px !important;
        font-weight: 800 !important;
    }}

    .stButton button:hover {{
        background-color: rgba(255,255,255,0.6) !important;
        transform: translateY(-2px);
        box-shadow: 0 6px 12px rgba(0,0,0,0.3) !important;
    }}

    /* 面包屑导航 */
    .breadcrumb {{
        background-color: rgba(255,255,255,0);
        padding: 12px 20px;
        border-radius: 8px;
        margin-bottom: 20px;
        font-family: 'Manrope', sans-serif;
        font-size: 18px;
        color: #000000;
        font-weight: 700;
        border: none;
        letter-spacing: normal;
    }}

    /* Back按钮 */
    .back-button {{
        margin-bottom: 20px;
    }}
    button[key="back_button"] {{
        background-color: rgba(255,255,255,0.4) !important;
        color: #000000 !important;
        font-family: 'Manrope', sans-serif !important;
        font-size: 18px !important;
        font-weight: 700 !important;
        border: 1px solid rgba(100,100,100,0.3) !important;
        border-radius: 8px !important;
        padding: 10px 24px !important;
    }}

    /* 容器样式 */
    div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlock"] {{
        background-color: rgba(255,255,255,0.5);
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 15px;
        border: none;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }}

    /* 标题 */
    h2 {{
        color: #000000;
        font-family: 'Manrope', sans-serif;
        font-weight: 800;
        margin-bottom: 15px;
        font-size: 56px;
        letter-spacing: normal;
        line-height: 1.2;
    }}
    h3 {{
        color: #000000;
        font-family: 'Manrope', sans-serif;
        font-weight: 700;
        margin-top: 20px;
        margin-bottom: 10px;
        font-size: 36px;
        letter-spacing: normal;
    }}

    /* 确保所有文本都是黑色并使用Manrope字体 */
    p, div, span {{
        color: #000000 !important;
        font-family: 'Manrope', sans-serif !important;
        font-weight: 400 !important;
        line-height: 1.6 !important;
    }}

    hr {{
        margin: 30px 0;
        border: none;
        border-top: 2px solid rgba(100,100,100,0.2);
    }}

    a {{
        color: #0066cc !important;
        text-decoration: none !important;
        font-family: 'Manrope', sans-serif !important;
        font-weight: 600 !important;
    }}
    a:hover {{
        color: #0052a3 !important;
        text-decoration: underline !important;
    }}

    /* 聊天消息区域 */
    .chat-messages-area {{
        flex: 1;
        overflow-y: auto;
        padding: 20px;
        border-bottom: 1px solid rgba(200,200,200,0.3);
    }}
    .chat-message {{
        margin-bottom: 15px;
        padding: 12px;
        background-color: rgba(240,240,240,0.4);
        border-radius: 8px;
        font-family: 'Manrope', sans-serif;
        font-size: 15px;
        font-weight: 400;
        line-height: 1.6;
        color: #000000;
    }}
    .chat-message strong {{
        color: #000000;
        font-weight: 700;
    }}

    /* 输入区域 */
    .stChatInput {{
        border-radius: 15px !important;
        border: 1px solid rgba(0,0,0,0.3) !important;
        background-color: rgba(18,19,28,0.9) !important;
        font-family: 'Manrope', sans-serif !important;
        font-size: 16px !important;
        font-weight: 400 !important;
        color: #ffffff !important;
    }}
    .stChatInput > div {{
        background: transparent !important;
    }}
    .stChatInput button {{
        background: transparent !important;
        border: none !important;
    }}

    .stChatInput textarea::placeholder {{
        color: #bbb !important;
        font-family: 'Manrope', sans-serif !important;
        font-size: 16px !important;
        font-weight: 400 !important;
        background: transparent !important;
        border: none !important;
    }}

    /* Clear按钮 */
    button[key="clear_chat"] {{
        background-color: rgba(255,255,255,0.4) !important;
        border: 1px solid rgba(100,100,100,0.3) !important;
        border-radius: 8px !important;
        padding: 6px 8px !important;
        font-family: 'Manrope', sans-serif !important;
        font-size: 14px !important;
        font-weight: 600 !important;
        color: #000000 !important;
        box-shadow: none !important;
    }}

    /* 完全隐藏所有音频播放器 */
    .stAudio {{
        display: none !important;
    }}

    div[data-testid="stAudioInput"] {{
        margin: 4px 0 !important;
        background: transparent !important;
    }}
    div[data-testid="stAudioInput"] > div {{
        background: transparent !important;
        border: none !important;
    }}
    div[data-testid="stAudioInput"] button {{
        background-color: rgba(255,255,255,0.3) !important;
        border: 1px solid rgba(100,100,100,0.3) !important;
        border-radius: 8px !important;
    }}

    /* 隐藏所有tooltip和弹窗元素（除了语言选择器） */
    div[data-baseweb="tooltip"]:not(.language-selector *) {{
        display: none !important;
    }}
    div[data-baseweb="modal"]:not(.language-selector *) {{
        display: none !important;
    }}
    .element-container:has(iframe) {{
        display: none !important;
    }}

    /* 为搜索框预留空间，避免被固定语言选择器覆盖 */
    div[data-testid="stVerticalBlock"] > div:first-child {{
        margin-top: 80px;
    }}
</style>
""", unsafe_allow_html=True)

# ---------- 语言选择器（固定在右上角） ----------
st.markdown('<div class="language-selector">', unsafe_allow_html=True)
language_col1, language_col2 = st.columns([1, 2])
with language_col1:
    st.markdown('<label>Select a Textbook::</label>', unsafe_allow_html=True)
with language_col2:
    new_language = st.selectbox(
        "Language",  # 提供非空标签，防止警告
        ["Chinese", "English"],
        index=0 if st.session_state.language == "Chinese" else 1,
        key="language_selector",
        label_visibility="collapsed"
    )
    if new_language != st.session_state.language:
        st.session_state.language = new_language
        levels_data = load_level_data(st.session_state.language)
        st.session_state.level = None
        st.session_state.path = []
        st.session_state.messages = [{"role": "system", "content": system_prompt}]
        st.session_state.auto_ref_pushed = False
        st.session_state.current_recommendations = None
        st.rerun()
st.markdown('</div>', unsafe_allow_html=True)

# ---------- 全局搜索框 ----------
with st.container():
    search_col1, search_col2 = st.columns([5, 1])
    with search_col1:
        search_input = st.text_input("", value=st.session_state.search_keyword, placeholder="Type to search...", key="search_box")
    with search_col2:
        if st.button("Clear", key="clear_search"):
            st.session_state.search_keyword = ""
            st.session_state.search_results = []
            st.rerun()
    
    # 如果搜索词变化，重新搜索
    if search_input != st.session_state.search_keyword:
        st.session_state.search_keyword = search_input
        if search_input.strip():
            st.session_state.search_results = global_search(search_input)
        else:
            st.session_state.search_results = []
        st.rerun()
    
    # 显示搜索结果
    if st.session_state.search_keyword and st.session_state.search_results:
        st.markdown(f"### Search Results for '{st.session_state.search_keyword}'")
        for res in st.session_state.search_results:
            # 构建面包屑路径
            path_str = " > ".join(res["path"])
            # 显示类型和内容
            content_preview = res["content"].replace("\n", " ")[:150]
            with st.container(border=True):
                cols = st.columns([1, 5])
                with cols[0]:
                    st.markdown(f"**{res['type']}**")
                with cols[1]:
                    st.markdown(f"{content_preview}")
                # 跳转按钮
                if st.button(f"Go to {path_str}", key=f"search_{res['level']}_{'_'.join(res['path'])}_{res['type']}_{res.get('index', '')}"):
                    st.session_state.level = res["level"]
                    st.session_state.path = res["path"]
                    st.session_state.search_keyword = ""
                    st.session_state.search_results = []
                    st.rerun()
        st.markdown("---")
    elif st.session_state.search_keyword:
        st.info("No results found.")

# ---------- 导航和卡片显示 ----------
st.title("TEXTBOOK ASSISTANT")

col1, col2, col3 = st.columns(3)
with col1:
    if st.button("Level 1", use_container_width=True):
        st.session_state.level = 1
        st.session_state.path = ["LEVEL_I"]
        st.session_state.auto_ref_pushed = False
        st.session_state.current_recommendations = None
        st.rerun()
with col2:
    if st.button("Level 2", use_container_width=True):
        st.session_state.level = 2
        st.session_state.path = ["LEVEL_II"]
        st.session_state.auto_ref_pushed = False
        st.session_state.current_recommendations = None
        st.rerun()
with col3:
    if st.button("Level 3", use_container_width=True):
        st.session_state.level = 3
        st.session_state.path = ["LEVEL_III"]
        st.session_state.auto_ref_pushed = False
        st.session_state.current_recommendations = None
        st.rerun()

if st.session_state.level:
    data = levels_data[f"Level {st.session_state.level}"]
    current_node = data
    for key in st.session_state.path:
        current_node = current_node.get(key, {})
        if not current_node:
            st.error("Path error. Please go back.")
            st.stop()

    bread = " > ".join(st.session_state.path)
    st.markdown(f"<div class='breadcrumb'>{bread}</div>", unsafe_allow_html=True)

    if len(st.session_state.path) > 1:
        st.markdown("<div class='back-button'>", unsafe_allow_html=True)
        if st.button("Back", key="back_button"):
            st.session_state.path.pop()
            st.session_state.auto_ref_pushed = False
            st.session_state.current_recommendations = None
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # ========== 修改后的 display_node 函数（支持卡片翻转） ==========
    def display_node(node):
        # 获取当前语言和另一语言
        current_lang = st.session_state.language
        other_lang = "English" if current_lang == "Chinese" else "Chinese"

        # 加载另一语言的数据
        other_levels_data = load_level_data(other_lang)
        other_node = other_levels_data[f"Level {st.session_state.level}"]
        # 根据当前路径定位另一语言的节点
        for key in st.session_state.path:
            other_node = other_node.get(key, {})
            if not other_node:
                other_node = None
                break

        if "name" in node:
            st.markdown(f"## {node['name']}")

        # 笔记部分（不翻转）
        if "notes" in node and node["notes"]:
            with st.container(border=True):
                st.markdown(node["notes"])

        # 例句部分（可翻转）
        if "examples" in node and node["examples"]:
            st.markdown("### Example Sentences")
            cols = st.columns(3)
            for idx, ex in enumerate(node["examples"]):
                with cols[idx % 3]:
                    # 唯一键
                    key = f"example_{idx}"
                    # 获取反面内容（如果另一语言节点存在且有对应索引）
                    other_ex = None
                    if other_node and "examples" in other_node and len(other_node["examples"]) > idx:
                        other_ex = other_node["examples"][idx]

                    # 当前翻转状态
                    flipped = st.session_state.get("flip_states", {}).get(key, False)

                    # 根据翻转状态显示内容
                    if flipped:
                        # 显示反面
                        display_content = other_ex if other_ex else "(Translation not available)"
                    else:
                        # 显示正面
                        display_content = ex

                    # 卡片按钮
                    if st.button(display_content, key=f"btn_{key}", use_container_width=True):
                        # 切换状态
                        if "flip_states" not in st.session_state:
                            st.session_state.flip_states = {}
                        st.session_state.flip_states[key] = not flipped
                        st.rerun()

        # 词汇部分（可翻转）
        if "vocabulary" in node and node["vocabulary"]:
            st.markdown("### Vocabulary")
            cols = st.columns(3)
            for idx, item in enumerate(node["vocabulary"]):
                with cols[idx % 3]:
                    # 正面：解析当前语言的词汇（可能带拼音/音标）
                    parts = item.rsplit(" ", 1)
                    word = parts[0]
                    pinyin = parts[1] if len(parts) > 1 else ""

                    # 反面：从另一语言获取对应词汇
                    other_item = None
                    if other_node and "vocabulary" in other_node and len(other_node["vocabulary"]) > idx:
                        other_item = other_node["vocabulary"][idx]
                    other_parts = other_item.rsplit(" ", 1) if other_item else ["", ""]
                    other_word = other_parts[0]
                    other_pron = other_parts[1] if len(other_parts) > 1 else ""

                    # 唯一键
                    key = f"vocab_{idx}"
                    flipped = st.session_state.get("flip_states", {}).get(key, False)

                    # 构建显示内容
                    if flipped:
                        # 反面：显示另一语言的词汇 + 发音（如果有）
                        display_content = other_word
                        if other_pron:
                            display_content += f"\n{other_pron}"
                    else:
                        # 正面：显示当前语言词汇 + 拼音
                        display_content = word
                        if pinyin:
                            display_content += f"\n{pinyin}"

                    # 卡片按钮
                    if st.button(display_content, key=f"btn_{key}", use_container_width=True):
                        if "flip_states" not in st.session_state:
                            st.session_state.flip_states = {}
                        st.session_state.flip_states[key] = not flipped
                        st.rerun()

        # 子目录导航（保持原有逻辑）
        if not any(key in node for key in ["notes", "examples", "vocabulary"]):
            sub_keys = [k for k in node.keys() if k not in ("name", "notes", "examples", "vocabulary")]
            if not sub_keys:
                st.info("This section has no content to display.")
            else:
                cols = st.columns(3)
                for i, key in enumerate(sub_keys):
                    with cols[i % 3]:
                        if isinstance(node[key], dict) and "name" in node[key]:
                            label = node[key]["name"]
                        else:
                            label = key
                        if st.button(label, key=f"dir_{key}", use_container_width=True):
                            st.session_state.path.append(key)
                            st.session_state.auto_ref_pushed = False
                            st.session_state.current_recommendations = None
                            # 路径改变时清空翻转状态（可选，提升体验）
                            st.session_state.flip_states = {}
                            st.rerun()

    display_node(current_node)

    # 显示推荐资源
    if st.session_state.current_recommendations:
        st.markdown("---")
        with st.container():
            st.markdown(st.session_state.current_recommendations, unsafe_allow_html=True)

    if not st.session_state.auto_ref_pushed:
        auto_push_reference(st.session_state.level, bread)

# ---------- 悬浮聊天窗（固定在右下角） ----------
# 强制打开聊天面板（用户要求）
st.session_state.chat_open = True

if st.session_state.chat_open:
    st.markdown('<div class="chat-panel">', unsafe_allow_html=True)

    # 初始化音频上下文（绕过浏览器自动播放限制）
    st.markdown('''
    <script>
        if (!window.audioContextInitialized) {
            window.audioContextInitialized = true;
            var silentAudio = document.createElement('audio');
            silentAudio.src = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';
            silentAudio.play().catch(function() {});
        }
    </script>
    ''', unsafe_allow_html=True)

    # 消息区域
    st.markdown('<div class="chat-messages-area" id="chat-messages">', unsafe_allow_html=True)
    for msg in st.session_state.messages:
        if msg["role"] == "user":
            st.markdown(f'<div class="chat-message"><strong>You:</strong> {msg["content"]}</div>', unsafe_allow_html=True)
        elif msg["role"] == "assistant":
            st.markdown(f'<div class="chat-message"><strong>AI:</strong> {msg["content"]}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # 自动滚动到底部
    st.markdown('''
    <script>
        setTimeout(function() {
            var chatArea = document.getElementById('chat-messages');
            if (chatArea) {
                chatArea.scrollTop = chatArea.scrollHeight;
            }
        }, 100);
    </script>
    ''', unsafe_allow_html=True)

    # 播放TTS音频
    if st.session_state.pending_tts:
        audio_bytes, fmt = st.session_state.pending_tts
        st.audio(audio_bytes, format=fmt, autoplay=True)
        st.session_state.pending_tts = None

    # 输入区域：三列布局（Clear按钮 + 语音按钮 + 文本输入）
    col_clear, col_voice, col_text = st.columns([1, 1, 6])

    with col_clear:
        # Clear 按钮
        if st.button("Clear", key="clear_chat", use_container_width=True):
            st.session_state.messages = [m for m in st.session_state.messages if m["role"] == "system"]
            st.session_state.pending_tts = None
            st.session_state.last_audio_id = None
            st.session_state.conversation_summary = ""
            st.session_state.conv_history = []
            st.session_state.user_msg_count = 0
            st.session_state.auto_ref_pushed = False
            if os.path.exists("conversation_summary.txt"):
                os.remove("conversation_summary.txt")
            st.rerun()

    with col_voice:
        # 语音输入按钮
        audio_input = st.audio_input("🎤", key="voice_input", label_visibility="collapsed")
        if audio_input is not None:
            audio_id = f"{audio_input.name}_{audio_input.size}"
            if audio_id != st.session_state.last_audio_id:
                st.session_state.last_audio_id = audio_id
                audio_bytes = audio_input.read()
                if audio_bytes:
                    with st.spinner("Transcribing..."):
                        transcript = transcribe_audio(audio_bytes)
                    if transcript and not transcript.startswith("[转录失败"):
                        with st.spinner("Thinking..."):
                            get_ai_reply(transcript)
                        st.rerun()

    with col_text:
        # 文本输入框
        if prompt := st.chat_input("Type a message...", key="text_input"):
            with st.spinner("Thinking..."):
                get_ai_reply(prompt)
            st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)
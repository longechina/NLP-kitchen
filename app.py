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
    page_title="Chinese Learning Assistant",
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
        # 指向通过 LFS 上传的模型文件
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
        return f"[转录失败: {e}]"

# ---------- 判断文本是否含中文 ----------
def has_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fff]', text))

# ---------- 文字转语音 ----------
def text_to_speech(text):
    kokoro = load_kokoro()
    if kokoro is not None:
        try:
            import soundfile as sf
            # 使用保留的中文音色 zf_001 和英文音色 af_sol
            voice = "zf_001" if has_chinese(text) else "af_sol"
            samples, sample_rate = kokoro.create(text, voice=voice, speed=1.0)
            buf = io.BytesIO()
            sf.write(buf, samples, sample_rate, format="WAV")
            buf.seek(0)
            return buf.read(), "audio/wav"
        except Exception:
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
    except Exception:
        return None, None

# ---------- 构建系统提示 ----------
def build_system_prompt(levels):
    prompt = """You are a language learning assistant helping students learn Languages. 
You have access to learning materials across 3 levels covering grammar, vocabulary, and conversation.
Keep your answers concise, clear, and helpful. Focus on what the user is currently studying."""
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

# ========== 新增：对话总结相关状态 ==========
if "conversation_summary" not in st.session_state:
    st.session_state.conversation_summary = ""          # 存储总结文本
if "conv_history" not in st.session_state:
    st.session_state.conv_history = []                  # 存储未总结的对话（用于生成总结）
if "user_msg_count" not in st.session_state:
    st.session_state.user_msg_count = 0                 # 用户消息计数器（用于触发总结）

# ========== 自动参考相关状态 ==========
if "auto_ref_pushed" not in st.session_state:
    st.session_state.auto_ref_pushed = False   # 标记当前水平是否已自动推送
if "current_recommendations" not in st.session_state:
    st.session_state.current_recommendations = None  # 存储当前页面的推荐资源

# ---------- 获取当前页面全部内容（完整喂给 AI） ----------
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

# ========== 自动生成参考消息（启用联网搜索，输出 Markdown） ==========
def auto_generate_reference(level, full_page_content, path_string):
    """
    根据当前水平、页面全部内容，让 AI 使用联网搜索生成具体的权威链接。
    输出为结构化 Markdown（标题、列表、可点击链接）。
    增加重试机制以应对速率限制（429）。
    """
    # 提取主题（从页面内容中尝试获取 name，如果没有则用路径最后一部分）
    topic = ""
    if "Section:" in full_page_content:
        # 提取 Section 行后的内容
        import re
        match = re.search(r"Section: (.+)", full_page_content)
        if match:
            topic = match.group(1)
    if not topic:
        parts = path_string.split(" > ")
        topic = parts[-1] if parts else "general"
    
    # 简化内容，只提取关键信息
    notes = ""
    if "Notes:" in full_page_content:
        notes_match = re.search(r"Notes: (.+?)(?:Example|Vocabulary|$)", full_page_content, re.DOTALL)
        if notes_match:
            notes = notes_match.group(1).strip()[:200]  # 限制长度
    
    # 简化的prompt，避免发送过多内容
    prompt = f"""You are a Chinese learning assistant. The user is at Level {level} studying: "{topic}".

Topic summary: {notes if notes else "Basic Chinese learning topic"}

Your task:
- Search the web to find 3-4 high-quality learning resources(must valid, send links before comfire if links is valid) (articles, videos, exercises)
- Provide a brief heading and list each res-ource with a short description and clickable link
- Keep it concise

Example format:
【Recommended Resources】

- BBC Chinese: Lesson on greetings. [View](https://www.bbc.co.uk/example)
- YouTube: Beginner video. [Watch](https://youtube.com/watch?v=xxxxx)

Now generate for: {topic}
"""
    
    max_retries = 2
    retry_delay = 3
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="groq/compound",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=500,  # 减少token数
            )
            ref_text = response.choices[0].message.content.strip()
            return ref_text
        except Exception as e:
            error_str = str(e).lower()
            if "413" in error_str or "too large" in error_str:
                # 如果内容太大，返回简化版本
                return f"**Resources for {topic}**\n\nPlease use the AI chat to ask for specific learning resources."
            if "rate" in error_str or "429" in error_str:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
            # 其他错误不显示，避免影响用户体验
            return None
    return None

# ========== 自动推送参考消息到页面显示 ==========
def auto_push_reference(level, path_string):
    """
    自动在页面上生成并显示参考消息。
    仅在首次进入某个水平时调用一次（由 auto_ref_pushed 控制）。
    """
    if st.session_state.auto_ref_pushed:
        return
    
    full_page_content = get_current_page_full_content()
    if full_page_content:
        # 静默生成，不显示加载提示
        ref_msg = auto_generate_reference(level, full_page_content, path_string)
        # 只有成功生成才保存
        if ref_msg:
            st.session_state.current_recommendations = ref_msg
        # 设置标记，避免重复推送
        st.session_state.auto_ref_pushed = True

# ========== AI 回复函数 ==========
def get_ai_reply(user_input):
    """处理用户输入，获取AI回复，并触发TTS。"""
    st.session_state.messages.append({"role": "user", "content": user_input})
    st.session_state.user_msg_count += 1
    st.session_state.conv_history.append({"role": "user", "content": user_input})
    
    full_page = get_current_page_full_content()
    context_msgs = st.session_state.messages.copy()
    if full_page and st.session_state.user_msg_count == 1:
        context_msgs.insert(1, {"role": "system", "content": full_page})
    
    if st.session_state.conversation_summary:
        summary_msg = {
            "role": "system",
            "content": f"[Previous conversation summary]\n{st.session_state.conversation_summary}"
        }
        context_msgs.insert(1, summary_msg)
    
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=context_msgs,
            temperature=0.7,
            max_tokens=512,
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        reply = f"[Error: {e}]"
    
    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.session_state.conv_history.append({"role": "assistant", "content": reply})
    
    # TTS生成
    audio_bytes, fmt = text_to_speech(reply)
    if audio_bytes:
        st.session_state.pending_tts = (audio_bytes, fmt)
    
    # 每隔5轮用户消息生成总结
    if st.session_state.user_msg_count % 5 == 0 and st.session_state.user_msg_count > 0:
        generate_and_save_summary()

# ========== 生成并保存对话总结 ==========
def generate_and_save_summary():
    """
    使用 AI 生成对话总结，并保存到文件和 session_state。
    """
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
            model="llama-3.3-70b-versatile",
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
    /* 导入Manrope字体 */
    @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@200;300;400;500;600;700;800&display=swap');
    
    /* 全局样式 */
    .stApp {{
        {bg_css}
        background-size: cover;
        background-position: center;
        background-attachment: scroll;
        font-family: 'Manrope', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }}

    * {{
        font-family: 'Manrope', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
    }}

    /* ↓ 新增代码放在这里 ↓ */
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
    
    /* 语言选择下拉框样式 */
    .language-selector div[data-baseweb="select"] {{
        background-color: white !important;
    }}
    .language-selector div[data-baseweb="select"] > div {{
        background-color: white !important;
        color: #000000 !important;
        border: 1px solid #ccc !important;
        font-family: 'Manrope', sans-serif !important;
        font-size: 16px !important;
        font-weight: 600 !important;
    }}
    /* 下拉菜单本身 */
    .language-selector div[data-baseweb="popover"] {{
        z-index: 1001 !important;
        display: block !important;
    }}
    div[role="listbox"] {{
        background-color: white !important;
        color: #000000 !important;
        display: block !important;
    }}
    div[role="option"] {{
        color: #000000 !important;
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

    /* 关键修复：让文字真的变大 */
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
    
    /* 推荐资源区域样式 */
    hr {{
        margin: 30px 0;
        border: none;
        border-top: 2px solid rgba(100,100,100,0.2);
    }}
    
    /* Markdown链接样式 */
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
        border-radius: 25px !important;
        border: 1px solid rgba(0,0,0,0.3) !important;
        background-color: rgba(0,0,0,0.9) !important;
        font-family: 'Manrope', sans-serif !important;
        font-size: 16px !important;
        font-weight: 400 !important;
        color: #ffffff !important;  /* 字体白色 */

    }}
    .stChatInput > div {{
        background: transparent !important;
    }}
    .stChatInput button {{
        background: transparent !important;
        border: none !important;
    }}

    /* placeholder 样式跟输入框一致 */
    .stChatInput textarea::placeholder {{
        color: #bbb !important;             /* 文字颜色，黑色或你想要的 */
        font-family: 'Manrope', sans-serif !important;
        font-size: 16px !important;
        font-weight: 400 !important;
        background: transparent !important;    /* 背景透明，与输入框一致 */
        opacity: 1 !important;                 /* 保持完全可见 */
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
    button[data-testid="baseButton-secondary"][key="chat_toggle_btn"]:hover,
    .chat-float-container .stButton button:hover {{
        background-color: rgba(255,255,255,0.5) !important;
        border-color: rgba(100,100,100,0.4) !important;
    }}

    /* 完全隐藏所有音频播放器 */
    .stAudio {{ display: none !important; }}

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


</style>
""", unsafe_allow_html=True)

# ---------- 语言选择器（固定在右上角） ----------
st.markdown('<div class="language-selector">', unsafe_allow_html=True)
language_col1, language_col2 = st.columns([1, 2])
with language_col1:
    st.markdown('<label>Language:</label>', unsafe_allow_html=True)
with language_col2:
    new_language = st.selectbox(
        "",
        ["Chinese", "English"],
        index=0 if st.session_state.language == "Chinese" else 1,
        key="language_selector",
        label_visibility="collapsed"
    )
    
    # 如果语言改变，重新加载数据并重置状态
    if new_language != st.session_state.language:
        st.session_state.language = new_language
        levels_data = load_level_data(st.session_state.language)
        st.session_state.level = None
        st.session_state.path = []
        st.session_state.messages = [{"role": "system", "content": system_prompt}]
        st.session_state.auto_ref_pushed = False
        st.session_state.current_recommendations = None  # 清除推荐
        st.rerun()
st.markdown('</div>', unsafe_allow_html=True)

# ---------- 导航和卡片显示 ----------
st.title("CHINESE LEARNING ASSISTANT")

col1, col2, col3 = st.columns(3)
with col1:
    if st.button("Level 1", use_container_width=True):
        st.session_state.level = 1
        st.session_state.path = ["LEVEL_I"]
        st.session_state.auto_ref_pushed = False   # 重置标记，以便重新推送
        st.session_state.current_recommendations = None  # 清除旧的推荐
        st.rerun()
with col2:
    if st.button("Level 2", use_container_width=True):
        st.session_state.level = 2
        st.session_state.path = ["LEVEL_II"]
        st.session_state.auto_ref_pushed = False   # 重置标记
        st.session_state.current_recommendations = None  # 清除旧的推荐
        st.rerun()
with col3:
    if st.button("Level 3", use_container_width=True):
        st.session_state.level = 3
        st.session_state.path = ["LEVEL_III"]
        st.session_state.auto_ref_pushed = False   # 重置标记
        st.session_state.current_recommendations = None  # 清除旧的推荐
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
            st.session_state.auto_ref_pushed = False  # 重置以便重新生成
            st.session_state.current_recommendations = None  # 清除旧的推荐
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    def display_node(node):
        if "name" in node:
            st.markdown(f"## {node['name']}")
        if "notes" in node and node["notes"]:
            with st.container(border=True):
                st.markdown(node["notes"])
        if "examples" in node and node["examples"]:
            st.markdown("### Example Sentences")
            cols = st.columns(3)
            for idx, ex in enumerate(node["examples"]):
                with cols[idx % 3]:
                    with st.container(border=True):
                        st.markdown(f"<div style='font-size:32px;'>{ex}</div>", unsafe_allow_html=True)
        if "vocabulary" in node and node["vocabulary"]:
            st.markdown("### Vocabulary")
            cols = st.columns(3)
            for idx, item in enumerate(node["vocabulary"]):
                with cols[idx % 3]:
                    parts = item.rsplit(" ", 1)
                    word = parts[0]
                    pinyin = parts[1] if len(parts) > 1 else ""
                    with st.container(border=True):
                        st.markdown(f"## {word}")
                        if pinyin:
                            st.markdown(f"<div>{pinyin}</div>", unsafe_allow_html=True)
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
                            st.session_state.auto_ref_pushed = False  # 重置以便重新生成
                            st.session_state.current_recommendations = None  # 清除旧的推荐
                            st.rerun()

    display_node(current_node)
    
    # ========== 显示推荐资源（在页面内容下方） ==========
    if st.session_state.current_recommendations:
        st.markdown("---")  # 分隔线
        with st.container():
            st.markdown(st.session_state.current_recommendations, unsafe_allow_html=True)
    
    # ========== 自动推送参考消息（在页面显示后触发） ==========
    # 如果还没有为当前水平推送过，则生成并推送
    if not st.session_state.auto_ref_pushed:
        auto_push_reference(st.session_state.level, bread)

# ---------- 悬浮聊天窗 ----------

st.session_state.chat_open = True

if st.session_state.chat_open:
    st.markdown('<div class="chat-panel">', unsafe_allow_html=True)
    
    # 初始化音频上下文（绕过浏览器自动播放限制）
    st.markdown('''
    <script>
        if (!window.audioContextInitialized) {
            window.audioContextInitialized = true;
            // 创建静默音频上下文以启用自动播放
            var silentAudio = document.createElement('audio');
            silentAudio.src = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';
            silentAudio.play().catch(function() {});
        }
    </script>
    ''', unsafe_allow_html=True)

    # Clear 按钮
    st.markdown('<div class="clear-button-container" style="display:flex;justify-content:flex-end;padding:8px 16px 0;">', unsafe_allow_html=True)
    if st.button("Clear", key="clear_chat"):
        st.session_state.messages = [m for m in st.session_state.messages if m["role"] == "system"]
        st.session_state.pending_tts = None
        st.session_state.last_audio_id = None
        # 清理总结相关状态
        st.session_state.conversation_summary = ""
        st.session_state.conv_history = []
        st.session_state.user_msg_count = 0
        st.session_state.auto_ref_pushed = False   # 重置自动推送标记
        # 可选：删除总结文件
        if os.path.exists("conversation_summary.txt"):
            os.remove("conversation_summary.txt")
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

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

    # ========== 修改：自动播放使用 st.audio 并隐藏 ==========
    if st.session_state.pending_tts:
        audio_bytes, fmt = st.session_state.pending_tts
        st.audio(audio_bytes, format=fmt, autoplay=True)
        st.session_state.pending_tts = None

    # 输入区域
    st.markdown('<div class="chat-input-area">', unsafe_allow_html=True)
    
    # 创建两列布局：语音按钮在左，文字输入在右
    col_voice, col_text = st.columns([1, 6])
    
    with col_voice:
        # 语音输入 - 小黑框
        audio_input = st.audio_input("🎤", key="voice_input", label_visibility="collapsed")
        if audio_input is not None:
            audio_id = f"{audio_input.name}_{audio_input.size}"
            if audio_id != st.session_state.last_audio_id:
                st.session_state.last_audio_id = audio_id
                with st.spinner("Transcribing..."):
                    transcript = transcribe_audio(audio_input.read())
                if transcript and not transcript.startswith("[转录失败"):
                    with st.spinner("Thinking..."):
                        get_ai_reply(transcript)
                    st.rerun()
    
    with col_text:
        # 文字输入 - 椭圆形大字体
        if prompt := st.chat_input("Type a message...", key="text_input"):
            with st.spinner("Thinking..."):
                get_ai_reply(prompt)
            st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

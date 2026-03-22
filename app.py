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
st.set_page_config(layout="wide", page_title="Chinese Learning Assistant")

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
    prompt = """You are a Chinese learning assistant helping students learn Chinese. 
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
    
    prompt = f"""You are a Chinese learning assistant. The user is at Level {level} and studying the topic: "{topic}".

The complete content of the current page is provided below. Use it to understand exactly what the user is learning.

{full_page_content}

Your task:
- Search information on web to find specific, authoritative online resources(include article, Q&A, meida, picture and videos) that about current topic, content and level.(The source must be high quality and form main stream)
- Based on the search results, provide a structured recommendation in clear structure.
- Include a brief heading (Recommended Resources), then list each resource with a short description and a clickable web links.
- Keep the answer concise but informative.

Example output:
【Recommended Resources】

- BBC: A lesson on greetings with audio and quizzes. [View](https://www.bbc.co.uk/example)
- YouTube: A beginner video on introducing yourself. [Watch](https://www.youtube.com/watch?v=xxxxx)

Now generate for the topic: {topic}
"""
    
    max_retries = 3
    retry_delay = 5  # 秒
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="groq/compound",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=800,
            )
            ref_text = response.choices[0].message.content.strip()
            return ref_text
        except Exception as e:
            error_str = str(e).lower()
            if "rate" in error_str or "429" in error_str:
                if attempt < max_retries - 1:
                    st.warning(f"Speed limit reached, retrying in {retry_delay} seconds... (attempt {attempt+1}/{max_retries})")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    continue
                else:
                    return f"Unable to generate recommendations: Exceeded rate limit after {max_retries} retries. Please try again later."
            else:
                return f"Unable to generate recommendations: {e}"
    return "Unable to generate recommendations after multiple retries."

# ========== 自动推送参考消息到聊天窗口 ==========
def auto_push_reference(level, path_string):
    """
    自动在 AI 面板中生成并推送参考消息。
    仅在首次进入某个水平时调用一次（由 auto_ref_pushed 控制）。
    """
    if st.session_state.auto_ref_pushed:
        return
    
    full_page_content = get_current_page_full_content()
    if full_page_content:
        with st.spinner("Generating recommended resources..."):
            ref_msg = auto_generate_reference(level, full_page_content, path_string)
        # 将参考消息以 assistant 角色推入对话历史
        st.session_state.messages.append({"role": "assistant", "content": ref_msg})
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
    /* 全局样式 */
    .stApp {{
        {bg_css}
        background-size: cover;
        background-position: center;
        background-attachment: fixed;
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
        font-weight: 600;
        color: #333;
        margin: 0;
    }}

    /* 主标题 */
    h1 {{
        text-align: center;
        color: #000000;
        font-size: 48px;
        font-weight: 700;
        text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        margin-bottom: 30px;
    }}

    /* Level按钮 */
    .stButton button {{
        background-color: rgba(255,255,255,0.2) !important;
        color: #000000 !important;
        font-size: 20px !important;
        font-weight: 600 !important;
        border: 2px solid #000000 !important;
        border-radius: 12px !important;
        padding: 20px !important;
        transition: all 0.3s ease !important;
        box-shadow: 0 4px 8px rgba(0,0,0,0.2) !important;
    }}
    .stButton button:hover {{
        background-color: rgba(255,255,255,0.4) !important;
        transform: translateY(-2px);
        box-shadow: 0 6px 12px rgba(0,0,0,0.3) !important;
    }}

    /* 面包屑导航 */
    .breadcrumb {{
        background-color: rgba(255,255,255,0.15);
        padding: 12px 20px;
        border-radius: 8px;
        margin-bottom: 20px;
        font-size: 18px;
        color: #000000;
        font-weight: 500;
        border: 1px solid rgba(0,0,0,0.1);
    }}

    /* Back按钮 */
    .back-button {{
        margin-bottom: 20px;
    }}
    button[key="back_button"] {{
        background-color: rgba(255,255,255,0.2) !important;
        color: #000000 !important;
        font-size: 16px !important;
        font-weight: 600 !important;
        border: 2px solid #000000 !important;
        border-radius: 8px !important;
        padding: 10px 24px !important;
    }}

    /* 容器样式 */
    div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlock"] {{
        background-color: rgba(255,255,255,0.15);
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 15px;
        border: 1px solid rgba(0,0,0,0.1);
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }}

    /* 标题 */
    h2 {{
        color: #000000;
        font-weight: 600;
        margin-bottom: 15px;
    }}
    h3 {{
        color: #000000;
        font-weight: 600;
        margin-top: 20px;
        margin-bottom: 10px;
    }}

    /* 悬浮AI按钮 */
    .chat-float-container {{
        position: fixed;
        bottom: 30px;
        right: 30px;
        z-index: 999;
    }}
    button[data-testid="baseButton-secondary"][key="chat_toggle_btn"],
    .chat-float-container .stButton button {{
        width: 70px !important;
        height: 70px !important;
        border-radius: 50% !important;
        background-color: rgba(255,255,255,0.2) !important;
        border: 2px solid #000000 !important;
        font-size: 28px !important;
        font-weight: 700 !important;
        color: #000000 !important;
        box-shadow: 0 6px 16px rgba(0,0,0,0.3) !important;
        transition: all 0.3s ease !important;
        padding: 0 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
    }}
    button[data-testid="baseButton-secondary"][key="chat_toggle_btn"]:hover,
    .chat-float-container .stButton button:hover {{
        background-color: rgba(255,255,255,0.4) !important;
        transform: scale(1.1);
    }}

    /* 聊天面板 */
    .chat-panel {{
        position: fixed;
        bottom: 120px;
        right: 30px;
        width: 450px;
        height: 600px;
        background-color: rgba(255,255,255,0.98);
        border-radius: 16px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        display: flex;
        flex-direction: column;
        z-index: 998;
        border: 2px solid #000000;
    }}

    /* 聊天消息区域 */
    .chat-messages-area {{
        flex: 1;
        overflow-y: auto;
        padding: 20px;
        border-bottom: 2px solid rgba(0,0,0,0.1);
    }}
    .chat-message {{
        margin-bottom: 15px;
        padding: 12px;
        background-color: rgba(240,240,240,0.6);
        border-radius: 8px;
        line-height: 1.6;
        color: #000000;
    }}
    .chat-message strong {{
        color: #000000;
    }}

    /* 输入区域 */
    .chat-input-area {{
        padding: 15px;
        background-color: rgba(250,250,250,0.95);
        border-radius: 0 0 14px 14px;
    }}
    .stChatInput {{
        border-radius: 25px !important;
        border: 2px solid #000000 !important;
        background-color: rgba(255,255,255,0.9) !important;
        font-size: 16px !important;
    }}

    /* Clear按钮 */
    button[key="clear_chat"] {{
        background-color: rgba(255,255,255,0.2) !important;
        border: 2px solid #000000 !important;
        border-radius: 8px !important;
        padding: 6px 16px !important;
        font-size: 14px !important;
        font-weight: 500 !important;
        color: #000000 !important;
        box-shadow: none !important;
    }}
    button[data-testid="baseButton-secondary"][key="chat_toggle_btn"]:hover,
    .chat-float-container .stButton button:hover {{
        background-color: rgba(255,255,255,0.3) !important;
        border-color: #000000 !important;
    }}

    /* 完全隐藏所有音频播放器 */
    .stAudio {{ display: none !important; }}

    div[data-testid="stAudioInput"] {{ margin: 4px 0 !important; }}
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
        st.rerun()
with col2:
    if st.button("Level 2", use_container_width=True):
        st.session_state.level = 2
        st.session_state.path = ["LEVEL_II"]
        st.session_state.auto_ref_pushed = False   # 重置标记
        st.rerun()
with col3:
    if st.button("Level 3", use_container_width=True):
        st.session_state.level = 3
        st.session_state.path = ["LEVEL_III"]
        st.session_state.auto_ref_pushed = False   # 重置标记
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
                        st.markdown(f"### {word}")
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
                            st.rerun()

    display_node(current_node)
    
    # ========== 自动推送参考消息（在页面显示后触发） ==========
    # 如果还没有为当前水平推送过，则生成并推送
    if not st.session_state.auto_ref_pushed:
        auto_push_reference(st.session_state.level, bread)

# ---------- 悬浮聊天窗 ----------
with st.container():
    st.markdown('<div class="chat-float-container">', unsafe_allow_html=True)

    if st.button("AI", key="chat_toggle_btn"):
        st.session_state.chat_open = not st.session_state.chat_open
        st.rerun()

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

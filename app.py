import json
import base64
import io
import re
import os
import time
import streamlit as st
import groq
# ==================== 关键修改 ====================
# 原：from streamlit_mic_recorder import mic_recorder
from audio_recorder_streamlit import audio_recorder as mic_recorder   # ← 已切换组件
# =================================================

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

st.set_page_config(
    layout="wide",
    page_title="Chinese Learning Assistant",
    initial_sidebar_state="collapsed",
    menu_items=None
)

# ---------- 初始化语言状态 ----------
if "language" not in st.session_state:
    st.session_state.language = "Chinese"

# ---------- 加载所有 Level 数据 ----------
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

# ---------- 语音转文字 ----------
def transcribe_audio(audio_bytes):
    try:
        transcription = client.audio.transcriptions.create(
            file=("audio.wav", audio_bytes, "audio/wav"),
            model="whisper-large-v3",
        )
        return transcription.text
    except Exception as e:
        st.error(f"Speech recognition failed: {e}")
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
if "pending_tts" not in st.session_state:
    st.session_state.pending_tts = None
if "voice_mode" not in st.session_state:
    st.session_state.voice_mode = False
if "last_audio_id" not in st.session_state:
    st.session_state.last_audio_id = None

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
        parts.append("Example sentences:\n" + "\n".join(f" - {e}" for e in node["examples"]))
    if "vocabulary" in node and node["vocabulary"]:
        parts.append("Vocabulary:\n" + "\n".join(f" - {v}" for v in node["vocabulary"]))
    return "\n".join(parts)

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
    prompt = f"""You are a Chinese learning assistant. The user is at Level {level} studying: "{topic}".
Topic summary: {notes if notes else "Basic Chinese learning topic"}
Your task:
- Search the web to find 3-4 high-quality learning resources (must be valid, provide links) (articles, videos, exercises)
- Provide a brief heading and list each resource with a short description and clickable link
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

# ========== AI 回复函数 ==========
def get_ai_reply(user_input):
    st.session_state.messages.append({"role": "user", "content": user_input})
    st.session_state.user_msg_count += 1
    st.session_state.conv_history.append({"role": "user", "content": user_input})
    full_page = get_current_page_full_content()
    context_msgs = st.session_state.messages.copy()
    if st.session_state.language:
        lang_msg = {"role": "system", "content": f"The user is currently learning {st.session_state.language}."}
        context_msgs.insert(1, lang_msg)
    if full_page:
        insert_idx = 2 if st.session_state.language else 1
        context_msgs.insert(insert_idx, {"role": "system", "content": full_page})
    if st.session_state.conversation_summary:
        summary_msg = {
            "role": "system",
            "content": f"[Previous conversation summary]\n{st.session_state.conversation_summary}"
        }
        base = 1
        if st.session_state.language:
            base += 1
        if full_page:
            base += 1
        context_msgs.insert(base, summary_msg)
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
    try:
        audio_bytes, fmt = text_to_speech(reply)
        if audio_bytes:
            st.session_state.pending_tts = (audio_bytes, fmt)
    except Exception as e:
        print(f"TTS error: {e}")
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
    @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@200;300;400;500;600;700;800&display=swap');
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
    .stApp {{
        background-color: rgba(255, 255, 255, 0.5) !important;
        background-blend-mode: overlay !important;
    }}
    /* 隐藏Streamlit顶部黑框和工具栏 */
    header[data-testid="stHeader"] {{ display: none !important; }}
    .stDeployButton {{ display: none !important; }}
    section[data-testid="stSidebar"] {{ display: none !important; }}
    #MainMenu {{ display: none !important; }}
    footer {{ display: none !important; }}
    div[role="dialog"] {{ display: none !important; }}
    div[data-testid="stModal"] {{ display: none !important; }}
    .stAlert {{ display: none !important; }}
    div[data-baseweb="drawer"] {{ display: none !important; }}
    div[data-baseweb="modal"] {{ display: none !important; }}
    div[class*="overlay"] {{ display: none !important; }}
    div[class*="backdrop"] {{ display: none !important; }}
    div[class*="Overlay"] {{ display: none !important; }}
    div[style*="position: fixed"][style*="inset: 0"] {{ pointer-events: none !important; background: transparent !important; }}
    /* 聊天相关样式保持不变 */
    div[data-testid="stChatInput"] textarea,
    div[data-testid="stChatInput"] > div {{ background-color: transparent !important; background: transparent !important; }}
    div[data-testid="stChatMessage"] {{ background-color: rgba(240, 240, 240, 0.4) !important; backdrop-filter: blur(5px); }}
    .stChatInput {{ border-radius: 15px !important; border: 1px solid rgba(0,0,0,0.3) !important; background-color: rgba(18,19,28,0.9) !important; }}
    .stAudio {{ display: none !important; }}
</style>
""", unsafe_allow_html=True)

# ---------- 语言选择器 ----------
st.markdown('<div class="language-selector">', unsafe_allow_html=True)
language_col1, language_col2 = st.columns([1, 2])
with language_col1:
    st.markdown('<label>Language:</label>', unsafe_allow_html=True)
with language_col2:
    new_language = st.selectbox(
        "Language",
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

# ---------- 导航和卡片显示 ----------
st.title("Chinese Learning Assistant")
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
                        label = node[key]["name"] if isinstance(node[key], dict) and "name" in node[key] else key
                        if st.button(label, key=f"dir_{key}", use_container_width=True):
                            st.session_state.path.append(key)
                            st.session_state.auto_ref_pushed = False
                            st.session_state.current_recommendations = None
                            st.rerun()
    display_node(current_node)
    if st.session_state.current_recommendations:
        st.markdown("---")
        with st.container():
            st.markdown(st.session_state.current_recommendations, unsafe_allow_html=True)
    if not st.session_state.auto_ref_pushed:
        auto_push_reference(st.session_state.level, bread)

# ---------- 悬浮聊天窗 ----------
st.session_state.chat_open = True
if st.session_state.chat_open:
    st.markdown('<div class="chat-panel">', unsafe_allow_html=True)
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
    st.markdown('<div class="chat-messages-area" id="chat-messages">', unsafe_allow_html=True)
    for msg in st.session_state.messages:
        if msg["role"] == "user":
            st.markdown(f'<div class="chat-message"><strong>You:</strong> {msg["content"]}</div>', unsafe_allow_html=True)
        elif msg["role"] == "assistant":
            st.markdown(f'<div class="chat-message"><strong>AI:</strong> {msg["content"]}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('''
    <script>
        setTimeout(function() {
            var chatArea = document.getElementById('chat-messages');
            if (chatArea) chatArea.scrollTop = chatArea.scrollHeight;
        }, 100);
    </script>
    ''', unsafe_allow_html=True)

    if st.session_state.pending_tts:
        audio_bytes, fmt = st.session_state.pending_tts
        st.audio(audio_bytes, format=fmt, autoplay=True)
        st.session_state.pending_tts = None

    # 输入区域：三列布局（Clear / Voice Mode / Text）
    col_clear, col_voice, col_text = st.columns([1, 1, 6])
    with col_clear:
        if st.button("Clear", key="clear_chat", use_container_width=True):
            st.session_state.messages = [m for m in st.session_state.messages if m["role"] == "system"]
            st.session_state.pending_tts = None
            st.session_state.conversation_summary = ""
            st.session_state.conv_history = []
            st.session_state.user_msg_count = 0
            st.session_state.auto_ref_pushed = False
            if os.path.exists("conversation_summary.txt"):
                os.remove("conversation_summary.txt")
            st.rerun()

    with col_voice:
        # 语音模式开关
        button_label = "Voice Mode" if not st.session_state.voice_mode else "Exit Voice Mode"
        if st.button(button_label, key="voice_toggle", use_container_width=True):
            st.session_state.voice_mode = not st.session_state.voice_mode
            st.session_state.last_audio_id = None
            st.rerun()

        if st.session_state.voice_mode:
            # ============ 真正的持续监听模式（说话即录，静默3秒自动停止）============
            audio_bytes = mic_recorder(
                pause_threshold=3.0,      # 静默3秒后自动停止
                energy_threshold=0.02,    # 音量灵敏度（可调小更灵敏）
                sample_rate=16000,        # Whisper最佳采样率
                text="Speak now..."       # 提示文字
            )

            if audio_bytes:               # ← 新组件直接返回 bytes 或 None
                audio_id = f"{len(audio_bytes)}"
                if audio_id != st.session_state.last_audio_id:
                    st.session_state.last_audio_id = audio_id
                    with st.spinner("Transcribing..."):
                        transcript = transcribe_audio(audio_bytes)
                    if transcript and not transcript.startswith("[转录失败"):
                        with st.spinner("Thinking..."):
                            get_ai_reply(transcript)
                        st.rerun()

            st.caption("Voice mode active: speak, I will listen automatically.")

    with col_text:
        if prompt := st.chat_input("Type a message...", key="text_input"):
            with st.spinner("Thinking..."):
                get_ai_reply(prompt)
            st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)
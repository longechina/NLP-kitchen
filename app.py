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
    st.session_state.voice_mode = False          # 语音模式开关
if "last_audio_data" not in st.session_state:
    st.session_state.last_audio_data = None      # 用于去重

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
        parts.append("Example sentences:\n" + "\n".join(f"  - {e}" for e in node["examples"]))
    if "vocabulary" in node and node["vocabulary"]:
        parts.append("Vocabulary:\n" + "\n".join(f"  - {v}" for v in node["vocabulary"]))
    return "\n".join(parts)

# ========== 自动生成参考消息 ==========
def auto_generate_reference(level, full_page_content, path_string):
    # ... 函数内容与之前相同，此处省略以节省篇幅 ...
    # （请从之前的完整代码中复制该函数）
    pass

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

    # 插入语言信息
    if st.session_state.language:
        lang_msg = {"role": "system", "content": f"The user is currently learning {st.session_state.language}."}
        context_msgs.insert(1, lang_msg)

    if full_page:
        insert_idx = 2 if st.session_state.language else 1
        context_msgs.insert(insert_idx, {"role": "system", "content": full_page})

    if st.session_state.conversation_summary:
        summary_msg = {"role": "system", "content": f"[Previous conversation summary]\n{st.session_state.conversation_summary}"}
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

    # TTS生成
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
    # ... 函数内容与之前相同，此处省略以节省篇幅 ...
    pass

# ---------- CSS样式 ----------
st.markdown(f"""
<style>
    /* 您之前所有的 CSS 样式保持不变，只需在末尾添加以下规则使语音模式开关美观 */
    .voice-mode-toggle {{
        margin: 10px 0;
        display: flex;
        justify-content: center;
        gap: 20px;
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
st.title("CHINESE LEARNING ASSISTANT")

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
    # ... 显示节点内容（与之前相同，此处省略以节省篇幅）...
    pass

# ---------- 悬浮聊天窗 ----------
st.session_state.chat_open = True
if st.session_state.chat_open:
    st.markdown('<div class="chat-panel">', unsafe_allow_html=True)

    # 初始化音频上下文
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
            if (chatArea) chatArea.scrollTop = chatArea.scrollHeight;
        }, 100);
    </script>
    ''', unsafe_allow_html=True)

    # 播放TTS音频
    if st.session_state.pending_tts:
        audio_bytes, fmt = st.session_state.pending_tts
        st.audio(audio_bytes, format=fmt, autoplay=True)
        st.session_state.pending_tts = None

    # ---------- 语音模式开关 ----------
    voice_mode = st.toggle("🎤 语音模式（持续监听）", value=st.session_state.voice_mode, key="voice_mode_toggle")
    if voice_mode != st.session_state.voice_mode:
        st.session_state.voice_mode = voice_mode
        if not voice_mode:
            # 关闭语音模式时，清理可能残留的音频数据
            st.session_state.last_audio_data = None
        st.rerun()

    # ---------- 手动输入区域（始终显示，但语音模式开启时自动录音，手动输入仍可用） ----------
    col_clear, col_text = st.columns([1, 6])
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
    with col_text:
        if prompt := st.chat_input("Type a message...", key="text_input"):
            with st.spinner("Thinking..."):
                get_ai_reply(prompt)
            st.rerun()

    # ---------- 语音模式组件（仅在开启时显示并运行） ----------
    if st.session_state.voice_mode:
        # 嵌入 HTML/JS 组件，实现自动录音和语音活动检测
        st.markdown("""
        <div id="voice-recorder-container" style="display: none;"></div>
        <script>
        (function() {
            // 避免重复初始化
            if (window._voice_recorder_initialized) return;
            window._voice_recorder_initialized = true;

            let mediaRecorder = null;
            let audioChunks = [];
            let isRecording = false;
            let silenceTimer = null;
            let audioContext = null;
            let source = null;
            let stream = null;
            let analyser = null;
            let mediaStream = null;
            let lastVolume = 0;
            let silenceDuration = 0;

            const SILENCE_TIMEOUT = 3000;   // 静默 3 秒后停止录音
            const VOLUME_THRESHOLD = 0.01;  // 音量阈值

            // 请求麦克风权限
            async function initMicrophone() {
                try {
                    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
                    mediaStream = stream;
                    audioContext = new (window.AudioContext || window.webkitAudioContext)();
                    source = audioContext.createMediaStreamSource(stream);
                    analyser = audioContext.createAnalyser();
                    analyser.fftSize = 256;
                    source.connect(analyser);
                    // 启动音量检测循环
                    checkVolume();
                    return true;
                } catch (err) {
                    console.error("Microphone error:", err);
                    // 通知 Streamlit 错误（可选）
                    return false;
                }
            }

            function checkVolume() {
                if (!analyser) return;
                const dataArray = new Uint8Array(analyser.frequencyBinCount);
                analyser.getByteTimeDomainData(dataArray);
                let sum = 0;
                for (let i = 0; i < dataArray.length; i++) {
                    const v = (dataArray[i] - 128) / 128;
                    sum += v * v;
                }
                let rms = Math.sqrt(sum / dataArray.length);
                // 更新音量
                const nowActive = rms > VOLUME_THRESHOLD;
                if (nowActive) {
                    silenceDuration = 0;
                    if (!isRecording) {
                        startRecording();
                    }
                } else {
                    if (isRecording) {
                        silenceDuration += 100;
                        if (silenceDuration >= SILENCE_TIMEOUT) {
                            stopRecordingAndSend();
                        }
                    }
                }
                requestAnimationFrame(checkVolume);
            }

            function startRecording() {
                if (isRecording) return;
                // 重新获取流（确保 MediaRecorder 使用当前流）
                if (!mediaStream) return;
                audioChunks = [];
                mediaRecorder = new MediaRecorder(mediaStream);
                mediaRecorder.ondataavailable = event => {
                    if (event.data.size > 0) audioChunks.push(event.data);
                };
                mediaRecorder.onstop = () => {
                    const blob = new Blob(audioChunks, { type: 'audio/wav' });
                    const reader = new FileReader();
                    reader.onloadend = () => {
                        const base64data = reader.result.split(',')[1];
                        // 通过 Streamlit 组件发送音频数据
                        Streamlit.setComponentValue(base64data);
                    };
                    reader.readAsDataURL(blob);
                };
                mediaRecorder.start();
                isRecording = true;
                console.log("Recording started");
            }

            function stopRecordingAndSend() {
                if (mediaRecorder && isRecording) {
                    mediaRecorder.stop();
                    isRecording = false;
                    silenceDuration = 0;
                    console.log("Recording stopped and sent");
                }
            }

            // 初始化
            initMicrophone().catch(e => console.error(e));
        })();
        </script>
        """, unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)
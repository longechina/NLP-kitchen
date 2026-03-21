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

# ---------- 加载所有 Level 数据 ----------
@st.cache_data
def load_level_data():
    levels = {}
    for i in range(1, 4):
        try:
            with open(f"level{i}.json", "r", encoding="utf-8") as f:
                levels[f"Level {i}"] = json.load(f)
        except FileNotFoundError:
            st.error(f"level{i}.json not found. Please ensure all level files exist.")
            st.stop()
    return levels

levels_data = load_level_data()

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
- Search information on web to find specific, authoritative online resources that about current topic, content and level.
- Based on the search results, provide a structured recommendation in clear structure.
- Include a brief heading (Recommended Resources), then list each resource with a short description and a clickable web links.
- Keep the answer concise but informative.

Example output:
Recommended Resources

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
            # 返回的内容可能是文本，也可能包含工具调用，直接取 content
            return response.choices[0].message.content
        except Exception as e:
            if "429" in str(e) or "rate_limit_exceeded" in str(e):
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (attempt + 1))  # 递增等待
                    continue
            return f"Auto-generation error: {e}"
    return "Auto-generation error: Rate limit exceeded after retries."

def auto_push_reference(level, path_string):
    """
    自动推送参考消息到聊天记录，并生成语音（可选）。
    只在未推送时执行一次。
    """
    if st.session_state.auto_ref_pushed:
        return

    # 获取当前页面的完整内容
    full_page_content = get_current_page_full_content()
    if not full_page_content:
        return

    ref_content = auto_generate_reference(level, full_page_content, path_string)
    if ref_content:
        # 直接使用 AI 生成的 Markdown 内容（无需额外包装）
        final_msg = ref_content.strip()
        st.session_state.messages.append({"role": "assistant", "content": final_msg})

        # 可选：生成语音（如果不需要可注释掉）
        audio_bytes, fmt = text_to_speech(final_msg)
        if audio_bytes:
            st.session_state.pending_tts = (audio_bytes, fmt)

        st.session_state.auto_ref_pushed = True

# ========== 缓存的 AI 回复函数 ==========
@st.cache_data(ttl=3600, max_entries=100)
def cached_chat_completion(system_prompt, page_context, summary_text, user_text):
    """缓存 AI 回复，参数必须可哈希"""
    messages = [{"role": "system", "content": system_prompt}]
    if page_context:
        messages.append({"role": "system", "content": f"[Current page context]\n{page_context}"})
    if summary_text:
        messages.append({"role": "system", "content": f"[Previous conversation summary]\n{summary_text}"})
    messages.append({"role": "user", "content": user_text})
    try:
        response = client.chat.completions.create(
            model="groq/compound",
            messages=messages,
            temperature=0.7,
            max_tokens=8192
        )
        return response.choices[0].message.content
    except Exception as e:
        err = str(e)
        if "rate_limit_exceeded" in err or "quota" in err.lower():
            return "I've reached my usage limit. Please try again in a few moments, or click Clear to start fresh."
        else:
            return f"Sorry, I encountered an error: {err}"

# ========== 生成总结的函数 ==========
def generate_summary(history, old_summary=""):
    """基于历史对话和旧总结生成新总结（使用 AI 自身）"""
    if not history:
        return old_summary
    # 构建总结提示
    prompt = "请用中文总结以下对话的核心内容，保持简洁。"
    if old_summary:
        prompt += f"已有的总结：{old_summary}\n"
    prompt += "新对话：\n" + "\n".join([f"{msg['role']}: {msg['content']}" for msg in history])
    try:
        response = client.chat.completions.create(
            model="groq/compound",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=8192
        )
        new_summary = response.choices[0].message.content
        # 保存到文件（便于调试和持久化）
        with open("conversation_summary.txt", "w", encoding="utf-8") as f:
            f.write(new_summary)
        return new_summary
    except Exception:
        # 如果调用失败，返回旧总结
        return old_summary

# ========== 修改后的 get_ai_reply ==========
def get_ai_reply(user_text):
    # 1. 将用户消息加入历史（用于显示和总结）
    st.session_state.messages.append({"role": "user", "content": user_text})
    st.session_state.conv_history.append({"role": "user", "content": user_text})
    st.session_state.user_msg_count += 1

    # 2. 检查是否需要生成总结（每5条用户消息触发一次）
    if st.session_state.user_msg_count % 5 == 0 and st.session_state.conv_history:
        # 生成总结，基于当前对话历史（包括本次用户消息）和旧总结
        new_summary = generate_summary(st.session_state.conv_history, st.session_state.conversation_summary)
        st.session_state.conversation_summary = new_summary
        # 清空历史，为下一轮总结做准备
        st.session_state.conv_history.clear()

    # 3. 获取当前页面上下文
    page_context = get_current_page_full_content()  # 注意：这里是用于聊天时的上下文，我们保留原逻辑但改用了完整内容

    # 4. 调用缓存的 AI 回复（只传递总结，不传递全部历史）
    reply = cached_chat_completion(
        system_prompt,
        page_context if page_context else "",
        st.session_state.conversation_summary,
        user_text
    )

    # 5. 将 AI 回复存入显示用的 messages，并加入对话历史（用于下次总结）
    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.session_state.conv_history.append({"role": "assistant", "content": reply})

    # 6. 生成语音
    audio_bytes, fmt = text_to_speech(reply)
    if audio_bytes:
        st.session_state.pending_tts = (audio_bytes, fmt)

# ---------- 自定义CSS ----------
st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@300;400;500;600;700;800&display=swap');

    body {{
        {bg_css}
        background-size: cover;
        background-position: center;
        background-attachment: fixed;
        background-repeat: no-repeat;
        background-color: #f0f0f0;
    }}

    html, body, .stApp, .main, div[data-testid="stAppViewContainer"],
    div[data-testid="stHeader"], div[data-testid="stToolbar"],
    div[data-testid="stVerticalBlock"], div[data-testid="column"],
    header, footer {{
        background-color: transparent !important;
    }}

    #stFooter {{ display: none !important; }}
    .main {{ padding: 2rem 1rem !important; }}

    html, body, [class*="css"], h1, h2, h3, p, div, span, .stMarkdown {{
        color: #000000 !important;
        text-shadow: none !important;
    }}

    h1 {{
        font-size: 72px !important;
        font-weight: 800 !important;
        letter-spacing: -0.5px;
        color: #000000 !important;
        margin-bottom: 16px !important;
        text-transform: uppercase;
    }}

    h2 {{
        font-size: 54px !important;
        font-weight: 400 !important;
        color: #000000 !important;
        margin: 24px 0 8px 0 !important;
    }}

    h3 {{
        font-size: 42px !important;
        font-weight: 500 !important;
        color: #000000 !important;
        margin: 16px 0 8px 0 !important;
    }}

    div[data-testid="column"] .stButton > button {{
        font-size: 56px !important;
        font-weight: 700 !important;
        background-color: transparent !important;
        color: #000000 !important;
        border: none !important;
        box-shadow: none !important;
        padding: 8px 0 !important;
        border-radius: 0 !important;
        transition: all 0.2s ease !important;
        width: 100%;
        text-align: left;
        margin: 0 !important;
        line-height: 1.2;
    }}
    div[data-testid="column"] .stButton > button:hover {{
        text-decoration: underline !important;
        background-color: transparent !important;
    }}

    .stButton > button {{
        font-size: 28px !important;
        font-weight: 500 !important;
        padding: 20px 24px !important;
        border-radius: 40px !important;
        background-color: transparent !important;
        color: #000000 !important;
        border: 2px solid rgba(0,0,0,0.3) !important;
        transition: all 0.2s ease !important;
        width: 100%;
        box-shadow: none !important;
    }}

    .stButton > button:hover {{
        background-color: rgba(255,255,255,0.3) !important;
        border-color: #000000 !important;
    }}

    div[data-testid="stVerticalBlock"] > div {{
        background-color: rgba(255,255,255,0.4) !important;
        border: none !important;
        box-shadow: none !important;
        padding: 16px !important;
        border-radius: 16px !important;
    }}

    div[data-testid="stVerticalBlock"] > div h3 {{
        font-size: 48px !important;
        font-weight: 500 !important;
        color: #000000 !important;
        margin: 0 0 8px 0 !important;
    }}

    div[data-testid="stVerticalBlock"] > div div {{
        font-size: 32px !important;
        color: #333333 !important;
        margin-bottom: 8px !important;
    }}

    div[data-testid="stVerticalBlock"] > div p {{
        font-size: 32px !important;
        color: #000000 !important;
    }}

    .breadcrumb {{
        font-size: 28px !important;
        color: #333333 !important;
        padding: 12px 0;
        border-bottom: 2px solid rgba(0,0,0,0.2);
        margin-bottom: 24px;
        font-weight: 400;
    }}

    .back-button .stButton > button {{
        background-color: transparent !important;
        color: #000000 !important;
        border: none !important;
        padding: 12px 0 !important;
        font-size: 28px !important;
        text-align: left;
        font-weight: 500 !important;
        box-shadow: none !important;
        border-bottom: 2px solid transparent !important;
    }}

    .back-button .stButton > button:hover {{
        background-color: transparent !important;
        border-bottom: 2px solid #000000 !important;
    }}

    div[data-testid="column"] {{ padding: 8px !important; }}

    hr {{
        margin: 24px 0 !important;
        border-color: rgba(0,0,0,0.1) !important;
    }}

    .chat-float-container {{
        position: fixed;
        bottom: 30px;
        right: 30px;
        z-index: 1000;
        display: flex;
        flex-direction: column;
        align-items: flex-end;
    }}

    .chat-panel {{
        width: 420px;
        height: 600px;
        background-color: #ffffff !important;
        border-radius: 20px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.15);
        border: 1px solid rgba(0,0,0,0.1);
        display: flex;
        flex-direction: column;
        overflow: hidden;
    }}

    .chat-messages-area {{
        flex: 1;
        overflow-y: auto;
        padding: 16px;
    }}

    .chat-input-area {{
        padding: 12px 16px;
        border-top: 1px solid #e0e0e0;
    }}

    /* 输入区域列布局优化 */
    .chat-input-area div[data-testid="column"] {{
        padding: 0 4px !important;
        display: flex !important;
        align-items: center !important;
    }}

    /* 语音输入样式 - 紧凑的小黑框 */
    .chat-input-area div[data-testid="stAudioInput"] {{
        margin: 0 !important;
    }}
    
    .chat-input-area div[data-testid="stAudioInput"] > div {{
        background-color: #2d3748 !important;
        border: none !important;
        border-radius: 12px !important;
        margin: 0 !important;
        padding: 8px !important;
        width: 56px !important;
        height: 56px !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
    }}
    
    .chat-input-area div[data-testid="stAudioInput"] button {{
        color: #ffffff !important;
        background-color: transparent !important;
        padding: 0 !important;
        min-height: auto !important;
    }}

    /* 文字输入框样式 - 椭圆形，大字体，矮框 */
    .chat-input-area .stChatInput {{
        margin: 0 !important;
    }}
    .chat-input-area .stChatInput > div {{
        background-color: #2d3748 !important;
        border: none !important;
        border-radius: 28px !important;
        margin: 0 !important;
        padding: 0 !important;
        height: 56px !important;
    }}
    .chat-input-area .stChatInput input {{
        font-size: 22px !important;
        padding: 0 20px !important;
        background-color: transparent !important;
        color: #ffffff !important;
        height: 56px !important;
        min-height: 56px !important;
        max-height: 56px !important;
        border: none !important;
        line-height: 56px !important;
    }}
    .chat-input-area .stChatInput input::placeholder {{
        font-size: 22px !important;
        color: #a0aec0 !important;
    }}
    
    /* 移除可能的红色边框 */
    .chat-input-area .stChatInput > div:focus-within,
    .chat-input-area .stChatInput input:focus {{
        border: none !important;
        outline: none !important;
        box-shadow: none !important;
    }}

    .chat-message {{
        margin-bottom: 16px;
        font-size: 16px;
        line-height: 1.5;
        padding: 8px 0;
    }}

    .chat-message strong {{
        font-weight: 600;
        margin-right: 6px;
        color: #2d3748;
    }}

    .clear-button-container .stButton > button {{
        background-color: transparent !important;
        border: none !important;
        box-shadow: none !important;
        color: #666666 !important;
        font-size: 14px !important;
        padding: 4px 12px !important;
        margin: 0 !important;
        width: auto !important;
        text-decoration: none !important;
        cursor: pointer;
        font-weight: 500 !important;
        border-radius: 16px !important;
    }}
    .clear-button-container .stButton > button:hover {{
        color: #000000 !important;
        background-color: rgba(0,0,0,0.05) !important;
    }}

    /* AI按钮样式 - 简洁的圆角按钮外观 */
    button[data-testid="baseButton-secondary"][key="chat_toggle_btn"],
    .chat-float-container .stButton button {{
        background-color: transparent !important;
        border: 2px solid rgba(0,0,0,0.3) !important;
        border-radius: 40px !important;
        padding: 16px 32px !important;
        font-size: 28px !important;
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

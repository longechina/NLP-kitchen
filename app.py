import json
import base64
import io
import re
import os
import time
import logging
import datetime
import streamlit as st
import groq
import requests
import tempfile
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# 导入OCR模块
from ocr_image_module import ocr_images_batch, BAIMIAO_CONFIG as IMAGE_OCR_CONFIG, format_results_as_text, save_results_to_txt
from ocr_pdf_module import ocr_pdf, BAIMIAO_CONFIG as PDF_OCR_CONFIG

# ---------- 配置日志记录 ----------
if not os.path.exists("logs"):
    os.makedirs("logs")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/app.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ---------- 模型配置 ----------
AVAILABLE_MODELS = {
    "Llama 4 Scout 17B": {
        "id": "meta-llama/llama-4-scout-17b-16e-instruct",
        "max_tokens": 8192
    },
    "Llama 3.3 70B": {
        "id": "llama-3.3-70b-versatile",
        "max_tokens": 8192
    },
    "Llama 3.1 8B": {
        "id": "llama-3.1-8b-instant",
        "max_tokens": 8192
    },
    "GPT OSS 120B": {
        "id": "openai/gpt-oss-120b",
        "max_tokens": 8192
    },
    "GPT OSS 20B": {
        "id": "openai/gpt-oss-20b",
        "max_tokens": 8192
    },
    "Qwen 3 32B": {
        "id": "qwen/qwen3-32b",
        "max_tokens": 8192
    },
    "Kimi K2 Instruct": {
        "id": "moonshotai/kimi-k2-instruct-0905",
        "max_tokens": 8192
    },
    "Groq Compound": {
        "id": "groq/compound",
        "max_tokens": 8192
    },
    "Groq Compound Mini": {
        "id": "groq/compound-mini",
        "max_tokens": 8192
    },
}

DEFAULT_MODEL = "Llama 3.3 70B"

# 初始化模型选择状态
if "selected_model" not in st.session_state:
    st.session_state.selected_model = DEFAULT_MODEL
if "model_name" not in st.session_state:
    st.session_state.model_name = AVAILABLE_MODELS[DEFAULT_MODEL]["id"]
if "model_max_tokens" not in st.session_state:
    st.session_state.model_max_tokens = AVAILABLE_MODELS[DEFAULT_MODEL]["max_tokens"]

# ---------- GitHub 配置 ----------
GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN")
REPO_OWNER = st.secrets.get("GITHUB_REPO_OWNER")
REPO_NAME = st.secrets.get("GITHUB_REPO_NAME")
GITHUB_ENABLED = GITHUB_TOKEN and REPO_OWNER and REPO_NAME


# ---------- 加载 Quiz 题型模板 ----------
def load_quiz_template():
    try:
        with open("chinese_test_template.txt", "r", encoding="utf-8") as f:
            content = f.read()
            logger.info("Successfully loaded chinese_test_template.txt")
            return content
    except FileNotFoundError:
        logger.warning("chinese_test_template.txt not found, using default template")
        return """
1. 单选题（Multiple Choice）：
   题目描述：以下哪个选项最符合题意？
   A. 选项A
   B. 选项B
   C. 选项C
   D. 选项D

2. 填空题（Fill in the blank）：
   请用正确的词语完成以下句子：
   ______

3. 翻译题（Translation）：
   请将以下句子翻译成中文：
   ______
"""

QUIZ_TEMPLATE = load_quiz_template()

# ---------- GitHub 上传函数 ----------
def upload_file_to_github(file_path, content, commit_message):
    """上传文件到 GitHub"""
    if not GITHUB_ENABLED:
        logger.warning("GitHub not configured, skipping upload")
        return False
    
    api_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{file_path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        response = requests.get(api_url, headers=headers)
        
        if response.status_code == 200:
            file_data = response.json()
            existing_content = base64.b64decode(file_data["content"]).decode("utf-8")
            if existing_content == content:
                logger.info(f"File {file_path} unchanged, skipping upload")
                return True
            sha = file_data["sha"]
        else:
            sha = None
        
        data = {
            "message": commit_message,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
            "sha": sha
        }
        
        response = requests.put(api_url, headers=headers, json=data)
        
        if response.status_code in [200, 201]:
            logger.info(f"Successfully uploaded {file_path} to GitHub")
            return True
        else:
            logger.error(f"GitHub upload failed: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"GitHub upload error: {e}")
        return False


def save_to_github(file_path, content, commit_message):
    """保存文件到 GitHub（如果配置了）或本地"""
    if GITHUB_ENABLED:
        return upload_file_to_github(file_path, content, commit_message)
    else:
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"Saved to local {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save locally: {e}")
            return False


# ---------- 加载 Teaching Principles ----------
def load_teaching_principles():
    try:
        with open("teaching_principle.txt", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return """Core: Guide, Don't Answer
NEVER give direct answers. Use guidance instead.
Guidance: Analogy, examples, simple words, Socratic questions
Feedback: Show score, indicate correct/incorrect. DO NOT give answers unless requested.
Log every quiz to feedback.md"""

TEACHING_PRINCIPLES = load_teaching_principles()

# ---------- Quiz 状态管理 ----------
if "quiz_active" not in st.session_state:
    st.session_state.quiz_active = False
if "current_quiz" not in st.session_state:
    st.session_state.current_quiz = None
if "quiz_answers" not in st.session_state:
    st.session_state.quiz_answers = {}
if "quiz_asked" not in st.session_state:
    st.session_state.quiz_asked = False


# ---------- 保存对话总结到 GitHub ----------
def save_conversation_summary(summary):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"""
## Conversation Summary - {timestamp}
{summary}

---
"""
    existing_content = ""
    try:
        with open("conversation_summary.txt", "r", encoding="utf-8") as f:
            existing_content = f.read()
    except FileNotFoundError:
        pass
    
    new_content = existing_content + entry if existing_content else "# Conversation Summaries\n\n" + entry
    save_to_github("conversation_summary.txt", new_content, f"Add conversation summary - {timestamp}")
    
    try:
        with open("conversation_summary.txt", "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:
        logger.error(f"Failed to save local summary: {e}")


# ---------- 生成 Quiz ----------
def generate_quiz(topic, full_page_content):
    if st.session_state.language == "Chinese":
        template = """
### 1. 单选题 (Multiple Choice)
**Instruction:** Choose the ONE best answer.

---

### 2. 填空题 (Fill in the blank)
**Instruction:** Fill in the blank with the correct word.

---

### 3. 翻译题 (Translation)
**Instruction:** Translate into English.

---

### 4. 改错题 (Error correction)
**Instruction:** Find and correct the mistake.

---

### 5. 造句题 (Sentence making)
**Instruction:** Use the given words to make a sentence.

"""
    else:
        template = """
### 1. 单选题 (Multiple Choice)
**Instruction:** Choose the ONE best answer.

---

### 2. 填空题 (Fill in the blank)
**Instruction:** Fill in the blank with the correct word.

---

### 3. 翻译题 (Translation)
**Instruction:** Translate into Chinese.

---

### 4. 改错题 (Error correction)
**Instruction:** Find and correct the mistake.

---

### 5. 造句题 (Sentence making)
**Instruction:** Use the given words to make a sentence.

"""
    
    prompt = f"""You are a language test designer. Based on the topic and content below, generate a COMPLETE quiz with ALL 5 question types.

**Topic:** {topic}
**Current Content:** {full_page_content[:800] if full_page_content else "No additional content"}

**Question Types (generate ONE question for EACH type):**
{template}

**STRUCTURE REQUIREMENTS:**
Use EXACTLY this format with 5 numbered questions:

## Quiz: {topic}

1. [Question 1 - Multiple Choice with A, B, C, D options]
2. [Question 2 - Fill in the blank with a complete sentence and a blank]
3. [Question 3 - Translation question with a full sentence to translate]
4. [Question 4 - Error correction question with a sentence containing one error]
5. [Question 5 - Sentence making question with 3-5 words to arrange]

**CRITICAL RULES:**
- Create COMPLETE, answerable questions based on "{topic}"
- Multiple choice: Provide 4 realistic options (A, B, C, D)
- Fill in the blank: Create a complete sentence with one blank (____)
- Translation: Provide a full sentence to translate
- Error correction: Provide a sentence with ONE specific error
- Sentence making: Provide 3-5 words that can form a meaningful sentence
- NEVER include the answer
- Number questions 1 through 5 only

Generate the quiz:"""
    
    try:
        response = client.chat.completions.create(
            model=st.session_state.model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=st.session_state.model_max_tokens,
        )
        quiz_text = response.choices[0].message.content.strip()
        
        # 确保只有5个问题
        lines = quiz_text.split('\n')
        cleaned_lines = []
        question_count = 0
        for line in lines:
            if re.match(r'^\d+\.', line.strip()):
                question_count += 1
                if question_count <= 5:
                    cleaned_lines.append(line)
            else:
                if question_count <= 5:
                    cleaned_lines.append(line)
        
        return "\n".join(cleaned_lines)
        
    except Exception as e:
        logger.error(f"Quiz generation error: {e}")
        return None
        

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
    initial_sidebar_state="expanded",
    menu_items=None
)

# ---------- 初始化语言状态 ----------
if "language" not in st.session_state:
    st.session_state.language = "Chinese"

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

# ---------- 加载 NEMT & CET 数据 ----------
@st.cache_data
def load_nemt_cet_data():
    nemt_cet_data = {}
    files_to_load = ["TEM-8.json", "NEMT.json", "CET-46.json"]
    
    for filename in files_to_load:
        try:
            with open(filename, "r", encoding="utf-8") as f:
                nemt_cet_data[filename.replace('.json', '')] = json.load(f)
                logger.info(f"Successfully loaded {filename}")
        except FileNotFoundError:
            logger.warning(f"{filename} not found")
            nemt_cet_data[filename.replace('.json', '')] = {}
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error in {filename}: {e}")
            nemt_cet_data[filename.replace('.json', '')] = {}
    
    return nemt_cet_data

nemt_cet_data = load_nemt_cet_data()

# 状态管理
if "current_mode" not in st.session_state:
    st.session_state.current_mode = "textbook"
if "selected_nemt_cet" not in st.session_state:
    st.session_state.selected_nemt_cet = None
if "nemt_cet_path" not in st.session_state:
    st.session_state.nemt_cet_path = []

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
        logger.error(f"语音识别失败: {e}")
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
            logger.error(f"Kokoro TTS error: {e}")
            pass
    try:
        response = client.audio.speech.create(
            model="canopylabs/orpheus-v1-english",
            voice="autumn",
            input=text,
            response_format="wav",
        )
        return response.read(), "audio/wav"
    except Exception as e:
        logger.error(f"Orpheus TTS error: {e}")
        return None, None

# ---------- 构建系统提示 ----------
def build_system_prompt(levels):
    prompt = f"""You are a language learning assistant helping students learn Languages.
You have access to learning materials across 3 levels covering grammar, vocabulary, and conversation.

TEACHING PRINCIPLES (MUST FOLLOW):
{TEACHING_PRINCIPLES}

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
    st.session_state.chat_open = True
if "last_audio_id" not in st.session_state:
    st.session_state.last_audio_id = None
if "pending_tts" not in st.session_state:
    st.session_state.pending_tts = None

# ========== 对话总结相关状态 ==========
if "conversation_summary" not in st.session_state:
    st.session_state.conversation_summary = ""
if "conv_history" not in st.session_state:
    st.session_state.conv_history = []
if "user_msg_count" not in st.session_state:
    st.session_state.user_msg_count = 0

# ========== 自动参考相关状态 ==========
if "page_recommendations" not in st.session_state:
    st.session_state.page_recommendations = {}
if "current_page_key" not in st.session_state:
    st.session_state.current_page_key = None

# ========== 卡片翻转状态 ==========
if "flip_states" not in st.session_state:
    st.session_state.flip_states = {}

# ========== 搜索相关状态 ==========
if "search_keyword" not in st.session_state:
    st.session_state.search_keyword = ""
if "search_results" not in st.session_state:
    st.session_state.search_results = []
if "search_scope" not in st.session_state:
    st.session_state.search_scope = "global"


# ---------- 获取当前页面唯一标识 ----------
def get_current_page_key():
    if st.session_state.current_mode == "textbook":
        return f"textbook_{st.session_state.level}_{'_'.join(st.session_state.path)}"
    else:
        return f"nemt_cet_{st.session_state.selected_nemt_cet}_{'_'.join(st.session_state.nemt_cet_path)}"


# ---------- 获取当前页面全部内容 ----------
def get_current_page_full_content():
    if st.session_state.current_mode == "nemt_cet":
        if not st.session_state.selected_nemt_cet or not st.session_state.nemt_cet_path:
            return None
        data = nemt_cet_data.get(st.session_state.selected_nemt_cet, {})
        
        if len(data) == 1 and st.session_state.selected_nemt_cet in data:
            data = data[st.session_state.selected_nemt_cet]
        
        node = data
        for key in st.session_state.nemt_cet_path:
            node = node.get(key, {})
            if not node:
                return None
        
        content_node = node
        dir_name = ""
        
        if isinstance(content_node, dict):
            if len(content_node) == 1:
                dir_name = list(content_node.keys())[0]
                content_node = content_node[dir_name]
            elif "name" in content_node:
                dir_name = content_node["name"]
        
        name_path_parts = []
        temp_data = data
        for path_key in st.session_state.nemt_cet_path:
            temp_data = temp_data.get(path_key, {})
            if isinstance(temp_data, dict) and len(temp_data) > 0:
                if len(temp_data) == 1:
                    inner_dir_name = list(temp_data.keys())[0]
                    name_path_parts.append(inner_dir_name)
                elif "name" in temp_data:
                    name_path_parts.append(temp_data["name"])
        
        parts = []
        location = " > ".join(name_path_parts) if name_path_parts else st.session_state.selected_nemt_cet
        parts.append(f"The user is currently viewing: {location}")
        
        if dir_name:
            parts.append(f"Section: {dir_name}")
        elif "name" in content_node:
            parts.append(f"Section: {content_node['name']}")
        
        if "notes" in content_node and content_node["notes"]:
            parts.append(f"Notes: {content_node['notes']}")
        if "examples" in content_node and content_node["examples"]:
            parts.append("Example sentences:\n" + "\n".join(f"  - {e}" for e in content_node["examples"]))
        if "words" in content_node and content_node["words"]:
            if isinstance(content_node["words"], str):
                words_list = content_node["words"].split(" / ")
            else:
                words_list = content_node["words"]
            parts.append("Words:\n" + "\n".join(f"  - {w}" for w in words_list[:20]))
        
        return "\n".join(parts)
    else:
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


# ========== 通用递归搜索函数 ==========
def search_in_dict(node, path_list, source, level_num, keyword):
    matches = []
    keyword_lower = keyword.lower()
    
    if not isinstance(node, dict):
        return matches
    
    if "name" in node and node["name"] and keyword_lower in str(node["name"]).lower():
        matches.append({
            "source": source,
            "level": level_num,
            "path": path_list.copy(),
            "type": "Section",
            "content": str(node["name"])[:150]
        })
    
    if "notes" in node and node["notes"] and keyword_lower in str(node["notes"]).lower():
        content = str(node["notes"])[:200] + "..." if len(str(node["notes"])) > 200 else str(node["notes"])
        matches.append({
            "source": source,
            "level": level_num,
            "path": path_list.copy(),
            "type": "Note",
            "content": content
        })
    
    if "examples" in node and node["examples"]:
        if isinstance(node["examples"], list):
            for idx, ex in enumerate(node["examples"]):
                if ex and keyword_lower in str(ex).lower():
                    matches.append({
                        "source": source,
                        "level": level_num,
                        "path": path_list.copy(),
                        "type": "Example",
                        "content": str(ex)[:150],
                        "index": idx
                    })
        elif isinstance(node["examples"], str) and keyword_lower in node["examples"].lower():
            matches.append({
                "source": source,
                "level": level_num,
                "path": path_list.copy(),
                "type": "Example",
                "content": node["examples"][:150]
            })
    
    if "vocabulary" in node and node["vocabulary"]:
        if isinstance(node["vocabulary"], list):
            for idx, item in enumerate(node["vocabulary"]):
                if item and keyword_lower in str(item).lower():
                    matches.append({
                        "source": source,
                        "level": level_num,
                        "path": path_list.copy(),
                        "type": "Vocabulary",
                        "content": str(item)[:150],
                        "index": idx
                    })
        elif isinstance(node["vocabulary"], str) and keyword_lower in node["vocabulary"].lower():
            matches.append({
                "source": source,
                "level": level_num,
                "path": path_list.copy(),
                "type": "Vocabulary",
                "content": node["vocabulary"][:150]
            })
    
    if "words" in node and node["words"] and keyword_lower in str(node["words"]).lower():
        content = str(node["words"])[:200] + "..." if len(str(node["words"])) > 200 else str(node["words"])
        matches.append({
            "source": source,
            "level": level_num,
            "path": path_list.copy(),
            "type": "Words",
            "content": content
        })
    
    for key, value in node.items():
        if key in ("name", "notes", "examples", "vocabulary", "words"):
            continue
        if isinstance(value, dict):
            matches.extend(search_in_dict(value, path_list + [key], source, level_num, keyword))
        elif isinstance(value, list):
            for idx, item in enumerate(value):
                if isinstance(item, dict):
                    matches.extend(search_in_dict(item, path_list + [f"{key}[{idx}]"], source, level_num, keyword))
    
    return matches


# ========== 全局搜索（搜索所有数据）==========
def global_search(keyword):
    if not keyword.strip():
        return []
    
    results = []
    
    # 1. 搜索 textbook 数据
    for level_num in range(1, 4):
        level_key = f"Level {level_num}"
        if level_key in levels_data:
            root_node = levels_data[level_key]
            for root_key, root_value in root_node.items():
                if isinstance(root_value, dict):
                    results.extend(search_in_dict(root_value, [root_key], "textbook", level_num, keyword))
    
    # 2. 搜索 NEMT & CET 数据
    for exam_name, exam_data in nemt_cet_data.items():
        if not exam_data:
            continue
        
        data_to_search = exam_data
        if len(exam_data) == 1 and exam_name in exam_data:
            data_to_search = exam_data[exam_name]
        
        for key, value in data_to_search.items():
            if isinstance(value, dict):
                if keyword.lower() in str(key).lower():
                    results.append({
                        "source": "nemt_cet",
                        "exam": exam_name,
                        "level": None,
                        "path": [exam_name, key],
                        "type": "Category",
                        "content": str(key)[:150]
                    })
                results.extend(search_in_dict(value, [exam_name, key], "nemt_cet", None, keyword))
    
    return results


# ========== 本地搜索 ==========
def local_search_textbook(keyword):
    if not keyword.strip():
        return []
    if not st.session_state.level:
        return []
    
    results = []
    level_key = f"Level {st.session_state.level}"
    if level_key in levels_data:
        root_node = levels_data[level_key]
        for root_key, root_value in root_node.items():
            if isinstance(root_value, dict):
                results.extend(search_in_dict(root_value, [root_key], "textbook", st.session_state.level, keyword))
    return results


def local_search_nemt_cet(keyword):
    if not keyword.strip():
        return []
    if not st.session_state.selected_nemt_cet:
        return []
    
    results = []
    exam_name = st.session_state.selected_nemt_cet
    exam_data = nemt_cet_data.get(exam_name, {})
    
    if not exam_data:
        return results
    
    data_to_search = exam_data
    if len(exam_data) == 1 and exam_name in exam_data:
        data_to_search = exam_data[exam_name]
    
    for key, value in data_to_search.items():
        if isinstance(value, dict):
            if keyword.lower() in str(key).lower():
                results.append({
                    "source": "nemt_cet",
                    "exam": exam_name,
                    "level": None,
                    "path": [exam_name, key],
                    "type": "Category",
                    "content": str(key)[:150]
                })
            results.extend(search_in_dict(value, [exam_name, key], "nemt_cet", None, keyword))
    
    return results


def local_search(keyword):
    if st.session_state.current_mode == "textbook":
        return local_search_textbook(keyword)
    elif st.session_state.current_mode == "nemt_cet":
        return local_search_nemt_cet(keyword)
    return []


# ========== 自动生成参考消息 ==========
def auto_generate_reference(level, full_page_content, path_string, mode="textbook"):
    topic = ""
    
    if mode == "nemt_cet":
        if path_string:
            parts = path_string.split(" > ")
            topic = parts[-1] if parts else "English exam vocabulary"
        else:
            topic = "English exam vocabulary"
    else:
        if "Section:" in full_page_content:
            match = re.search(r"Section: (.+)", full_page_content)
            if match:
                topic = match.group(1)
        if not topic:
            parts = path_string.split(" > ")
            topic = parts[-1] if parts else "general"

    notes = ""
    if "Notes:" in full_page_content:
        notes_match = re.search(r"Notes: (.+?)(?:Example|Vocabulary|Words|$)", full_page_content, re.DOTALL)
        if notes_match:
            notes = notes_match.group(1).strip()[:200]

    if mode == "nemt_cet" or st.session_state.language == "English":
        single_keyword = topic.split()[-1] if topic else "english"
        single_keyword = re.sub(r'[^\w\s]', '', single_keyword).strip().lower()
        
        prompt = f"""You are an English learning assistant. The user is at Level {level} studying: "{topic}".

Topic summary: {notes if notes else "Basic English learning topic"}

Your task:
- Generate 3-4 high-quality learning resources using fixed trusted platforms
- DO search the web
- Use the topic keyword to build real, valid search links
- Keep it concise
- No emojis!

Use these rules to generate links:
- YouTube: https://www.youtube.com/results?search_query={topic.replace(' ', '+')}+english+learning
- Quizlet: https://quizlet.com/search?query={topic}+vocabulary
- StackExchange: https://english.stackexchange.com/search?q={single_keyword} only 1 keyword.

Example format:
【Recommended Resources】

- YouTube: Beginner explanation video  
  [Watch](https://www.youtube.com/results?search_query={topic.replace(' ', '+')}+english+learning)

- Quizlet: Flashcards for practice  
  [Practice](https://quizlet.com/search?query={topic}+vocabulary)

- English StackExchange: Community Q&A discussion  
  [Explore](https://english.stackexchange.com/search?q={single_keyword})

Now generate for: {topic}
"""
    else:
        single_keyword = topic.split()[-1] if topic else "中文"
        single_keyword = re.sub(r'[^\u4e00-\u9fff\w\s]', '', single_keyword).strip()
        
        prompt = f"""You are a Chinese learning assistant. The user is at Level {level} studying: "{topic}".

Topic summary: {notes if notes else "Basic Chinese learning topic"}

Your task:
- Generate 3-4 high-quality learning resources using fixed trusted platforms
- DO search the web
- Use the topic keyword to build real, valid search links
- Keep it concise
- No emojis!

Use these rules to generate links:
- YouTube: https://www.youtube.com/results?search_query={topic}+in+chinese （the Chinese topic）
- Quizlet: https://quizlet.com/search?query={topic}+chinese the topic is Chinese
- StackExchange: https://chinese.stackexchange.com/search?q={single_keyword} only 1 Chinese keyword.

Example format:
【Recommended Resources】

- YouTube: Beginner explanation video  
  [Watch](https://www.youtube.com/results?search_query={topic}+in+chinese)

- Quizlet: Flashcards for practice  
  [Practice](https://quizlet.com/search?query={topic}+chinese)

- Chinese StackExchange: Community Q&A discussion  
  [Explore](https://chinese.stackexchange.com/search?q={single_keyword})

Now generate for: {topic}
"""
    max_retries = 2
    retry_delay = 3
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=st.session_state.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=st.session_state.model_max_tokens,
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


# ========== 获取或生成当前页面的推荐资源 ==========
def get_page_recommendations():
    page_key = get_current_page_key()
    
    if st.session_state.current_page_key != page_key:
        st.session_state.current_page_key = page_key
    
    if page_key not in st.session_state.page_recommendations:
        full_page_content = get_current_page_full_content()
        if full_page_content:
            path_string = ""
            mode = ""
            level = None
            
            if st.session_state.current_mode == "textbook":
                path_string = " > ".join(st.session_state.path)
                mode = "textbook"
                level = st.session_state.level
            else:
                data = nemt_cet_data.get(st.session_state.selected_nemt_cet, {})
                if len(data) == 1 and st.session_state.selected_nemt_cet in data:
                    data = data[st.session_state.selected_nemt_cet]
                
                name_path_parts = []
                temp_data = data
                for path_key in st.session_state.nemt_cet_path:
                    temp_data = temp_data.get(path_key, {})
                    if isinstance(temp_data, dict) and len(temp_data) > 0:
                        if len(temp_data) == 1:
                            inner_dir_name = list(temp_data.keys())[0]
                            name_path_parts.append(inner_dir_name)
                        elif "name" in temp_data:
                            name_path_parts.append(temp_data["name"])
                
                path_string = " > ".join(name_path_parts) if name_path_parts else st.session_state.selected_nemt_cet
                mode = "nemt_cet"
                level = None
            
            ref_msg = auto_generate_reference(level, full_page_content, path_string, mode)
            if ref_msg:
                st.session_state.page_recommendations[page_key] = ref_msg
    
    return st.session_state.page_recommendations.get(page_key)


# ========== 使用语言模型翻译单词 ==========
def translate_word(word, target_lang="Chinese"):
    try:
        logger.info(f"Translating word: {word}")
        clean_word = re.sub(r'[^a-zA-Z\s-]', '', word).strip()
        clean_word = clean_word.split()[0] if clean_word else word
        clean_word = clean_word.lower()
        
        if not clean_word or len(clean_word) < 2:
            logger.warning(f"Invalid word after cleaning: {word}, returning original")
            return word
        
        prompt = f"""Translate the following English word to {target_lang}. Only return the translation word, nothing else.
Word: {clean_word}
Translation:"""
        
        response = client.chat.completions.create(
            model=st.session_state.model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=st.session_state.model_max_tokens,
        )
        translation = response.choices[0].message.content.strip()
        
        if not translation:
            logger.warning(f"Empty translation for '{clean_word}', returning original")
            return clean_word
        
        logger.info(f"Translation for '{clean_word}': '{translation}'")
        return translation
    except Exception as e:
        logger.error(f"Translation error for '{word}': {e}")
        return word

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
            model=st.session_state.model_name,
            messages=[{"role": "user", "content": summary_prompt}],
            temperature=0.5,
            max_tokens=st.session_state.model_max_tokens,
        )
        new_summary = response.choices[0].message.content.strip()

        if st.session_state.conversation_summary:
            st.session_state.conversation_summary += "\n\n" + new_summary
        else:
            st.session_state.conversation_summary = new_summary

        save_conversation_summary(st.session_state.conversation_summary)

        st.session_state.conv_history = []
    except Exception as e:
        logger.error(f"Failed to generate summary: {e}")
        st.warning(f"Failed to generate summary: {e}")

# ========== AI 回复函数 ==========
def get_ai_reply(user_input):
    logger.info(f"User input: {user_input[:100]}...")
    
    # 如果 Quiz 处于活跃状态，处理 Quiz 答案
    if st.session_state.quiz_active and st.session_state.current_quiz:
        questions = st.session_state.current_quiz.get("questions", [])
        
        # 检查用户是否在请求答案
        if user_input.lower().strip() in ["give me answers", "show answers", "give answers", "show me the answers"]:
            reply = "I'd be happy to help! Let's go through the answers together. Which question would you like me to explain first?"
            st.session_state.quiz_active = False
            st.session_state.current_quiz = None
            st.session_state.quiz_answers = {}
            st.session_state.quiz_asked = False
            
            st.session_state.messages.append({"role": "assistant", "content": reply})
            st.session_state.conv_history.append({"role": "assistant", "content": reply})
            
            try:
                audio_bytes, fmt = text_to_speech(reply)
                if audio_bytes:
                    st.session_state.pending_tts = (audio_bytes, fmt)
            except Exception as e:
                logger.error(f"TTS error: {e}")
            return
        
        # ========== 解析用户答案（支持多行）==========
        lines = user_input.split('\n')
        all_matches = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            match = re.match(r'^(\d+)[\.\:\-\s]+(.+)$', line)
            if match:
                q_num = int(match.group(1))
                ans = match.group(2).strip()
                all_matches.append((q_num, ans))
        
        if all_matches:
            # 有多行格式的回答，直接填充所有答案
            for q_num, ans in all_matches:
                if 1 <= q_num <= len(questions):
                    st.session_state.quiz_answers[q_num] = ans
        else:
            # 尝试匹配单行 "1. A, 2. B, 3. C" 格式
            answer_pattern = re.findall(r'(\d+)[\.\:\-\s]+([^,]+?)(?=\s*\d+[\.\:\-\s]|$)', user_input)
            if answer_pattern:
                for num_str, ans in answer_pattern:
                    q_num = int(num_str)
                    if 1 <= q_num <= len(questions):
                        st.session_state.quiz_answers[q_num] = ans.strip()
            else:
                # 没有编号，当作顺序回答
                current_q_num = len(st.session_state.quiz_answers) + 1
                if current_q_num <= len(questions):
                    st.session_state.quiz_answers[current_q_num] = user_input
        
        # ========== 关键修复：每次输入后都检查是否已完成 ==========
        # 如果已经收集到所有答案，立即评估
        if len(st.session_state.quiz_answers) >= len(questions):
            # 构建问题和答案列表
            qa_list = []
            for i, q in enumerate(questions):
                user_ans = st.session_state.quiz_answers.get(i+1, "No answer")
                qa_list.append(f"Question {i+1}: {q}\nYour answer: {user_ans}")
            
            # 根据模式选择评估提示
            if st.session_state.language == "Chinese":
                eval_prompt = f"""You are a language teacher. Evaluate these quiz answers. Be GENEROUS in your evaluation.

CRITICAL RULES:
- Multiple choice: Accept the letter (A, B, C, D) OR the full text. Any answer that indicates the correct option is CORRECT.
- Fill in the blank: Accept ANY word that makes the sentence grammatically correct and semantically meaningful. If multiple answers are possible, ALL are CORRECT. Only mark incorrect if the word makes no sense or creates a grammar error.- Translation: Accept if the meaning is preserved. Wording can vary. Even if it's not exactly the same, if the idea is conveyed, it's CORRECT.
- Error correction: Accept if the error is fixed. The fix doesn't have to be exactly the same as expected.
- Sentence making: Accept ANY grammatically correct sentence that uses all the given words. Order and wording can vary.

For incorrect answers, DO NOT give the correct answer directly. Instead:
1. Briefly explain why it's not ideal
2. Ask a Socratic question to help

CRITICAL: Must give the correct answers when the user asks for them (e.g., "give me answers", "show answers").

Quiz Questions and Answers:
{chr(10).join(qa_list)}

Return exactly this format:
1: [✅/❌] - [if ❌: brief explanation + Socratic question]
2: [✅/❌] - [if ❌: brief explanation + Socratic question]
3: [✅/❌] - [if ❌: brief explanation + Socratic question]
4: [✅/❌] - [if ❌: brief explanation + Socratic question]
5: [✅/❌] - [if ❌: brief explanation + Socratic question]
Total: X/5"""
            elif st.session_state.language == "English":
                eval_prompt = f"""You are a language teacher. Evaluate these quiz answers. Be GENEROUS in your evaluation.

CRITICAL RULES:
- Multiple choice: Accept the letter (A, B, C, D) OR the full text. Any answer that indicates the correct option is CORRECT.
- Fill in the blank: Accept ANY word that makes the sentence grammatically correct and semantically meaningful. If multiple answers are possible, ALL are CORRECT. Only mark incorrect if the word makes no sense or creates a grammar error.- Translation: Accept if the meaning is preserved. Wording can vary. Even if it's not exactly the same, if the idea is conveyed, it's CORRECT.
- Error correction: Accept if the error is fixed. The fix doesn't have to be exactly the same as expected.
- Sentence making: Accept ANY grammatically correct sentence that uses all the given words. Order and wording can vary.

For incorrect answers, DO NOT give the correct answer directly. Instead:
1. Briefly explain why it's not ideal
2. Ask a Socratic question to help

CRITICAL: Must give the correct answers when the user asks for them (e.g., "give me answers", "show answers").

Quiz Questions and Answers:
{chr(10).join(qa_list)}

Return exactly this format:
1: [✅/❌] - [if ❌: brief explanation + Socratic question]
2: [✅/❌] - [if ❌: brief explanation + Socratic question]
3: [✅/❌] - [if ❌: brief explanation + Socratic question]
4: [✅/❌] - [if ❌: brief explanation + Socratic question]
5: [✅/❌] - [if ❌: brief explanation + Socratic question]
Total: X/5"""
            else:
                eval_prompt = f"""You are a language teacher. Evaluate these quiz answers. Be GENEROUS in your evaluation.

CRITICAL RULES:
- Multiple choice: Accept the letter (A, B, C, D) OR the full text. Any answer that indicates the correct option is CORRECT.
- Fill in the blank: Accept ANY word that makes the sentence grammatically correct and semantically meaningful. If multiple answers are possible, ALL are CORRECT. Only mark incorrect if the word makes no sense or creates a grammar error.- Translation: Accept if the meaning is preserved. Wording can vary. Even if it's not exactly the same, if the idea is conveyed, it's CORRECT.
- Error correction: Accept if the error is fixed. The fix doesn't have to be exactly the same as expected.
- Sentence making: Accept ANY grammatically correct sentence that uses all the given words. Order and wording can vary.

For incorrect answers, DO NOT give the correct answer directly. Instead:
1. Briefly explain why it's not ideal
2. Ask a Socratic question to help

CRITICAL: Must give the correct answers when the user asks for them (e.g., "give me answers", "show answers").

Quiz Questions and Answers:
{chr(10).join(qa_list)}

Return exactly this format:
1: [✅/❌] - [if ❌: brief explanation + Socratic question]
2: [✅/❌] - [if ❌: brief explanation + Socratic question]
3: [✅/❌] - [if ❌: brief explanation + Socratic question]
4: [✅/❌] - [if ❌: brief explanation + Socratic question]
5: [✅/❌] - [if ❌: brief explanation + Socratic question]
Total: X/5"""
            
            try:
                eval_response = client.chat.completions.create(
                    model=st.session_state.model_name,
                    messages=[{"role": "user", "content": eval_prompt}],
                    temperature=0.3,
                    max_tokens=st.session_state.model_max_tokens,
                )
                evaluation = eval_response.choices[0].message.content.strip()
                
                # 保存到 feedback.md
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                entry = f"""
## Quiz Record - {timestamp}

**Topic:** {st.session_state.current_quiz.get("topic", "General")}
**Mode:** {st.session_state.language}

### Quiz:
{st.session_state.current_quiz.get("quiz_text", "No quiz text")}

### User Answers:
{chr(10).join([f"{i+1}. {st.session_state.quiz_answers.get(i+1, 'No answer')}" for i in range(len(questions))])}

### Evaluation:
{evaluation}

---
"""
                with open("feedback.md", "a", encoding="utf-8") as f:
                    f.write(entry)
                save_to_github("feedback.md", entry, f"Add quiz record - {timestamp}")      
                reply = evaluation + "\n\nGreat job! Let me know if you have any questions about the feedback."
                
                # 重置 quiz 状态
                st.session_state.quiz_active = False
                st.session_state.current_quiz = None
                st.session_state.quiz_answers = {}
                st.session_state.quiz_asked = False
                
                st.session_state.messages.append({"role": "assistant", "content": reply})
                st.session_state.conv_history.append({"role": "assistant", "content": reply})
                
                try:
                    audio_bytes, fmt = text_to_speech(reply)
                    if audio_bytes:
                        st.session_state.pending_tts = (audio_bytes, fmt)
                except Exception as e:
                    logger.error(f"TTS error: {e}")
                return
                
            except Exception as e:
                logger.error(f"Evaluation error: {e}")
                reply = f"Evaluation failed: {str(e)}\n\nPlease try again."
                st.session_state.messages.append({"role": "assistant", "content": reply})
                st.session_state.conv_history.append({"role": "assistant", "content": reply})
                try:
                    audio_bytes, fmt = text_to_speech(reply)
                    if audio_bytes:
                        st.session_state.pending_tts = (audio_bytes, fmt)
                except Exception as e:
                    logger.error(f"TTS error: {e}")
                return
        else:
            # 还没答完所有问题，提示下一个
            answered = set(st.session_state.quiz_answers.keys())
            next_q_num = 1
            while next_q_num in answered:
                next_q_num += 1
            
            if next_q_num <= len(questions):
                current_q_text = questions[next_q_num - 1] if next_q_num - 1 < len(questions) else f"Question {next_q_num}"
                reply = f"Please answer question {next_q_num}: {current_q_text}\n\nUse format: '{next_q_num}. answer' (e.g., '1. A')"
            else:
                reply = f"Please answer the remaining questions."
            
            st.session_state.messages.append({"role": "assistant", "content": reply})
            st.session_state.conv_history.append({"role": "assistant", "content": reply})
            
            try:
                audio_bytes, fmt = text_to_speech(reply)
                if audio_bytes:
                    st.session_state.pending_tts = (audio_bytes, fmt)
            except Exception as e:
                logger.error(f"TTS error: {e}")
            return
    
 
    # ========== 正常处理用户输入（非 Quiz 状态）==========
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
        summary_msg = {"role": "system", "content": f"[Previous conversation summary]\n{st.session_state.conversation_summary}"}
        base = 1
        if st.session_state.language:
            base += 1
        if full_page:
            base += 1
        context_msgs.insert(base, summary_msg)

    try:
        response = client.chat.completions.create(
            model=st.session_state.model_name,
            messages=context_msgs,
            temperature=0.7,
            max_tokens=st.session_state.model_max_tokens,
        )
        reply = response.choices[0].message.content.strip()
        logger.info(f"AI reply: {reply[:100]}...")
    except Exception as e:
        logger.error(f"AI reply error: {e}")
        reply = f"[Error: {e}]"

    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.session_state.conv_history.append({"role": "assistant", "content": reply})

    try:
        audio_bytes, fmt = text_to_speech(reply)
        if audio_bytes:
            st.session_state.pending_tts = (audio_bytes, fmt)
    except Exception as e:
        logger.error(f"TTS error in get_ai_reply: {e}")

    if st.session_state.user_msg_count % 5 == 0 and st.session_state.user_msg_count > 0:
        generate_and_save_summary()


# ========== AI 回复函数（带图片）==========
def get_ai_reply_with_image(user_input, image_bytes):
    logger.info(f"User input with image: {user_input[:100]}...")
    
    # 将图片转换为 base64
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
    mime_type = st.session_state.get("image_mime", "image/jpeg")
    image_url = f"data:{mime_type};base64,{image_base64}"
    
    # 构建消息
    context_msgs = st.session_state.messages.copy()
    
    # 添加当前页面内容
    full_page = get_current_page_full_content()
    if full_page:
        context_msgs.insert(1, {"role": "system", "content": full_page})
    
    # 添加语言信息
    if st.session_state.language:
        lang_msg = {"role": "system", "content": f"The user is currently learning {st.session_state.language}."}
        context_msgs.insert(1, lang_msg)
    
    # 添加对话总结
    if st.session_state.conversation_summary:
        summary_msg = {"role": "system", "content": f"[Previous conversation summary]\n{st.session_state.conversation_summary}"}
        base = 1
        if st.session_state.language:
            base += 1
        if full_page:
            base += 1
        context_msgs.insert(base, summary_msg)
    
    # 添加带图片的用户消息
    context_msgs.append({
        "role": "user",
        "content": [
            {"type": "text", "text": user_input},
            {"type": "image_url", "image_url": {"url": image_url}}
        ]
    })
    
    try:
        response = client.chat.completions.create(
            model=st.session_state.model_name,
            messages=context_msgs,
            temperature=0.7,
            max_tokens=st.session_state.model_max_tokens,
        )
        reply = response.choices[0].message.content.strip()
        logger.info(f"AI reply with image: {reply[:100]}...")
    except Exception as e:
        logger.error(f"AI reply error with image: {e}")
        reply = f"[Error: {e}]"
    
    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.session_state.conv_history.append({"role": "assistant", "content": reply})
    
    # TTS
    try:
        audio_bytes, fmt = text_to_speech(reply)
        if audio_bytes:
            st.session_state.pending_tts = (audio_bytes, fmt)
    except Exception as e:
        logger.error(f"TTS error in get_ai_reply_with_image: {e}")


# ========== OCR 处理函数（带详细日志）==========
def process_ocr_images(uploaded_files):
    """处理图片OCR - 带详细日志"""
    if not uploaded_files:
        return None
    
    logger.info(f"=== OCR图片处理开始 ===")
    logger.info(f"上传图片数量: {len(uploaded_files)}")
    
    # 记录每张图片信息
    for idx, f in enumerate(uploaded_files):
        logger.info(f"  图片 {idx+1}: {f.name}, 大小: {f.size} bytes, 类型: {f.type}")
    
    # 限制最多300张
    if len(uploaded_files) > 300:
        logger.warning(f"图片数量超过300，只处理前300张")
        uploaded_files = uploaded_files[:300]
    
    # 准备图片数据，记录读取时间
    image_list = []
    for f in uploaded_files:
        read_start = time.time()
        img_bytes = f.read()
        read_time = time.time() - read_start
        logger.info(f"读取图片 {f.name}: {read_time:.2f}秒, 大小: {len(img_bytes)/1024:.1f}KB")
        image_list.append((img_bytes, f.name))
    
    # 进度显示
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    def update_progress(current, total, filename, status, preview):
        progress_bar.progress(current / total)
        status_text.text(f"Processing: {current}/{total} - {filename} - {status}")
        logger.info(f"OCR进度: {current}/{total} - {filename} - {status}")
        if preview:
            logger.debug(f"  预览: {preview[:100]}...")
    
    # 执行OCR，记录总时间
    ocr_start = time.time()
    logger.info("开始调用 ocr_images_batch...")
    
    try:
        results = ocr_images_batch(image_list, IMAGE_OCR_CONFIG, progress_callback=update_progress)
        ocr_time = time.time() - ocr_start
        logger.info(f"ocr_images_batch 完成，耗时: {ocr_time:.2f}秒")
        
        # 记录结果统计
        if results:
            success_count = sum(1 for r in results if r[1] == "success")
            failed_count = len(results) - success_count
            logger.info(f"OCR结果: 成功={success_count}, 失败={failed_count}")
            
            for filename, status, text in results:
                if status == "success":
                    logger.info(f"  ✅ {filename}: {len(text)} 字符")
                else:
                    logger.error(f"  ❌ {filename}: 识别失败")
        else:
            logger.warning("OCR结果为空")
            
    except Exception as e:
        logger.error(f"OCR处理异常: {e}", exc_info=True)
        results = None
    
    progress_bar.empty()
    status_text.empty()
    
    logger.info(f"=== OCR图片处理结束 ===")
    return results


def process_ocr_pdf(uploaded_pdf):
    """处理PDF OCR - 带详细日志"""
    if not uploaded_pdf:
        return None
    
    logger.info(f"=== OCR PDF处理开始 ===")
    logger.info(f"PDF文件: {uploaded_pdf.name}, 大小: {uploaded_pdf.size} bytes")
    
    read_start = time.time()
    pdf_bytes = uploaded_pdf.read()
    read_time = time.time() - read_start
    logger.info(f"读取PDF耗时: {read_time:.2f}秒, 大小: {len(pdf_bytes)/1024:.1f}KB")
    
    # 进度显示
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    def update_progress(current, total, message):
        progress_bar.progress(current / total)
        status_text.text(f"OCR: {message}")
        logger.info(f"PDF OCR进度: {current}/{total} - {message}")
    
    ocr_start = time.time()
    logger.info("开始调用 ocr_pdf...")
    
    try:
        status, text = ocr_pdf(
            pdf_bytes,
            uploaded_pdf.name,
            PDF_OCR_CONFIG["cookie"],
            PDF_OCR_CONFIG["x_auth_token"],
            PDF_OCR_CONFIG["x_auth_uuid"],
            progress_callback=update_progress,
            config=PDF_OCR_CONFIG
        )
        
        ocr_time = time.time() - ocr_start
        logger.info(f"ocr_pdf 完成，耗时: {ocr_time:.2f}秒")
        
        if status == "success":
            logger.info(f"PDF OCR成功，文本长度: {len(text)} 字符")
        else:
            logger.error(f"PDF OCR失败，状态: {status}")
            
    except Exception as e:
        logger.error(f"PDF OCR异常: {e}", exc_info=True)
        status = "failed"
        text = None
    
    progress_bar.empty()
    status_text.empty()
    
    logger.info(f"=== OCR PDF处理结束 ===")
    
    if status == "success":
        return text
    else:
        return None

# ---------- CSS样式（修改版）----------
st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@200;300;400;500;600;700;800&display=swap');

    /* 主应用样式 */
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
        background-color: rgba(0, 0, 0, 0.7) !important;
        background-blend-mode: overlay !important;
    }}

    /* 隐藏不必要的Streamlit元素 */
    header[data-testid="stHeader"] {{
        display: none !important;
    }}
    .stDeployButton {{
        display: none !important;
    }}
    #MainMenu {{
        display: none !important;
    }}
    footer {{
        display: none !important;
    }}

    /* ========== 侧边栏样式 ========== */
    section[data-testid="stSidebar"] {{
        background-color: transparent !important;
        border-right: none !important;
        backdrop-filter: none !important;
        transition: width 0.3s ease !important;
        z-index: 100 !important;
    }}

    /* 展开状态 */
    section[data-testid="stSidebar"][aria-expanded="true"] {{
        width: 400px !important;
        min-width: 400px !important;
    }}

    /* 折叠状态 - 只显示一个按钮区域 */
    section[data-testid="stSidebar"][aria-expanded="false"] {{
        width: 60px !important;
        min-width: 60px !important;
        overflow: visible !important;
    }}

    /* 折叠时隐藏内容，但保留按钮区域 */
    section[data-testid="stSidebar"][aria-expanded="false"] > div:not([data-testid="stSidebarHeader"]) {{
        display: none !important;
    }}

    /* 自定义折叠按钮样式 */
    section[data-testid="stSidebar"] button[data-testid="stSidebarCollapseButton"] {{
        background-color: rgba(102, 126, 234, 0.8) !important;
        border-radius: 8px !important;
        margin: 10px !important;
        padding: 8px !important;
        width: 40px !important;
        height: 40px !important;
        cursor: pointer !important;
        transition: all 0.3s ease !important;
        border: 1px solid rgba(255, 255, 255, 0.3) !important;
        color: white !important;
        font-size: 20px !important;
        z-index: 101 !important;
    }}

    section[data-testid="stSidebar"] button[data-testid="stSidebarCollapseButton"]:hover {{
        background-color: rgba(102, 126, 234, 1) !important;
        transform: scale(1.05) !important;
    }}

    /* 折叠状态下按钮旋转 */
    section[data-testid="stSidebar"][aria-expanded="false"] button[data-testid="stSidebarCollapseButton"] {{
        transform: rotate(180deg) !important;
        position: relative !important;
        left: 10px !important;
    }}

    /* 确保侧边栏中的文本可见 */
    section[data-testid="stSidebar"] * {{
        color: #b0b0b0 !important;
    }}

    /* 侧边栏输入框、选择框、按钮 - 黑色半透明 */
    section[data-testid="stSidebar"] input,
    section[data-testid="stSidebar"] textarea,
    section[data-testid="stSidebar"] select,
    section[data-testid="stSidebar"] div[data-baseweb="select"] > div,
    section[data-testid="stSidebar"] [data-baseweb="select"] {{
        background-color: rgba(0, 0, 0, 0.1) !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        border-radius: 8px !important;
        color: #b0b0b0 !important;
    }}

    /* 侧边栏按钮 - 黑色半透明 */
    section[data-testid="stSidebar"] button {{
        background-color: rgba(0, 0, 0, 0.15) !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        border-radius: 8px !important;
        color: #b0b0b0 !important;
    }}

    /* 下拉框选项文字颜色 */
    section[data-testid="stSidebar"] select option {{
        background-color: #1a1a2e !important;
        color: #b0b0b0 !important;
    }}

    /* 其他原有样式保持不变 */
    .breadcrumb {{
        background-color: rgba(255, 255, 255, 0.1);
        padding: 12px 20px;
        border-radius: 8px;
        margin-bottom: 20px;
        font-family: 'Manrope', sans-serif;
        font-size: 18px;
        color: #ffffff !important;
        font-weight: 700;
        border: 1px solid rgba(255, 255, 255, 0.2);
    }}

    button[kind="primary"],
    .stButton button {{
        background-color: rgba(255, 255, 255, 0.2) !important;
        color: #ffffff !important;
        font-family: 'Manrope', sans-serif !important;
        font-size: 100px !important;
        font-weight: 800 !important;
        padding: 30px !important;
        transition: all 0.3s ease !important;
        border: 1px solid rgba(255, 255, 255, 0.3) !important;
        border-radius: 12px !important;
    }}

    .stButton button > div {{
        font-size: 92px !important;
        font-weight: 800 !important;
    }}

    .stButton button:hover {{
        background-color: rgba(255, 255, 255, 0.3) !important;
        transform: translateY(-2px);
    }}

    h1 {{
        color: #ffffff !important;
        font-size: 300px;
        font-weight: 800;
        text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.3);
    }}

    @media (max-width: 768px) {{
        h1 {{
            font-size: 96px;
        }}
    }}

    h2, h3 {{
        color: #ffffff !important;
        text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.3);
    }}

    p, div, span, label {{
        color: #ffffff !important;
    }}

    div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlock"] {{
        background-color: rgba(255, 255, 255, 0.1);
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 15px;
        border: 1px solid rgba(255, 255, 255, 0.2);
    }}

    .word-card {{
        background-color: rgba(255, 255, 255, 0.1);
        border-radius: 12px;
        padding: 20px;
        margin: 10px 0;
        border: 1px solid rgba(255, 255, 255, 0.2);
        transition: all 0.3s ease;
    }}

    .word-card:hover {{
        transform: translateY(-2px);
        background-color: rgba(255, 255, 255, 0.15);
    }}
</style>
""", unsafe_allow_html=True)

# ========== 侧边栏 ==========
with st.sidebar:
    # ========== 聊天区域 ==========
    # 聊天消息显示区域
    chat_messages = st.container(height=500)
    with chat_messages:
        for msg in st.session_state.messages:
            if msg["role"] == "system":
                continue
            if msg["role"] == "user":
                st.write(f"**You:** {msg['content']}")
            else:
                st.write(f"**AI:** {msg['content']}")
    
    # 第一行：语音 + 文本输入框
    col_voice, col_text = st.columns([1, 4])
    with col_voice:
        audio_in = st.audio_input("", key="audio_in", label_visibility="collapsed")
        if audio_in is not None:
            audio_id = f"{audio_in.name}_{audio_in.size}"
            if audio_id != st.session_state.get("last_audio", ""):
                st.session_state.last_audio = audio_id
                audio_bytes = audio_in.read()
                if audio_bytes:
                    with st.spinner(""):
                        transcript = transcribe_audio(audio_bytes)
                    if transcript:
                        get_ai_reply(transcript)
                        st.rerun()
    with col_text:
        user_msg = st.chat_input("")
        if user_msg:
            get_ai_reply(user_msg)
            st.rerun()
    
    # 第二行：Clear + Clear Search
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Clear", key="clear_btn", use_container_width=True):
            st.session_state.messages = [{"role": "system", "content": system_prompt}]
            st.session_state.conversation_summary = ""
            st.session_state.conv_history = []
            st.session_state.user_msg_count = 0
            st.rerun()
    with col_b:
        if st.button("Clear Search", key="clear_search_btn", use_container_width=True):
            st.session_state.search_keyword = ""
            st.session_state.search_results = []
            st.rerun()
    
    # 第三行：Generate Quiz + Run OCR
    col_c, col_d = st.columns(2)
    with col_c:
        if st.button("Generate Quiz", key="quiz_btn_small", use_container_width=True):
            full_page = get_current_page_full_content()
            topic = "general"
            if full_page:
                sec_match = re.search(r"Section: (.+)", full_page)
                if sec_match:
                    topic = sec_match.group(1)
            quiz_text = generate_quiz(topic, full_page)
            if quiz_text:
                st.session_state.quiz_active = True
                questions = []
                for line in quiz_text.split('\n'):
                    line = line.strip()
                    if re.match(r'^\d+\.', line):
                        questions.append(line)
                st.session_state.current_quiz = {
                    "questions": questions,
                    "quiz_text": quiz_text,
                    "topic": topic
                }
                st.session_state.quiz_answers = {}
                st.session_state.quiz_asked = True
                reply = f"Here's a quiz on {topic}:\n\n{quiz_text}\n\nPlease answer the questions. Use format: '1. A, 2. B, 3. C'"
                st.session_state.messages.append({"role": "assistant", "content": reply})
                st.session_state.conv_history.append({"role": "assistant", "content": reply})
                try:
                    audio_bytes, fmt = text_to_speech(reply)
                    if audio_bytes:
                        st.session_state.pending_tts = (audio_bytes, fmt)
                except Exception as e:
                    logger.error(f"TTS error: {e}")
                st.rerun()
    with col_d:
        if st.button("Run OCR", key="ocr_run_small", use_container_width=True):
            img_files = st.session_state.get("ocr_imgs", [])
            pdf_file = st.session_state.get("ocr_pdf", None)
            zip_file = st.session_state.get("ocr_zip", None)
            ocr_results = []
            if img_files:
                with st.spinner(""):
                    results = process_ocr_images(img_files)
                    if results:
                        ocr_results.extend(results)
            if pdf_file:
                with st.spinner(""):
                    text = process_ocr_pdf(pdf_file)
                    if text:
                        ocr_results.append(("PDF", "success", text))
            if zip_file:
                with st.spinner(""):
                    zip_bytes = zip_file.read()
                    with tempfile.TemporaryDirectory() as tmp:
                        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
                            zip_imgs = []
                            for info in zf.infolist():
                                if not info.is_dir():
                                    ext = os.path.splitext(info.filename)[1].lower()
                                    if ext in ['.jpg','.jpeg','.png','.bmp','.webp','.tiff']:
                                        img_bytes = zf.read(info.filename)
                                        zip_imgs.append((img_bytes, os.path.basename(info.filename)))
                            if zip_imgs:
                                results = ocr_images_batch(zip_imgs, IMAGE_OCR_CONFIG)
                                ocr_results.extend(results)
            if ocr_results:
                result_text = format_results_as_text(ocr_results)
                st.session_state.ocr_result_text = result_text
                st.rerun()
    
    # 如果有OCR结果，显示
    if st.session_state.get("ocr_result_text"):
        st.text_area("OCR Results", st.session_state.ocr_result_text, height=150)
        col_dl, col_send = st.columns(2)
        with col_dl:
            st.download_button("Download", st.session_state.ocr_result_text, file_name=f"ocr_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt", key="ocr_dl_small")
        with col_send:
            if st.button("Send to AI", key="ocr_send_small"):
                get_ai_reply(f"Please analyze these OCR results:\n\n{st.session_state.ocr_result_text}")
                st.session_state.ocr_result_text = None
                st.rerun()
    
    # ========== 设置工具区域 ==========
    # Mode
    mode_opts = ["Chinese", "English", "NEMT & CET"]
    cur_idx = 0
    if st.session_state.language == "English":
        cur_idx = 1
    elif st.session_state.language == "NEMT & CET":
        cur_idx = 2
    
    new_lang = st.selectbox("Mode", mode_opts, index=cur_idx, key="mode_select")
    if new_lang != st.session_state.language:
        st.session_state.language = new_lang
        if new_lang == "NEMT & CET":
            st.session_state.current_mode = "nemt_cet"
            st.session_state.level = None
            st.session_state.path = []
            st.session_state.selected_nemt_cet = None
            st.session_state.nemt_cet_path = []
        else:
            st.session_state.current_mode = "textbook"
            levels_data = load_level_data(st.session_state.language)
            st.session_state.level = None
            st.session_state.path = []
            st.session_state.selected_nemt_cet = None
            st.session_state.nemt_cet_path = []
        st.session_state.messages = [{"role": "system", "content": system_prompt}]
        st.session_state.quiz_active = False
        st.session_state.current_quiz = None
        st.session_state.quiz_answers = {}
        st.session_state.quiz_asked = False
        st.rerun()
    
    # Search
    scope_opts = ["Global", "Local"]
    scope_idx = 0 if st.session_state.search_scope == "global" else 1
    new_scope = st.selectbox("Search in", scope_opts, index=scope_idx, key="scope_select")
    if new_scope == "Global":
        new_scope_val = "global"
    else:
        new_scope_val = "local"
    if new_scope_val != st.session_state.search_scope:
        st.session_state.search_scope = new_scope_val
        st.session_state.search_results = []
        st.session_state.search_keyword = ""
        st.rerun()
    
    search_input = st.text_input("Search", value=st.session_state.search_keyword, key="search_input")
    if st.button("Search", key="search_btn"):
        st.session_state.search_keyword = search_input
        if search_input.strip():
            if st.session_state.search_scope == "global":
                st.session_state.search_results = global_search(search_input)
            else:
                st.session_state.search_results = local_search(search_input)
        else:
            st.session_state.search_results = []
        st.rerun()
    
    # Model
    model_names = list(AVAILABLE_MODELS.keys())
    cur_model_idx = model_names.index(st.session_state.selected_model)
    new_model = st.selectbox("Model", model_names, index=cur_model_idx, key="model_select")
    if new_model != st.session_state.selected_model:
        st.session_state.selected_model = new_model
        st.session_state.model_name = AVAILABLE_MODELS[new_model]["id"]
        st.session_state.model_max_tokens = AVAILABLE_MODELS[new_model]["max_tokens"]
        st.rerun()
    
    # OCR 文件上传
    img_files = st.file_uploader("Images", type=["jpg","jpeg","png","bmp","webp","tiff"], accept_multiple_files=True, key="ocr_imgs")
    pdf_file = st.file_uploader("PDF", type=["pdf"], key="ocr_pdf")
    zip_file = st.file_uploader("ZIP", type=["zip"], key="ocr_zip")

# TTS 音频播放
if st.session_state.pending_tts:
    audio_bytes, fmt = st.session_state.pending_tts
    st.audio(audio_bytes, format=fmt, autoplay=True)
    st.session_state.pending_tts = None

# ========== 主界面：内容显示和聊天 ==========
# 显示搜索结果
if st.session_state.search_keyword and st.session_state.search_results:
    st.markdown(f"### Search Results for '{st.session_state.search_keyword}'")
    st.markdown(f"Found {len(st.session_state.search_results)} result(s)")
    
    for idx, res in enumerate(st.session_state.search_results):
        if "path" in res and res["path"]:
            path_list = []
            for p in res["path"]:
                p_clean = str(p).split("[")[0] if isinstance(p, str) else str(p)
                if p_clean and p_clean not in ["LEVEL_I", "LEVEL_II", "LEVEL_III"]:
                    if not p_clean.isdigit():
                        path_list.append(p_clean)
            path_str = " > ".join(path_list) if path_list else "Root"
        else:
            path_str = "Unknown"
        
        if res.get("source") == "textbook":
            source_info = f"Level {res.get('level', '?')}"
        elif res.get("source") == "nemt_cet":
            source_info = res.get('exam', 'Exam')
        else:
            source_info = "Content"
        
        content_preview = res["content"].replace("\n", " ")[:120]
        if len(res["content"]) > 120:
            content_preview += "..."
        
        button_label = f"{res.get('type', 'Content')} | {source_info}\n\n{content_preview}\n\nPath: {path_str}"
        
        if st.button(
            button_label,
            key=f"search_result_{idx}_{hash(str(res))}",
            use_container_width=True
        ):
            if res.get("source") == "textbook" and res.get("level"):
                st.session_state.current_mode = "textbook"
                st.session_state.level = res["level"]
                st.session_state.path = [f"LEVEL_{['I','II','III'][res['level']-1]}"]
                st.session_state.search_keyword = ""
                st.session_state.search_results = []
                st.rerun()
            elif res.get("source") == "nemt_cet" and res.get("exam"):
                st.session_state.current_mode = "nemt_cet"
                st.session_state.selected_nemt_cet = res["exam"]
                st.session_state.nemt_cet_path = []
                st.session_state.search_keyword = ""
                st.session_state.search_results = []
                st.rerun()
    
    st.markdown("---")

elif st.session_state.search_keyword:
    st.info(f"No results found for '{st.session_state.search_keyword}'.")

# 导航和卡片显示
st.title("TEXTBOOK ASSISTANT")

# 根据语言选择器显示不同的主界面
if st.session_state.language == "NEMT & CET":
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("TEM-8", use_container_width=True):
            st.session_state.current_mode = "nemt_cet"
            st.session_state.selected_nemt_cet = "TEM-8"
            st.session_state.nemt_cet_path = []
            st.session_state.level = None
            st.session_state.path = []
            st.rerun()
    with col2:
        if st.button("NEMT", use_container_width=True):
            st.session_state.current_mode = "nemt_cet"
            st.session_state.selected_nemt_cet = "NEMT"
            st.session_state.nemt_cet_path = []
            st.session_state.level = None
            st.session_state.path = []
            st.rerun()
    with col3:
        if st.button("CET-46", use_container_width=True):
            st.session_state.current_mode = "nemt_cet"
            st.session_state.selected_nemt_cet = "CET-46"
            st.session_state.nemt_cet_path = []
            st.session_state.level = None
            st.session_state.path = []
            st.rerun()
else:
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Level 1", use_container_width=True):
            st.session_state.current_mode = "textbook"
            st.session_state.level = 1
            st.session_state.path = ["LEVEL_I"]
            st.rerun()
    with col2:
        if st.button("Level 2", use_container_width=True):
            st.session_state.current_mode = "textbook"
            st.session_state.level = 2
            st.session_state.path = ["LEVEL_II"]
            st.rerun()
    with col3:
        if st.button("Level 3", use_container_width=True):
            st.session_state.current_mode = "textbook"
            st.session_state.level = 3
            st.session_state.path = ["LEVEL_III"]
            st.rerun()

# 如果当前在 textbook 模式
if st.session_state.current_mode == "textbook":
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
            current_lang = st.session_state.language
            other_lang = "English" if current_lang == "Chinese" else "Chinese"

            other_levels_data = load_level_data(other_lang)
            other_node = other_levels_data[f"Level {st.session_state.level}"]
            for key in st.session_state.path:
                other_node = other_node.get(key, {})
                if not other_node:
                    other_node = None
                    break

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
                        key = f"example_{idx}"
                        other_ex = None
                        if other_node and "examples" in other_node and len(other_node["examples"]) > idx:
                            other_ex = other_node["examples"][idx]

                        flipped = st.session_state.get("flip_states", {}).get(key, False)

                        if flipped:
                            display_content = other_ex if other_ex else "(Translation not available)"
                        else:
                            display_content = ex

                        if st.button(display_content, key=f"btn_{key}", use_container_width=True):
                            if "flip_states" not in st.session_state:
                                st.session_state.flip_states = {}
                            st.session_state.flip_states[key] = not flipped
                            st.rerun()

            if "vocabulary" in node and node["vocabulary"]:
                st.markdown("### Vocabulary")
                cols = st.columns(3)
                for idx, item in enumerate(node["vocabulary"]):
                    with cols[idx % 3]:
                        parts = item.rsplit(" ", 1)
                        word = parts[0]
                        pinyin = parts[1] if len(parts) > 1 else ""

                        other_item = None
                        if other_node and "vocabulary" in other_node and len(other_node["vocabulary"]) > idx:
                            other_item = other_node["vocabulary"][idx]
                        other_parts = other_item.rsplit(" ", 1) if other_item else ["", ""]
                        other_word = other_parts[0]
                        other_pron = other_parts[1] if len(other_parts) > 1 else ""

                        key = f"vocab_{idx}"
                        flipped = st.session_state.get("flip_states", {}).get(key, False)

                        if flipped:
                            display_content = other_word
                            if other_pron:
                                display_content += f"\n{other_pron}"
                        else:
                            display_content = word
                            if pinyin:
                                display_content += f"\n{pinyin}"

                        if st.button(display_content, key=f"btn_{key}", use_container_width=True):
                            if "flip_states" not in st.session_state:
                                st.session_state.flip_states = {}
                            st.session_state.flip_states[key] = not flipped
                            st.rerun()

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
                                st.session_state.flip_states = {}
                                st.rerun()

        display_node(current_node)
        
        recommendations = get_page_recommendations()
        if recommendations:
            st.markdown("---")
            with st.container():
                st.markdown(recommendations, unsafe_allow_html=True)

# 如果当前在 NEMT & CET 模式
elif st.session_state.current_mode == "nemt_cet" and st.session_state.selected_nemt_cet:
    data = nemt_cet_data.get(st.session_state.selected_nemt_cet, {})
    
    if len(data) == 1 and st.session_state.selected_nemt_cet in data:
        data = data[st.session_state.selected_nemt_cet]
    
    if not st.session_state.nemt_cet_path:
        st.markdown(f"## {st.session_state.selected_nemt_cet}")
        
        sub_keys = sorted([k for k in data.keys() if isinstance(data[k], dict) and str(k).isdigit()], key=lambda x: int(x))
        
        if sub_keys:
            st.markdown("### Categories")
            cols = st.columns(2)
            for i, key in enumerate(sub_keys):
                with cols[i % 2]:
                    inner_dict = data[key]
                    if inner_dict and isinstance(inner_dict, dict):
                        dir_name = list(inner_dict.keys())[0] if inner_dict else f"Section {key}"
                    else:
                        dir_name = f"Section {key}"
                    if st.button(dir_name, key=f"nemt_dir_{key}", use_container_width=True):
                        st.session_state.nemt_cet_path.append(key)
                        st.rerun()
        else:
            st.info("No content available.")
    else:
        current_node = data
        for key in st.session_state.nemt_cet_path:
            current_node = current_node.get(key, {})
            if not current_node:
                st.error("Path error. Please go back.")
                st.stop()
        
        content_node = current_node
        if isinstance(content_node, dict):
            while len(content_node) == 1 and isinstance(list(content_node.values())[0], dict):
                content_node = list(content_node.values())[0]
        
        bread_parts = []
        temp_data = data
        for path_key in st.session_state.nemt_cet_path:
            temp_data = temp_data.get(path_key, {})
            if isinstance(temp_data, dict) and len(temp_data) > 0:
                inner_data = temp_data
                while len(inner_data) == 1 and isinstance(list(inner_data.values())[0], dict):
                    inner_data = list(inner_data.values())[0]
                if isinstance(inner_data, dict) and "name" in inner_data:
                    bread_parts.append(inner_data["name"])
                elif len(inner_data) > 0:
                    first_key = list(inner_data.keys())[0] if inner_data else ""
                    if first_key and first_key not in bread_parts:
                        bread_parts.append(first_key)
        
        bread = " > ".join(bread_parts)
        st.markdown(f"<div class='breadcrumb' style='font-size: 18px;'>{bread}</div>", unsafe_allow_html=True)
        
        if len(st.session_state.nemt_cet_path) > 0:
            col_back, col_spacer = st.columns([1, 5])
            with col_back:
                if st.button("← Back", key="nemt_back_button", use_container_width=True):
                    st.session_state.nemt_cet_path.pop()
                    st.rerun()
            st.markdown("<br>", unsafe_allow_html=True)
        
        if "name" in content_node:
            st.markdown(f"<h2 style='font-size: 48px; font-weight: 700; margin-bottom: 20px;'>{content_node['name']}</h2>", unsafe_allow_html=True)
        
        if "notes" in content_node and content_node["notes"]:
            with st.container(border=True):
                st.markdown(f"<div style='font-size: 20px; line-height: 1.6; padding: 15px;'>{content_node['notes']}</div>", unsafe_allow_html=True)
        
        if "words" in content_node and content_node["words"]:
            st.markdown("<h3 style='font-size: 36px; font-weight: 600; margin-top: 30px; margin-bottom: 20px;'>Words</h3>", unsafe_allow_html=True)
            
            if isinstance(content_node["words"], str):
                words_list = content_node["words"].split(" / ")
            elif isinstance(content_node["words"], list):
                words_list = content_node["words"]
            else:
                words_list = []
            
            if "translation_cache_nemt" not in st.session_state:
                st.session_state.translation_cache_nemt = {}
            
            target_lang = "Chinese"
            cols = st.columns(3)
            
            for idx, word_item in enumerate(words_list):
                if not word_item or not word_item.strip():
                    continue
                
                word = word_item.strip().split(" ", 1)[0]
                card_key = f"nemt_word_card_{idx}"
                flipped = st.session_state.get("flip_states", {}).get(card_key, False)
                
                with cols[idx % 3]:
                    if flipped:
                        cache_key = f"{word}_{target_lang}"
                        if cache_key in st.session_state.translation_cache_nemt:
                            translation = st.session_state.translation_cache_nemt[cache_key]
                        else:
                            with st.spinner(f"Translating {word}..."):
                                translation = translate_word(word, target_lang)
                                st.session_state.translation_cache_nemt[cache_key] = translation
                        display_content = translation
                    else:
                        display_content = word
                    
                    if not display_content or display_content.strip() == "":
                        display_content = word if not flipped else f"({word})"
                    
                    if st.button(display_content, key=f"btn_{card_key}", use_container_width=True):
                        if "flip_states" not in st.session_state:
                            st.session_state.flip_states = {}
                        st.session_state.flip_states[card_key] = not flipped
                        st.rerun()
        
        if "examples" in content_node and content_node["examples"]:
            st.markdown("<h3 style='font-size: 36px; font-weight: 600; margin-top: 30px; margin-bottom: 15px;'>Example Sentences</h3>", unsafe_allow_html=True)
            for ex in content_node["examples"]:
                st.markdown(f"<div style='font-size: 20px; padding: 8px 0;'>• {ex}</div>", unsafe_allow_html=True)
        
        sub_items = []
        for k, v in current_node.items():
            if isinstance(v, dict):
                if str(k).isdigit() or (isinstance(k, str) and k.replace(".", "").replace("-", "").isdigit()):
                    if len(v) > 0:
                        inner_v = v
                        while len(inner_v) == 1 and isinstance(list(inner_v.values())[0], dict):
                            inner_v = list(inner_v.values())[0]
                        if isinstance(inner_v, dict) and "name" in inner_v:
                            dir_name = inner_v["name"]
                        else:
                            dir_name = list(v.keys())[0] if v and isinstance(v, dict) else f"Section {k}"
                        sub_items.append((k, dir_name))
        
        if sub_items:
            st.markdown("<h3 style='font-size: 36px; font-weight: 600; margin-top: 30px; margin-bottom: 15px;'>Sections</h3>", unsafe_allow_html=True)
            cols = st.columns(2)
            for i, (num_key, dir_name) in enumerate(sub_items):
                with cols[i % 2]:
                    if st.button(dir_name, key=f"nemt_subdir_{num_key}", use_container_width=True):
                        st.session_state.nemt_cet_path.append(num_key)
                        st.rerun()
        
        recommendations = get_page_recommendations()
        if recommendations:
            st.markdown("---")
            with st.container():
                st.markdown(recommendations, unsafe_allow_html=True)


    # 输入区域：三列布局（Clear + 语音 + 文本输入）
    col_clear, col_voice, col_text = st.columns([1, 1, 4])

    with col_clear:
        if st.button("Clear Chat", key="clear_chat", use_container_width=True):
            st.session_state.messages = [m for m in st.session_state.messages if m["role"] == "system"]
            st.session_state.pending_tts = None
            st.session_state.last_audio_id = None
            st.session_state.conversation_summary = ""
            st.session_state.conv_history = []
            st.session_state.user_msg_count = 0
            st.session_state.quiz_active = False
            st.session_state.current_quiz = None
            st.session_state.quiz_answers = {}
            st.session_state.quiz_asked = False
            if os.path.exists("conversation_summary.txt"):
                os.remove("conversation_summary.txt")
            st.rerun()

    with col_voice:
        audio_input = st.audio_input("Voice Input", key="voice_input", label_visibility="collapsed")
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
        if prompt := st.chat_input("Type a message...", key="text_input"):
            with st.spinner("Thinking..."):
                if hasattr(st.session_state, 'uploaded_image'):
                    get_ai_reply_with_image(prompt, st.session_state.uploaded_image)
                    del st.session_state.uploaded_image
                else:
                    get_ai_reply(prompt)
            st.rerun()
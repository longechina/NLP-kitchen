# utils/data_loader.py
import json
import logging
import streamlit as st
from pathlib import Path

logger = logging.getLogger(__name__)

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

@st.cache_data
def load_level_data(language):
    levels = {}
    suffix = "_en" if language == "English" else ""
    for i in range(1, 4):
        try:
            filename = f"data/level{i}{suffix}.json"
            with open(filename, "r", encoding="utf-8") as f:
                levels[f"Level {i}"] = json.load(f)
        except FileNotFoundError:
            st.error(f"{filename} not found. Please ensure all level files exist.")
            st.stop()
    return levels

@st.cache_data
def load_nemt_cet_data():
    nemt_cet_data = {}
    files_to_load = ["data/TEM-8.json", "data/NEMT.json", "data/CET-46.json"]
    
    for filename in files_to_load:
        try:
            with open(filename, "r", encoding="utf-8") as f:
                key = filename.replace('data/', '').replace('.json', '')
                nemt_cet_data[key] = json.load(f)
                logger.info(f"Successfully loaded {filename}")
        except FileNotFoundError:
            key = filename.replace('data/', '').replace('.json', '')
            logger.warning(f"{filename} not found")
            nemt_cet_data[key] = {}
        except json.JSONDecodeError as e:
            key = filename.replace('data/', '').replace('.json', '')
            logger.error(f"JSON parse error in {filename}: {e}")
            nemt_cet_data[key] = {}
    
    return nemt_cet_data


# ========== NLP 教材加载函数 ==========
@st.cache_data
def load_nlp_textbook_data():
    """
    加载 data/nlp/ 目录下的所有 nlpX.json 文件
    返回格式: {
        "CHAPTER_1": {...},
        "CHAPTER_2": {...},
        ...
    }
    """
    nlp_data = {}
    nlp_dir = Path("data/nlp")
    
    if not nlp_dir.exists():
        logger.warning(f"NLP directory not found: {nlp_dir}")
        return {}
    
    # 获取所有 nlp*.json 文件并排序
    nlp_files = sorted(nlp_dir.glob("nlp*.json"), key=lambda x: int(x.stem.replace("nlp", "")))
    
    for file_path in nlp_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                chapter_data = json.load(f)
                # 每个文件应该只有一个 CHAPTER_X 键
                for chapter_key, chapter_content in chapter_data.items():
                    nlp_data[chapter_key] = chapter_content
                    logger.info(f"Loaded {chapter_key} from {file_path.name}")
        except Exception as e:
            logger.error(f"Failed to load {file_path}: {e}")
    
    return nlp_data


def save_nlp_chapter_notes(chapter_key, section_key, notes_content):
    """
    保存 notes 到对应的 nlpX.json 文件
    
    Args:
        chapter_key: 例如 "CHAPTER_1"
        section_key: 例如 "1.1"
        notes_content: 用户输入的笔记内容
    """
    import re
    
    # 提取章节号
    match = re.search(r'CHAPTER_(\d+)', chapter_key)
    if not match:
        logger.error(f"Invalid chapter key: {chapter_key}")
        return False
    
    chapter_num = match.group(1)
    file_path = Path(f"data/nlp/nlp{chapter_num}.json")
    
    if not file_path.exists():
        logger.error(f"File not found: {file_path}")
        return False
    
    try:
        # 读取现有文件
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # 更新 notes
        if chapter_key in data:
            if section_key in data[chapter_key]:
                data[chapter_key][section_key]["notes"] = notes_content
                logger.info(f"Updated notes for {chapter_key}/{section_key}")
            else:
                logger.warning(f"Section {section_key} not found in {chapter_key}")
                return False
        else:
            logger.warning(f"Chapter {chapter_key} not found in file")
            return False
        
        # 写回文件
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Saved notes to {file_path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to save notes: {e}")
        return False

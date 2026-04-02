# utils/quiz.py
import re
import logging
import streamlit as st
import time

logger = logging.getLogger(__name__)

def generate_quiz(client, topic, full_page_content):
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
            max_tokens=st.session_state.model_resp_tokens,
        )
        quiz_text = response.choices[0].message.content.strip()
        
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

def auto_generate_reference(client, level, full_page_content, path_string, mode="textbook"):
    # ========== 打印传入的内容 ==========
    print("\n" + "="*80)
    print("AUTO_GENERATE_REFERENCE CALLED")
    print("="*80)
    print(f"mode: {mode}")
    print(f"level: {level}")
    print(f"path_string: {path_string}")
    print("-"*80)
    print("FULL_PAGE_CONTENT:")
    print("-"*80)
    if full_page_content:
        print(full_page_content)
        print("-"*80)
        print(f"Total characters: {len(full_page_content)}")
    else:
        print("None")
    print("="*80 + "\n")
    # ========== 打印结束 ==========
    
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
    # ========== 将构造的完整 prompt 写入文件 ==========
    with open("/tmp/auto_ref_prompt.txt", "w", encoding="utf-8") as f:
        f.write("="*80 + "\n")
        f.write("FULL PROMPT SENT TO AI IN auto_generate_reference:\n")
        f.write("="*80 + "\n")
        f.write(prompt)
        f.write("\n" + "="*80 + "\n")
    # ========== 结束写入 ==========
    
    max_retries = 2
    retry_delay = 3
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=st.session_state.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=st.session_state.model_resp_tokens,
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

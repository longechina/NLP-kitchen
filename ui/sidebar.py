# ui/sidebar.py
import streamlit as st
import re
import datetime
import tempfile
import zipfile
import io
import os
import logging
from utils.tts import transcribe_audio, text_to_speech
from utils.search import global_search, local_search
from utils.ocr import process_ocr_images, process_ocr_pdf
from utils.quiz import generate_quiz
from config import AVAILABLE_MODELS
from ocr_image_module import format_results_as_text, ocr_images_batch, BAIMIAO_CONFIG as IMAGE_OCR_CONFIG
from utils.data_loader import load_nlp_textbook_data, load_learning_states, get_word_state_key

logger = logging.getLogger(__name__)

def render_sidebar(levels_data, nemt_cet_data, client, system_prompt, get_current_page_full_content, get_ai_reply):
    # 加载 NLP 数据
    nlp_data = load_nlp_textbook_data()
    
    with st.sidebar:
        # ========== 聊天消息展示区域 ==========
        chat_messages = st.container()
        with chat_messages:
            for msg in st.session_state.messages:
                if msg["role"] == "system":
                    continue
                if msg["role"] == "user":
                    st.write(f"**You:** {msg['content']}")
                else:
                    st.write(f"**AI:** {msg['content']}")
            # 自动滚动到最新消息
            st.markdown("""
            <script>
                setTimeout(function() {
                    var sidebar = document.querySelector('section[data-testid="stSidebar"]');
                    if (sidebar) {
                        var scrollable = sidebar.querySelector('[data-testid="stVerticalBlock"]');
                        if (scrollable) {
                            scrollable.scrollTop = scrollable.scrollHeight;
                        }
                    }
                }, 50);
            </script>
            """, unsafe_allow_html=True)

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
                            transcript = transcribe_audio(client, audio_bytes)
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
            # ===== 智能 Quiz 按钮：优先从缓存取（瞬发），否则现场生成 =====
            _page_key_for_quiz = None
            try:
                if (st.session_state.current_mode == "textbook" and st.session_state.level):
                    _page_key_for_quiz = f"textbook_{st.session_state.level}_{'_'.join(st.session_state.path)}"
                elif (st.session_state.current_mode == "nemt_cet" and st.session_state.selected_nemt_cet):
                    _page_key_for_quiz = f"nemt_cet_{st.session_state.selected_nemt_cet}_{'_'.join(st.session_state.nemt_cet_path)}"
            except Exception:
                pass
            
            _quiz_cached = _page_key_for_quiz and _page_key_for_quiz in st.session_state.auto_quiz_cache
            _quiz_btn_label = "Quiz (Ready)" if _quiz_cached else "Generate Quiz"
            
            if st.button(_quiz_btn_label, key="quiz_btn_small", use_container_width=True):
                full_page = get_current_page_full_content()
                topic = "general"
                if full_page:
                    sec_match = re.search(r"Section: (.+)", full_page)
                    if sec_match:
                        topic = sec_match.group(1)
                
                # 优先从缓存取
                cached = st.session_state.auto_quiz_cache.get(_page_key_for_quiz) if _page_key_for_quiz else None
                if cached:
                    quiz_text = cached["quiz_text"]
                    topic = cached.get("topic", topic)
                    questions = cached.get("questions", [])
                    # 用后删除，下次重新生成
                    del st.session_state.auto_quiz_cache[_page_key_for_quiz]
                    logger.info(f"[QUIZ] Served from cache for {_page_key_for_quiz}")
                else:
                    quiz_text = generate_quiz(client, topic, full_page)
                    questions = []
                    if quiz_text:
                        for line in quiz_text.split('\n'):
                            line = line.strip()
                            if re.match(r'^\d+[\.\s]', line):
                                questions.append(line)
                
                if quiz_text:
                    st.session_state.quiz_active = True
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
                        audio_bytes, fmt = text_to_speech(client, reply)
                        if audio_bytes:
                            st.session_state.pending_tts = (audio_bytes, fmt)
                    except Exception as e:
                        logger.error(f"TTS error: {e}")
                    st.rerun()
            # ===== 结束 Quiz 按钮 =====
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
        
        # ========== 全局复习提醒 ==========
        if st.session_state.learning_states:
            _total_review = sum(1 for k, v in st.session_state.learning_states.items()
                                if not k.startswith("note_") and v == 2)
            _total_learned = sum(1 for k, v in st.session_state.learning_states.items()
                                 if not k.startswith("note_") and v == 1)
            if _total_review > 0:
                st.markdown(f"**{_total_review} words need review** | {_total_learned} learned")
        # ==========================================

        # ========== 学习进度统计 ==========
        if st.session_state.learning_states:
            # 统计当前显示区域的单词状态
            total = 0
            learned = 0
            review = 0
            unlearned = 0
            
            # 根据当前模式统计
            if st.session_state.current_mode == "textbook" and st.session_state.level and st.session_state.path:
                # 获取当前显示的词汇
                data = levels_data[f"Level {st.session_state.level}"]
                current_node = data
                for key in st.session_state.path:
                    current_node = current_node.get(key, {})
                    if not current_node:
                        break
                if "vocabulary" in current_node and current_node["vocabulary"]:
                    path_str = "_".join(st.session_state.path)
                    for idx, item in enumerate(current_node["vocabulary"]):
                        total += 1
                        word_key = get_word_state_key("textbook", st.session_state.level, [path_str], idx)
                        state = st.session_state.learning_states.get(word_key, 0)
                        if state == 0:
                            unlearned += 1
                        elif state == 1:
                            learned += 1
                        elif state == 2:
                            review += 1
            
            elif st.session_state.current_mode == "nemt_cet" and st.session_state.selected_nemt_cet and st.session_state.nemt_cet_path:
                data = nemt_cet_data.get(st.session_state.selected_nemt_cet, {})
                current_node = data
                for key in st.session_state.nemt_cet_path:
                    current_node = current_node.get(key, {})
                    if not current_node:
                        break
                if "words" in current_node and current_node["words"]:
                    if isinstance(current_node["words"], str):
                        words_list = current_node["words"].split(" / ")
                    else:
                        words_list = current_node["words"]
                    path_str = "_".join([str(p) for p in st.session_state.nemt_cet_path])
                    for idx, word_item in enumerate(words_list):
                        if word_item and word_item.strip():
                            total += 1
                            word_key = get_word_state_key("nemt_cet", st.session_state.selected_nemt_cet, [path_str], idx)
                            state = st.session_state.learning_states.get(word_key, 0)
                            if state == 0:
                                unlearned += 1
                            elif state == 1:
                                learned += 1
                            elif state == 2:
                                review += 1
            
            elif st.session_state.current_mode == "nlp_textbook" and st.session_state.nlp_selected_section:
                # NLP 教材的词汇统计（如果后续添加词汇功能）
                pass
            
            if total > 0:
                st.markdown("---")
                st.markdown("### Learning Progress")
                st.markdown(f"**Not Started:** {unlearned}")
                st.markdown(f"**Learned:** {learned}")
                st.markdown(f"**Need Review:** {review}")
                st.markdown(f"**Total:** {total}")
                st.progress((learned + review) / total if total > 0 else 0)
                
                # 筛选控件
                filter_opts = {
                    "all": "All Words",
                    "unlearned": "Not Started",
                    "learned": "Learned",
                    "review": "Need Review"
                }
                current_filter = st.session_state.vocab_filter
                filter_labels = [filter_opts["all"], filter_opts["unlearned"], filter_opts["learned"], filter_opts["review"]]
                filter_values = ["all", "unlearned", "learned", "review"]
                
                new_filter = st.selectbox(
                    "Show",
                    options=filter_values,
                    format_func=lambda x: filter_opts[x],
                    index=filter_values.index(current_filter),
                    key="vocab_filter_select"
                )
                if new_filter != current_filter:
                    st.session_state.vocab_filter = new_filter
                    st.rerun()
        
        # ========== 设置工具区域 ==========
        # Mode - 添加 Info. Search 选项
        mode_opts = ["Chinese", "English", "NEMT & CET", "NLP Textbook", "Info. Search"]
        cur_idx = 0
        if st.session_state.language == "English":
            cur_idx = 1
        elif st.session_state.language == "NEMT & CET":
            cur_idx = 2
        elif st.session_state.language == "NLP Textbook":
            cur_idx = 3
        elif st.session_state.language == "Info. Search":
            cur_idx = 4
        
        new_lang = st.selectbox("Mode", mode_opts, index=cur_idx, key="mode_select")
        if new_lang != st.session_state.language:
            st.session_state.language = new_lang
            if new_lang == "NEMT & CET":
                st.session_state.current_mode = "nemt_cet"
                st.session_state.level = None
                st.session_state.path = []
                st.session_state.selected_nemt_cet = None
                st.session_state.nemt_cet_path = []
                st.session_state.nlp_selected_chapter = None
                st.session_state.nlp_selected_section = None
            elif new_lang == "NLP Textbook":
                st.session_state.current_mode = "nlp_textbook"
                st.session_state.level = None
                st.session_state.path = []
                st.session_state.selected_nemt_cet = None
                st.session_state.nemt_cet_path = []
                st.session_state.nlp_selected_chapter = None
                st.session_state.nlp_selected_section = None
            elif new_lang == "Info. Search":
                st.session_state.current_mode = "info_search"
                st.session_state.level = None
                st.session_state.path = []
                st.session_state.selected_nemt_cet = None
                st.session_state.nemt_cet_path = []
                st.session_state.nlp_selected_chapter = None
                st.session_state.nlp_selected_section = None
            else:
                st.session_state.current_mode = "textbook"
                from utils.data_loader import load_level_data
                levels_data = load_level_data(st.session_state.language)
                st.session_state.level = None
                st.session_state.path = []
                st.session_state.selected_nemt_cet = None
                st.session_state.nemt_cet_path = []
                st.session_state.nlp_selected_chapter = None
                st.session_state.nlp_selected_section = None
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
                    st.session_state.search_results = global_search(search_input, levels_data, nemt_cet_data, nlp_data)
                else:
                    st.session_state.search_results = local_search(
                        search_input, st.session_state.current_mode, st.session_state.level,
                        st.session_state.selected_nemt_cet, levels_data, nemt_cet_data, nlp_data
                    )
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
            st.session_state.model_resp_tokens = AVAILABLE_MODELS[new_model]["resp_tokens"]
            st.rerun()
        
        # OCR 文件上传
        img_files = st.file_uploader("Images", type=["jpg","jpeg","png","bmp","webp","tiff"], accept_multiple_files=True, key="ocr_imgs")
        pdf_file = st.file_uploader("PDF", type=["pdf"], key="ocr_pdf")
        zip_file = st.file_uploader("ZIP", type=["zip"], key="ocr_zip")

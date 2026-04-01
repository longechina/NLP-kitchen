# state/session.py
import streamlit as st
from config import AVAILABLE_MODELS, DEFAULT_MODEL

def init_session_state():
    # 模型状态
    if "selected_model" not in st.session_state:
        st.session_state.selected_model = DEFAULT_MODEL
    if "model_name" not in st.session_state:
        st.session_state.model_name = AVAILABLE_MODELS[DEFAULT_MODEL]["id"]
    if "model_max_tokens" not in st.session_state:
        st.session_state.model_max_tokens = AVAILABLE_MODELS[DEFAULT_MODEL]["max_tokens"]
    
    # 语言和模式
    if "language" not in st.session_state:
        st.session_state.language = "Chinese"
    if "current_mode" not in st.session_state:
        st.session_state.current_mode = "textbook"
    if "selected_nemt_cet" not in st.session_state:
        st.session_state.selected_nemt_cet = None
    if "nemt_cet_path" not in st.session_state:
        st.session_state.nemt_cet_path = []
    if "level" not in st.session_state:
        st.session_state.level = None
    if "path" not in st.session_state:
        st.session_state.path = []
    
    # NLP 教材状态
    if "nlp_selected_chapter" not in st.session_state:
        st.session_state.nlp_selected_chapter = None
    if "nlp_selected_section" not in st.session_state:
        st.session_state.nlp_selected_section = None
    if "nlp_notes_editing" not in st.session_state:
        st.session_state.nlp_notes_editing = False
    if "nlp_current_notes" not in st.session_state:
        st.session_state.nlp_current_notes = ""
    
    # 聊天状态
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_summary" not in st.session_state:
        st.session_state.conversation_summary = ""
    if "conv_history" not in st.session_state:
        st.session_state.conv_history = []
    if "user_msg_count" not in st.session_state:
        st.session_state.user_msg_count = 0
    if "chat_open" not in st.session_state:
        st.session_state.chat_open = True
    if "pending_tts" not in st.session_state:
        st.session_state.pending_tts = None
    if "last_audio_id" not in st.session_state:
        st.session_state.last_audio_id = None
    
    # Quiz 状态
    if "quiz_active" not in st.session_state:
        st.session_state.quiz_active = False
    if "current_quiz" not in st.session_state:
        st.session_state.current_quiz = None
    if "quiz_answers" not in st.session_state:
        st.session_state.quiz_answers = {}
    if "quiz_asked" not in st.session_state:
        st.session_state.quiz_asked = False
    
    # 搜索状态
    if "search_keyword" not in st.session_state:
        st.session_state.search_keyword = ""
    if "search_results" not in st.session_state:
        st.session_state.search_results = []
    if "search_scope" not in st.session_state:
        st.session_state.search_scope = "global"
    
    # 其他状态
    if "flip_states" not in st.session_state:
        st.session_state.flip_states = {}
    if "page_recommendations" not in st.session_state:
        st.session_state.page_recommendations = {}
    if "current_page_key" not in st.session_state:
        st.session_state.current_page_key = None

# ui/main_content.py
import streamlit as st
import re
from utils.search import global_search, local_search
from utils.helpers import translate_word

def render_main_content(levels_data, nemt_cet_data, client, get_current_page_full_content, get_page_recommendations, get_ai_reply):
    # 显示搜索结果
    if st.session_state.search_keyword and st.session_state.search_results:
        st.markdown(f"### Search Results for '{st.session_state.search_keyword}'")
        st.markdown(f"Found {len(st.session_state.search_results)} result(s)")
        
        kw = st.session_state.search_keyword
        
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
                exam_name = res.get('level', 'Exam')
                source_info = f"{exam_name}"
            else:
                source_info = "Content"
            
            content_preview = res["content"].replace("\n", " ")[:120]
            if len(res["content"]) > 120:
                content_preview += "..."
            
            try:
                highlighted_preview = re.sub(
                    re.escape(kw),
                    f"[{kw.upper()}]",
                    content_preview,
                    flags=re.IGNORECASE
                )
            except Exception:
                highlighted_preview = content_preview
            
            button_label = f"{res.get('type', 'Content')} | {source_info}\n\n{highlighted_preview}\n\nPath: {path_str}"
            
            if st.button(
                button_label,
                key=f"search_result_{idx}_{hash(str(res))}",
                use_container_width=True
            ):
                if res.get("source") == "textbook" and res.get("level"):
                    st.session_state.current_mode = "textbook"
                    st.session_state.level = res["level"]
                    level_prefix = f"LEVEL_{['I','II','III'][res['level']-1]}"
                    raw_path = res.get("path", [])
                    clean_path = []
                    for p in raw_path:
                        p_str = str(p)
                        if '[' in p_str:
                            p_str = p_str.split('[')[0]
                        if p_str:
                            clean_path.append(p_str)
                    if clean_path and clean_path[0] != level_prefix:
                        clean_path = [level_prefix] + clean_path
                    st.session_state.path = clean_path if clean_path else [level_prefix]
                    st.session_state.search_keyword = ""
                    st.session_state.search_results = []
                    st.rerun()
                
                elif res.get("source") == "nemt_cet":
                    exam = res.get("level") or res.get("exam")
                    if not exam:
                        st.warning("无法识别考试类型")
                        st.stop()
                    
                    st.session_state.current_mode = "nemt_cet"
                    st.session_state.selected_nemt_cet = exam
                    
                    raw_path = res.get("path", [])
                    clean_nemt_path = []
                    
                    for p in raw_path:
                        p_str = str(p)
                        if '[' in p_str:
                            p_str = p_str.split('[')[0]
                        if not p_str:
                            continue
                        if p_str == exam:
                            continue
                        if p_str in ["words", "examples", "notes", "name", "vocabulary"]:
                            continue
                        clean_nemt_path.append(p_str)
                    
                    st.session_state.nemt_cet_path = clean_nemt_path
                    st.session_state.flip_states = {}
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

                from utils.data_loader import load_level_data
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
                            if other_item:
                                other_pron = other_parts[1] if len(other_parts) > 1 else ""
                            else:
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
                                    translation = translate_word(client,word, target_lang)
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
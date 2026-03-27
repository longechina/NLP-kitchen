import streamlit as st

st.set_page_config(layout="wide", page_title="Test Sidebar", initial_sidebar_state="expanded")

st.title("测试侧边栏折叠按钮")

with st.sidebar:
    st.write("侧边栏内容")
    st.button("测试按钮")

st.write("主界面内容")
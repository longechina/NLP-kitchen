# utils/ai.py
import os
import streamlit as st
import groq

@st.cache_resource
def get_groq_client():
    return groq.Client(api_key=os.environ.get("GROQ_API_KEY") or st.secrets["GROQ_API_KEY"])

def build_system_prompt(levels, teaching_principles):
    prompt = f"""You are a learning assistant helping students learn knowledge.
You have access to learning materials.

TEACHING PRINCIPLES (MUST FOLLOW):
{teaching_principles}

Keep your answers concise, clear, and helpful. Focus on what the user is currently studying. No emojis!"""
    return prompt

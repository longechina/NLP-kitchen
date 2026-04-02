# config.py
import streamlit as st

# max_tokens  = 模型上下文窗口大小（用于参考/显示）
# resp_tokens = 每次 API 调用的输出预算（实际传给 max_tokens 参数）
#               Groq TPM = input_tokens + resp_tokens，必须小于模型的每分钟限制
AVAILABLE_MODELS = {
    "Llama 4 Scout 17B": {"id": "meta-llama/llama-4-scout-17b-16e-instruct", "max_tokens": 8192,   "resp_tokens": 4096},
    "Llama 3.3 70B":     {"id": "llama-3.3-70b-versatile",                   "max_tokens": 32768,  "resp_tokens": 4096},
    "Llama 3.1 8B":      {"id": "llama-3.1-8b-instant",                      "max_tokens": 131072, "resp_tokens": 4096},
    "GPT OSS 120B":      {"id": "openai/gpt-oss-120b",                        "max_tokens": 65536,  "resp_tokens": 4096},
    "GPT OSS 20B":       {"id": "openai/gpt-oss-20b",                         "max_tokens": 65536,  "resp_tokens": 4096},
    "Qwen 3 32B":        {"id": "qwen/qwen3-32b",                             "max_tokens": 40960,  "resp_tokens": 4096},
    "Kimi K2 Instruct":  {"id": "moonshotai/kimi-k2-instruct-0905",           "max_tokens": 8192,   "resp_tokens": 4096},
    "Groq Compound":     {"id": "groq/compound",                              "max_tokens": 8192,   "resp_tokens": 4096},
    "Groq Compound Mini":{"id": "groq/compound-mini",                         "max_tokens": 8192,   "resp_tokens": 4096},
    # ========== Google Gemini 模型（新版官方参数）==========
    "Gemini 3.1 Pro":         {"id": "gemini-3.1-pro-preview",         "max_tokens": 1000000, "resp_tokens": 64000, "provider": "google"},
    "Gemini 3.1 Flash Lite":  {"id": "gemini-3.1-flash-lite-preview",  "max_tokens": 1000000, "resp_tokens": 64000, "provider": "google"},
    "Gemini 3.1 Flash Image": {"id": "gemini-3.1-flash-image-preview", "max_tokens": 128000,  "resp_tokens": 32000, "provider": "google"},
    "Gemini 3 Flash":         {"id": "gemini-3-flash-preview",         "max_tokens": 1000000, "resp_tokens": 64000, "provider": "google"},
    "Gemini 3 Pro Image":     {"id": "gemini-3-pro-image-preview",     "max_tokens": 65000,   "resp_tokens": 32000, "provider": "google"},
}

# 默认选中这个模型
DEFAULT_MODEL = "GPT OSS 20B"

# GitHub 同步相关配置
GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN")
REPO_OWNER = st.secrets.get("GITHUB_REPO_OWNER")
REPO_NAME = st.secrets.get("GITHUB_REPO_NAME")
GITHUB_ENABLED = GITHUB_TOKEN and REPO_OWNER and REPO_NAME
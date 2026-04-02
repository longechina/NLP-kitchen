#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import google.generativeai as genai

# ========== 请在这里粘贴您的 API Key ==========
API_KEY = "AIzaSyACv49a8zFr3gQ5mOKwPuaQAuWuX6cjUk8"
# ============================================

# 要测试的模型
MODEL_NAME = "gemini-3.1-pro-preview"

print("=" * 50)
print(f"测试模型: {MODEL_NAME}")
print("=" * 50)

try:
    # 配置 API
    genai.configure(api_key=API_KEY)
    
    # 创建模型
    model = genai.GenerativeModel(MODEL_NAME)
    
    # 测试生成
    print("发送测试请求...")
    response = model.generate_content("Just say 'OK, model works'")
    
    print(f"\n✅ 模型可用！")
    print(f"回复: {response.text}")
    print(f"✅ 成功")
    
except Exception as e:
    print(f"\n❌ 模型不可用")
    print(f"错误: {e}")
    
    # 尝试列出可用模型
    print("\n" + "=" * 50)
    print("您有权限的模型列表:")
    print("=" * 50)
    try:
        for m in genai.list_models():
            if "generateContent" in m.supported_generation_methods:
                print(f"  ✅ {m.name}")
    except Exception as list_err:
        print(f"无法列出模型: {list_err}")

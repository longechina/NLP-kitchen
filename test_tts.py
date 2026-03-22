import soundfile as sf
from kokoro_onnx import Kokoro

model_path = "kokoro-chinese/model_static.onnx"
voice_file = "kokoro-chinese/voices/zf_001.npy"   # 使用转换后的 .npy

kokoro = Kokoro(model_path, voice_file)
text = "你好，这是一个测试。"
samples, sr = kokoro.create(text, voice="zf_001", speed=1.0)
sf.write("test_zf_001.wav", samples, sr)
print("生成 test_zf_001.wav 完成")

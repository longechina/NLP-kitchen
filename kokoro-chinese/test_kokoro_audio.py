import onnxruntime as ort
import numpy as np
import wave

MODEL_PATH = "model_static.onnx"

# 简单字符编码 tokenizer
def text_to_input_ids(text):
    ids = [ord(c) % 256 for c in text]  # 每个字符转成 0-255
    if len(ids) < 512:
        ids += [0] * (512 - len(ids))
    else:
        ids = ids[:512]
    return np.array([ids], dtype=np.int64)


def save_wav(audio, filename="output.wav", sr=22050):
    audio = np.clip(audio, -1.0, 1.0)
    audio_int16 = (audio * 32767).astype(np.int16)
    with wave.open(filename, "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(audio_int16.tobytes())


def main():
    print("Loading model...")
    session = ort.InferenceSession(MODEL_PATH)

    text = "你好，这是一个测试。"

    input_ids = text_to_input_ids(text)

    # 先用随机 voice embedding
    ref_s = np.random.randn(1, 256).astype(np.float32)
    speed = np.array([1.0], dtype=np.float32)

    outputs = session.run(
        None,
        {
            "input_ids": input_ids,
            "ref_s": ref_s,
            "speed": speed,
        }
    )

    audio = outputs[0]

    print("Saving audio...")
    save_wav(audio, "output.wav")
    print("Done! -> output.wav")


if __name__ == "__main__":
    main()

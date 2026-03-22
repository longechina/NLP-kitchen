import onnxruntime as ort
import numpy as np

MODEL_PATH = "model_static.onnx"

def inspect_model(session):
    print("=== Inputs ===")
    for inp in session.get_inputs():
        print(f"name={inp.name}, shape={inp.shape}, type={inp.type}")
    
    print("\n=== Outputs ===")
    for out in session.get_outputs():
        print(f"name={out.name}, shape={out.shape}, type={out.type}")


def fake_run(session):
    inputs = session.get_inputs()
    
    feed = {}
    
    for inp in inputs:
        shape = []
        for dim in inp.shape:
            if isinstance(dim, str) or dim is None:
                shape.append(1)
            else:
                shape.append(dim)
        
        if "int" in inp.type:
            data = np.ones(shape, dtype=np.int64)
        else:
            data = np.random.randn(*shape).astype(np.float32)
        
        feed[inp.name] = data
    
    print("\nRunning fake inference...")
    outputs = session.run(None, feed)
    
    print("Success! Output shapes:")
    for o in outputs:
        print(np.array(o).shape)


def main():
    print("Loading model...")
    session = ort.InferenceSession(MODEL_PATH)
    
    inspect_model(session)
    fake_run(session)


if __name__ == "__main__":
    main()

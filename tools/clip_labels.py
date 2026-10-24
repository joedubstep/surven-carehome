"""Build surven_outreach/day7/clip_labels.json: the descriptions the free ad's photo check compares photos with
(day7/looks.py), each turned into CLIP's numbers once here so the nightly run only needs the image half of the model.
Run again after changing LABELS. Needs: pip install onnxruntime tokenizers numpy (downloads ~65 MB once)."""
import json
import urllib.request
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

LABELS = {
    "building": ["a photo of the outside of a senior living building", "an exterior photo of a large house with a lawn",
                 "a photo of an apartment building", "a photo of the front entrance of a building with a porch",
                 "an aerial photo of a building complex", "a photo of a one story building and its driveway"],
    "people": ["a photo of smiling elderly people", "a photo of seniors doing an activity together",
               "a caregiver hugging an elderly woman", "elderly residents sitting at a table"],
    "inside": ["a photo of an indoor living room", "a photo of a dining room with tables and chairs",
               "a photo of a bedroom", "a photo of a hallway", "a photo of an indoor swimming pool",
               "a photo of a kitchen", "a photo of a lobby", "a painting on a wall"],
    "sign": ["a sign with a building name in front of a lawn"],
    "bad": ["a close-up photo of a flower", "a portrait headshot of a person", "people wearing costumes at a party",
            "a christmas wreath in the snow", "a poster with lots of text", "a logo", "a landscape with trees and no buildings",
            "food on a plate", "a map", "a field of flowers", "a beach and the ocean", "a parking lot",
            "a cartoon drawing", "a document or a menu", "a group photo of staff in scrubs", "a car"],
}
BASE = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
CACHE = Path.home() / ".cache" / "surven-clip"
OUT = Path(__file__).resolve().parents[1] / "surven_outreach" / "day7" / "clip_labels.json"


def fetch(name: str, path: str) -> Path:
    p = CACHE / name
    if not p.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(BASE + path, p)
    return p


def main():
    tok = Tokenizer.from_file(str(fetch("tokenizer.json", "tokenizer.json")))
    sess = ort.InferenceSession(str(fetch("text_model_quantized.onnx", "onnx/text_model_quantized.onnx")),
                                providers=["CPUExecutionProvider"])
    rows = []
    for group, texts in LABELS.items():
        for t in texts:
            ids = tok.encode(t).ids
            e = sess.run(None, {"input_ids": np.array([ids], dtype=np.int64)})[0][0]
            e = e / np.linalg.norm(e)
            rows.append({"group": group, "text": t, "emb": [round(float(x), 5) for x in e]})
    OUT.write_text(json.dumps(rows), encoding="utf-8")
    print(len(rows), "labels ->", OUT)


if __name__ == "__main__":
    main()

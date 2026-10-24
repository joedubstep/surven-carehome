"""What a photo shows, so the free ad gets the house (Joey 10/24: "make sure we get actual pictures of the house").

A free image model (CLIP, OpenAI's open model, run on this machine with onnxruntime; ~85 MB, downloaded once into
~/.cache/surven-clip) compares the photo with short descriptions in clip_labels.json and says which group fits:
  building  the outside of the home          people  residents or staff
  inside    a room, a hallway, a pool         sign    the home's sign
  bad       flowers, a headshot, a costume party, a wreath, a poster with text, a logo, a map, food, a beach...
Color rules could not tell a costume party from a lawn or a blue wall from the sky (10/24 volume test).
No model on hand (no internet the first time): None, and photo.py falls back to its color rule."""
from __future__ import annotations

import io
import json
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABELS = HERE / "clip_labels.json"
CACHE = Path.home() / ".cache" / "surven-clip"
MODEL = CACHE / "vision_model_quantized.onnx"
URL = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/onnx/vision_model_quantized.onnx"
MEAN = (0.48145466, 0.4578275, 0.40821073)
STD = (0.26862954, 0.26130258, 0.27577711)
_state: dict = {}


def _load():
    if "session" in _state:
        return _state["session"]
    _state["session"] = None
    try:
        import numpy as np
        import onnxruntime as ort
        if not MODEL.exists():
            CACHE.mkdir(parents=True, exist_ok=True)
            tmp = MODEL.with_suffix(".part")
            urllib.request.urlretrieve(URL, tmp)
            tmp.replace(MODEL)
        lab = json.loads(LABELS.read_text(encoding="utf-8"))
        _state["names"] = [x["text"] for x in lab]
        _state["groups"] = [x["group"] for x in lab]
        _state["text"] = np.array([x["emb"] for x in lab], dtype=np.float32)
        _state["session"] = ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])
    except Exception:
        _state["session"] = None
    return _state["session"]


def _pixels(data: bytes):
    import numpy as np
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    s = 224 / min(w, h)
    im = im.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BICUBIC)
    w, h = im.size
    left, top = (w - 224) // 2, (h - 224) // 2
    a = np.asarray(im.crop((left, top, left + 224, top + 224)), dtype=np.float32) / 255.0
    a = (a - np.array(MEAN, dtype=np.float32)) / np.array(STD, dtype=np.float32)
    return a.transpose(2, 0, 1)[None]


def look(data: bytes) -> dict | None:
    """-> {"building": p, "people": p, "inside": p, "sign": p, "bad": p, "top": best description} or None."""
    sess = _load()
    if sess is None:
        return None
    import numpy as np
    try:
        emb = sess.run(None, {"pixel_values": _pixels(data)})[0][0]
    except Exception:
        return None
    emb = emb / np.linalg.norm(emb)
    logits = 100.0 * (_state["text"] @ emb)
    p = np.exp(logits - logits.max())
    p /= p.sum()
    out = {g: 0.0 for g in ("building", "people", "inside", "sign", "bad")}
    for g, v in zip(_state["groups"], p):
        out[g] += float(v)
    out = {k: round(v, 3) for k, v in out.items()}
    out["top"] = _state["names"][int(p.argmax())]
    return out

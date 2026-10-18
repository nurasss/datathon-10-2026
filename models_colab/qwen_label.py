"""Label training frames with Qwen3-VL-4B-Instruct (teacher). Runs on Colab GPU.

Writes /content/labels.jsonl incrementally (resumable). Log: /content/qwen.log
"""
import json
import os
import re
import sys
import time

import torch
from PIL import Image
from transformers import AutoModelForImageTextToText, AutoProcessor

MODEL = "Qwen/Qwen3-VL-4B-Instruct"
FRAMES = "/content/frames"
OUT = "/content/labels.jsonl"
BATCH = int(os.environ.get("QBATCH", "12"))

PROMPT = (
    "This is a frame from an overhead CCTV camera in a bank cashier room. "
    "Fixed equipment that is NOT cash: banknote counting machine, desk telephone, laptop, printer. "
    "The safe is a grey metal cabinet standing by the wall.\n"
    "Return ONLY one line of minified JSON (no spaces, no newlines), no prose:\n"
    '{"people": int, "cash_on_desk": bool, "cash_in_hand": bool, "cash_bag": bool, '
    '"phone_in_hand": bool, "cup_or_food": bool, "clothing_or_bag_on_desk": bool, '
    '"safe_door_open": true|false|null, "laptop_screen": "document"|"lockscreen"|"off"|"not_visible", '
    '"boxes": [{"label": "cash"|"cash_bag"|"phone"|"cup_or_food"|"clothing_or_bag"|"safe"|"laptop", '
    '"bbox_2d": [x1, y1, x2, y2]}]}\n'
    "Rules: cash = paper banknotes or bundles of banknotes; cash_bag = a cash collection bag; "
    "phone = a handheld mobile phone (not the desk telephone); laptop_screen 'lockscreen' = wallpaper "
    "picture with clock, 'document' = open window/table/text. Give a box for every visible object of "
    "these labels only (at most 6 boxes, no other labels); bbox_2d in 0-1000 relative coordinates. "
    "phone counts only when held in a person's hand. Use null for safe_door_open if the safe door "
    "is not visible."
)


def log(msg):
    with open("/content/qwen.log", "a") as f:
        f.write(time.strftime("%H:%M:%S ") + msg + "\n")


def main():
    done = set()
    if os.path.exists(OUT):
        with open(OUT) as f:
            for line in f:
                try:
                    done.add(json.loads(line)["file"])
                except Exception:
                    pass
    keep = json.load(open("/content/subset2.json"))
    files = [f for f in keep if f not in done]
    log(f"start: {len(files)} to do, {len(done)} done")
    proc = AutoProcessor.from_pretrained(MODEL, max_pixels=448 * 448)
    proc.tokenizer.padding_side = "left"
    model = AutoModelForImageTextToText.from_pretrained(
        MODEL, dtype=torch.float16, device_map="cuda"
    ).eval()
    log("model loaded")
    t0 = time.time()
    for i in range(0, len(files), BATCH):
        batch = files[i:i + BATCH]
        msgs = [[{"role": "user", "content": [
            {"type": "image", "image": Image.open(os.path.join(FRAMES, f)).convert("RGB")},
            {"type": "text", "text": PROMPT}]}] for f in batch]
        inputs = proc.apply_chat_template(
            msgs, tokenize=True, add_generation_prompt=True, return_dict=True,
            return_tensors="pt", padding=True,
        ).to("cuda")
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=200, do_sample=False)
        texts = proc.batch_decode(out[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        with open(OUT, "a") as fo:
            for f, t in zip(batch, texts):
                m = re.search(r"\{.*\}", t, re.S)
                rec = {"file": f, "raw": t}
                if m:
                    try:
                        rec["ans"] = json.loads(m.group(0))
                    except Exception:
                        pass
                fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
        n = i + len(batch)
        log(f"{n}/{len(files)} {(time.time()-t0)/n:.2f}s/frame")
    log("DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log(f"ERROR {type(e).__name__}: {e}")
        raise

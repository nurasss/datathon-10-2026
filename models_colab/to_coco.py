"""Qwen labels.jsonl -> COCO train/val jsons (leave-angle-out) + parsed answers.

Usage: python -I to_coco.py labels.jsonl manifest.json out_dir VAL_GROUPS(comma)
"""
import json
import re
import sys
from pathlib import Path

labels_path, manifest_path, out_dir, val_groups = sys.argv[1:5]
val_groups = set(val_groups.split(","))
out = Path(out_dir)
out.mkdir(parents=True, exist_ok=True)
W, H = 464, 848
CLASSES = ["cash", "cash_bag", "phone", "cup_or_food", "clothing_or_bag",
           "safe_open", "safe_closed", "laptop_document", "laptop_lockscreen"]
CID = {c: i + 1 for i, c in enumerate(CLASSES)}

SCALAR = re.compile(r'"(\w+)"\s*:\s*(true|false|null|-?\d+|"[^"]*")')
BOX = re.compile(r'\{\s*"label"\s*:\s*"([\w ]+)"\s*,\s*"bbox_2d"\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]')


def parse(raw: str) -> dict:
    ans = {}
    head = raw.split('"boxes"')[0]
    for k, v in SCALAR.findall(head):
        ans[k] = json.loads(v)
    ans["boxes"] = [{"label": m[0], "bbox_2d": [int(x) for x in m[1:]]} for m in BOX.findall(raw)]
    return ans


manifest = {m["file"]: m for m in json.load(open(manifest_path))}
parsed = {}
for line in open(labels_path):
    rec = json.loads(line)
    parsed[rec["file"]] = rec.get("ans") or parse(rec["raw"])
(out / "answers.json").write_text(json.dumps(parsed, ensure_ascii=False))

gate = {"cash": lambda a: a.get("cash_on_desk") or a.get("cash_in_hand"),
        "cash_bag": lambda a: a.get("cash_bag"),
        "phone": lambda a: a.get("phone_in_hand"),
        "cup_or_food": lambda a: a.get("cup_or_food"),
        "clothing_or_bag": lambda a: a.get("clothing_or_bag_on_desk")}

splits = {"train": {"images": [], "annotations": []}, "val": {"images": [], "annotations": []}}
stats = {s: {c: 0 for c in CLASSES} for s in splits}
aid = 1
for img_id, (fname, ans) in enumerate(sorted(parsed.items()), 1):
    m = manifest.get(fname)
    if not m:
        continue
    split = "val" if m["group"] in val_groups else "train"
    splits[split]["images"].append({"id": img_id, "file_name": fname, "width": W, "height": H})
    for b in ans.get("boxes", []):
        lab = b["label"]
        if lab == "safe":
            sdo = ans.get("safe_door_open")
            if sdo is None:
                continue
            lab = "safe_open" if sdo else "safe_closed"
        elif lab == "laptop":
            scr = ans.get("laptop_screen")
            if scr not in ("document", "lockscreen"):
                continue
            lab = "laptop_" + scr
        elif lab in gate:
            if not gate[lab](ans):
                continue
        else:
            continue
        x1, y1, x2, y2 = b["bbox_2d"]
        x1, x2 = sorted((x1 * W / 1000, x2 * W / 1000))
        y1, y2 = sorted((y1 * H / 1000, y2 * H / 1000))
        w, h = x2 - x1, y2 - y1
        if w < 4 or h < 4 or w * h > 0.5 * W * H:
            continue
        splits[split]["annotations"].append({"id": aid, "image_id": img_id, "category_id": CID[lab],
                                             "bbox": [x1, y1, w, h], "area": w * h, "iscrowd": 0})
        stats[split][lab] += 1
        aid += 1

cats = [{"id": CID[c], "name": c} for c in CLASSES]
for s, d in splits.items():
    d["categories"] = cats
    (out / f"{s}.json").write_text(json.dumps(d))
print(json.dumps({s: {"images": len(d["images"]), **stats[s]} for s, d in splits.items()}, indent=1))

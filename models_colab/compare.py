"""Compare Qwen per-frame answers with events.csv (Gemini annotation).

Usage: python -I compare.py answers.json manifest.json events.csv out.md
"""
import csv
import json
import sys
from collections import defaultdict

answers = json.load(open(sys.argv[1]))
manifest = {m["file"]: m for m in json.load(open(sys.argv[2]))}
by_video = defaultdict(list)
for f, a in answers.items():
    m = manifest.get(f)
    if m:
        by_video[m["video"]].append((m["t"], a))
for v in by_video.values():
    v.sort(key=lambda x: x[0])

SIGNAL = {
    "LEAVE_VALUABLES": ("cash_on_desk|cash_bag", lambda a: bool(a.get("cash_on_desk") or a.get("cash_bag"))),
    "LEAVE_SCREEN_UNLOCKED": ("laptop_screen=document", lambda a: a.get("laptop_screen") == "document"),
    "SAFE_OPEN": ("safe_door_open", lambda a: a.get("safe_door_open") is True),
    "PERSONAL_ITEMS": ("cup_or_food|clothing_or_bag", lambda a: bool(a.get("cup_or_food") or a.get("clothing_or_bag_on_desk"))),
    "PHONE_USE": ("phone_in_hand", lambda a: bool(a.get("phone_in_hand"))),
    "OUTSIDER": ("people>=3 or (people>=2 & no client before)", lambda a: (a.get("people") or 0) >= 2),
    "MANUAL_COUNT": ("cash_in_hand", lambda a: bool(a.get("cash_in_hand"))),
}
ALL = {k: f for k, (_, f) in SIGNAL.items()}

rows = []
with open(sys.argv[3], encoding="utf-8") as fh:
    for r in csv.DictReader(fh):
        vid = r["video"].split(" ")[0].replace("Х", "X")
        code = r["type_code"]
        frames = by_video.get(vid, [])
        if code == "NONE":
            hits = {k: sum(1 for _, a in frames if f(a)) for k, f in ALL.items() if k != "OUTSIDER"}
            flagged = {k: v for k, v in hits.items() if v >= max(2, len(frames) // 5)}
            rows.append((vid, code, "-", f"{len(frames)} кадров", "РАСХОЖДЕНИЕ: " + ", ".join(f"{k}×{v}" for k, v in flagged.items()) if flagged else "ok"))
            continue
        try:
            peak = float(r["peak_sec"] or r["label_sec"])
        except ValueError:
            peak = None
        if code not in SIGNAL or peak is None:
            rows.append((vid, code, r.get("peak_sec", ""), "нет сигнала у Qwen", "не проверяется"))
            continue
        name, fn = SIGNAL[code]
        near = [(t, a) for t, a in frames if abs(t - peak) <= 3]
        hit = [t for t, a in near if fn(a)]
        extra = ""
        if code == "OUTSIDER":
            extra = " people=" + ",".join(str(a.get("people")) for _, a in near)
        verdict = "ok" if hit else "РАСХОЖДЕНИЕ"
        rows.append((vid, code, f"{peak:g}", f"{name}: {len(hit)}/{len(near)} кадров ±3 с{extra}", verdict))

with open(sys.argv[4], "w", encoding="utf-8") as out:
    out.write("| видео | тип (events.csv) | пик, с | сигнал Qwen | итог |\n|---|---|---|---|---|\n")
    for r in rows:
        out.write("| " + " | ".join(str(x) for x in r) + " |\n")
print(sum(1 for r in rows if r[-1].startswith("РАСХ")), "расхождений из", len(rows))

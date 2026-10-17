"""Explicit offline annotation injection. Never imported by the HTTP job path.

Upper-bound ENGINE test, not a vision score: both scene states and action
triggers come from annotations. Unsupported taxonomy rows remain unsupported.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from .scene_state import DeskItem, SceneState
from .triggers import Trigger
from rules.registry import TAXONOMY


def normalize_video(name: str) -> str:
    name = Path(name).stem.replace("Х", "X")
    match = re.search(r"X-(\d+)", name)
    return match.group(0) if match else name


def load_annotations(path: str | Path) -> dict[str, list[dict]]:
    rows = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows.setdefault(normalize_video(row["video"]), []).append(row)
    return rows


def code_for_annotation(row: dict) -> str | None:
    code = row["type_code"]
    if code in TAXONOMY:
        return code
    mapped = {"NONE": None, "VALUABLES_ON_DESK": "LEAVE_VALUABLES", "SCREEN_NOT_LOCKED": "LEAVE_SCREEN_UNLOCKED",
              "SAFE_LEFT_OPEN": "SAFE_OPEN", "PERSONAL_ITEMS_DESK": "PERSONAL_ITEMS", "UNAUTHORIZED_ENTRY": "OUTSIDER",
              "DOOR_LEFT_OPEN": "DOOR_OPEN"}
    if code in mapped:
        return mapped[code]
    if code == "IMPROPER_OPERATIONS":
        text = " ".join(row.get(k, "") for k in ("observed", "type_name", "notes")).lower()
        if "телефон" in text or "смартфон" in text:
            return "PHONE_USE"
        if "вручную" in text or "ручной пересчёт" in text:
            return "MANUAL_COUNT"
    return None


def oracle_inputs(rows: list[dict], duration: float):
    scenes_by_t = {}
    presence = {}
    triggers = []
    unsupported = []
    for row in rows:
        code = code_for_annotation(row)
        if code is None:
            if row["type_code"] != "NONE":
                unsupported.append(row)
            continue
        start = float(row["start_sec"])
        end = min(duration-0.05, float(row["end_sec"]))
        if code == "OUTSIDER":
            triggers.append(Trigger("outsider", start, end, 9001, {"reason": "oracle_annotated_entry", "zone_reached_sec": start,
                "support_times": [round(start+i*.2, 4) for i in range(int((end-start)*5)+1)]}))
        elif code.startswith("LEAVE_") or code == "SAFE_OPEN":
            triggers.append(Trigger("cashier_leave", start, end, 9000, {"source": "oracle_annotation"}))
        elif code == "DOOR_OPEN":
            triggers.append(Trigger("cashier_enter", start, end, 9000, {"source": "oracle_annotation"}))
        count = max(1, int(round((end-start)*5))+1)
        for i in range(count):
            t = round(min(end, start + i*0.2), 4)
            scene = scenes_by_t.setdefault(t, SceneState(t, None, [], None, None, None))
            absent = code.startswith("LEAVE_") or code == "SAFE_OPEN"
            presence[t] = {"cashier_present": not absent, "cashier_seated": not absent,
                           "cashier_near_safe": False if absent else None, "source": "oracle_annotation"}
            text = " ".join(row.get(k, "") for k in ("observed", "type_name", "notes")).lower()
            item = {"LEAVE_VALUABLES": "cash_bag" if "сумк" in text else "cash",
                    "PERSONAL_ITEMS": "clothing_or_bag" if "сумк" in text or "одежд" in text else "cup",
                    "PHONE_USE": "phone"}.get(code)
            if item:
                scene.desk_items.append(DeskItem(item, (0.3, 0.3, 0.5, 0.5), 1.0))
            if code == "LEAVE_SCREEN_UNLOCKED":
                scene.screen_state = "document"
            if code == "SAFE_OPEN":
                scene.safe_open = 1.0
            if code == "DOOR_OPEN":
                scene.door_open = 1.0
            if code == "MANUAL_COUNT":
                scene.cashier_hand_counting = 1.0
    return triggers, sorted(scenes_by_t.values(), key=lambda s: s.t), presence, unsupported

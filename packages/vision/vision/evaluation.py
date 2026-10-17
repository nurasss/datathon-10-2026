"""Offline scoring only. Labels in this module never enter vision inference."""
from .oracle import code_for_annotation

LEAVE_CASES = {f"X-{n}" for n in (11, 13, 15, 22, 24, 26, 28)}
ENTER_CASES = {"X-32", "X-33"}
NORMAL_ACTIONS = {**{f"X-{n}": "cashier_leave" for n in (12, 23, 25, 27, 29)}, "X-31": "cashier_enter"}


def type_comparison(events, annotations):
    expected = {code_for_annotation(a) for a in annotations} - {None}
    predicted = {e.event_code for e in events}
    return {"label_codes": ";".join(sorted(expected)), "type_ok": expected == predicted,
            "type_hit": bool(expected & predicted)}


def score_actions(video, triggers, annotations, tolerance=2.0):
    kind = ("cashier_leave" if video in LEAVE_CASES else "cashier_enter" if video in ENTER_CASES
            else NORMAL_ACTIONS.get(video))
    if kind is None:
        return None
    times = [float(t["t"]) for t in triggers if t["kind"] == kind]
    normal = video in NORMAL_ACTIONS
    # NONE.start_sec is the beginning of the clip, not a labelled departure.
    expected = None if normal else min(float(a["start_sec"]) for a in annotations if a["type_code"] != "NONE")
    nearest = min(times, key=lambda t: abs(t-expected)) if times and expected is not None else None
    return {"video": video, "kind": kind, "normal": normal, "expected_sec": expected,
            "predicted_sec": times, "nearest_sec": nearest,
            "delta_sec": nearest-expected if nearest is not None else None,
            "timing_ok": abs(nearest-expected) <= tolerance if nearest is not None else False if expected is not None else None,
            "present": bool(times), "extra_triggers": max(0, len(times)-1), "tolerance_sec": tolerance,
            "reference_note": "NONE has no action timestamp; presence only" if normal else "events.csv start_sec"}


def action_summary(rows):
    timed = [r for r in rows if r["expected_sec"] is not None]
    normal = [r for r in rows if r["normal"]]
    hits = sum(r["timing_ok"] for r in timed)
    return {"labelled_actions": len(timed), "within_tolerance": hits,
            "within_tolerance_fraction": hits/len(timed) if timed else None,
            "normal_actions": len(normal), "normal_actions_present": sum(r["present"] for r in normal),
            "normal_without_violation": sum(r.get("normal_no_violation", False) for r in normal) if any("normal_no_violation" in r for r in normal) else None,
            "rows": rows}

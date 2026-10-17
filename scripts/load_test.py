#!/usr/bin/env python3
"""HTTP API concurrent load test.

Sends concurrent POST requests for the configured clips. Actual durations and
latencies come from the saved run, not approximate values in this docstring.

Monitors each job from POST -> done and records latency and final status.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from datetime import datetime
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx

CLIPS = [
    "Х-14.mov",
    "Х-39.mov",
    "Х-13.mov",
    "Х-38.mov",
    "Х-20.mov",
]


def run_one_clip(
    base_url: str,
    api_key: str,
    video_path: Path,
    poll_interval: float = 1.0,
    timeout_sec: float = 300.0,
    barrier: threading.Barrier | None = None,
) -> dict[str, Any]:
    clip_name = video_path.stem.replace("\u0425", "X")
    post_start = time.perf_counter()
    wall_start = time.time()
    raw = []
    if barrier:
        barrier.wait(timeout=30)

    with open(video_path, "rb") as fh:
        files = {"file": (video_path.name, fh, "video/quicktime")}
        headers = {"X-API-Key": api_key}
        with httpx.Client(base_url=base_url, timeout=60.0) as client:
            resp = client.post("/api/v1/analyze", files=files, headers=headers)
            raw.append({"phase": "post", "elapsed_sec": time.perf_counter()-post_start,
                        "status_code": resp.status_code, "body": resp.json()})
            if resp.status_code not in (200, 202):
                return {
                    "clip": clip_name,
                    "file": video_path.name,
                    "job_id": None,
                    "status": f"HTTP_{resp.status_code}",
                    "verdict": None,
                    "elapsed_sec": round(time.perf_counter() - post_start, 2),
                    "error": resp.text[:200],
                    "raw_http": raw,
                }
            job_data = resp.json()
            job_id = job_data["job_id"]

    # Poll until done or failed
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        while time.perf_counter() - post_start < timeout_sec:
            poll_resp = client.get(f"/api/v1/jobs/{job_id}", headers={"X-API-Key": api_key})
            raw.append({"phase": "poll", "elapsed_sec": time.perf_counter()-post_start,
                        "status_code": poll_resp.status_code, "body": poll_resp.json()})
            if poll_resp.status_code == 200:
                data = poll_resp.json()
                st = data.get("status")
                if st in ("done", "failed"):
                    elapsed = round(time.perf_counter() - post_start, 2)
                    return {
                        "clip": clip_name,
                        "file": video_path.name,
                        "job_id": job_id,
                        "status": st,
                        "verdict": data.get("verdict"),
                        "elapsed_sec": elapsed,
                        "error": data.get("error_message"),
                        "progress": data.get("progress"),
                        "started_at": wall_start,
                        "raw_http": raw,
                    }
            time.sleep(poll_interval)

    return {
        "clip": clip_name,
        "file": video_path.name,
        "job_id": job_id,
        "status": "TIMEOUT",
        "verdict": None,
        "elapsed_sec": round(time.perf_counter() - post_start, 2),
        "error": "Polling exceeded timeout",
        "raw_http": raw,
    }


def run_load_test(
    base_url: str,
    api_key: str,
    videos_dir: Path,
) -> dict[str, Any]:
    tasks = []
    for clip in CLIPS:
        p = videos_dir / clip
        if not p.is_file():
            # Try ascii X
            alt = clip.replace("\u0425", "X")
            p = videos_dir / alt
        if not p.is_file():
            raise FileNotFoundError(f"Clip not found: {clip} in {videos_dir}")
        tasks.append(p)

    results = []
    barrier = threading.Barrier(len(tasks))
    t_global_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = {
            executor.submit(run_one_clip, base_url, api_key, p, barrier=barrier): p.name
            for p in tasks
        }
        for fut in concurrent.futures.as_completed(futures):
            try:
                res = fut.result()
            except Exception as exc:
                res = {"clip": futures[fut], "status": "CLIENT_ERROR", "error": repr(exc)}
            results.append(res)
            print(f"Finished {res['clip']}: status={res['status']}, elapsed={res['elapsed_sec']}s, verdict={res.get('verdict')}")

    total_time = round(time.perf_counter() - t_global_start, 2)
    print(f"\nAll 5 jobs completed in total wall time: {total_time}s")
    # Preserve order of CLIPS
    order = {c.replace("\u0425", "X").replace(".mov", ""): i for i, c in enumerate(CLIPS)}
    results.sort(key=lambda r: order.get(r["clip"], 999))
    return {"total_wall_sec": total_time, "concurrent_submissions": len(tasks), "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="HTTP load test for ForteVision.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--api-key", default=os.environ.get("FV_API_KEYS", "").split(",")[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--reports-dir", type=Path, help="Local worker reports to embed, without evidence bytes")
    parser.add_argument("--videos-dir", type=Path, default=Path(__file__).resolve().parents[2] / "data_raw" / "open" / "videos")
    args = parser.parse_args()

    if not args.api_key:
        parser.error("set FV_API_KEYS or pass --api-key")
    root = Path(__file__).resolve().parents[1]
    out = args.out or root / "artifacts/load" / f"{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        parser.error("refusing to overwrite load artifacts")
    result = run_load_test(args.base_url, args.api_key, args.videos_dir)
    result.update({"base_url": args.base_url, "created_at": datetime.now().isoformat(),
                   "worker_concurrency_configured": 2})
    if args.reports_dir:
        for row in result["results"]:
            report = args.reports_dir / f"{row.get('job_id')}.json"
            if report.exists():
                row["worker_report"] = json.loads(report.read_text())
            log = report.with_suffix(".log")
            if log.exists():
                row["worker_log"] = log.read_text(errors="replace")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Raw load artifact: {out}")
    return 0 if all(r["status"] == "done" for r in result["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

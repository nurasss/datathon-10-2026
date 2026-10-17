#!/usr/bin/env python3
"""HTTP API concurrent load test.

Sends 5 concurrent POST requests with clips:
- Х-14 (~142s)
- Х-39 (~53s)
- Х-13 (~65s)
- Х-38 (~40s)
- Х-20 (~12s)

Monitors each job from POST -> done and records latency and final status.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
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
) -> dict[str, Any]:
    clip_name = video_path.stem.replace("\u0425", "X")
    post_start = time.perf_counter()

    with open(video_path, "rb") as fh:
        files = {"file": (video_path.name, fh, "video/quicktime")}
        headers = {"X-API-Key": api_key}
        with httpx.Client(base_url=base_url, timeout=60.0) as client:
            resp = client.post("/api/v1/analyze", files=files, headers=headers)
            if resp.status_code not in (200, 202):
                return {
                    "clip": clip_name,
                    "file": video_path.name,
                    "job_id": None,
                    "status": f"HTTP_{resp.status_code}",
                    "verdict": None,
                    "elapsed_sec": round(time.perf_counter() - post_start, 2),
                    "error": resp.text[:200],
                }
            job_data = resp.json()
            job_id = job_data["job_id"]

    # Poll until done or failed
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        while time.perf_counter() - post_start < timeout_sec:
            poll_resp = client.get(f"/api/v1/jobs/{job_id}", headers={"X-API-Key": api_key})
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
    }


def run_load_test(
    base_url: str,
    api_key: str,
    videos_dir: Path,
) -> list[dict[str, Any]]:
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
    t_global_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = {
            executor.submit(run_one_clip, base_url, api_key, p): p.name
            for p in tasks
        }
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            results.append(res)
            print(f"Finished {res['clip']}: status={res['status']}, elapsed={res['elapsed_sec']}s, verdict={res.get('verdict')}")

    total_time = round(time.perf_counter() - t_global_start, 2)
    print(f"\nAll 5 jobs completed in total wall time: {total_time}s")
    # Preserve order of CLIPS
    order = {c.replace("\u0425", "X").replace(".mov", ""): i for i, c in enumerate(CLIPS)}
    results.sort(key=lambda r: order.get(r["clip"], 999))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="HTTP load test for ForteVision.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default="loadtest-secret-key-1")
    parser.add_argument("--videos-dir", type=Path, default=Path(__file__).resolve().parents[2] / "data_raw" / "open" / "videos")
    args = parser.parse_args()

    results = run_load_test(args.base_url, args.api_key, args.videos_dir)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

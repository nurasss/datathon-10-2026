#!/usr/bin/env python3
"""Real curl acceptance + sequential API benchmark. Never prints API/UI secrets."""

from __future__ import annotations

import argparse
import base64
import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for extra in ("apps/api", "packages/vision", "packages/rules"):
    sys.path.insert(0, str(ROOT / extra))

from app.schemas import DoneJob  # noqa: E402
from vision.config import PipelineConfig  # noqa: E402
from vision.evidence import encode_frame  # noqa: E402
from vision.videoprobe import FrameReader, probe  # noqa: E402


def redact_images(body):
    body = copy.deepcopy(body)
    for violation in body.get("violations", []):
        for frame in violation["evidence_frames"]:
            frame["image_base64"] = frame["image_base64"][:40] + "…"
    return body


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--videos", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts/acceptance")
    args = parser.parse_args()
    key = os.environ["FV_API_KEYS"].split(",")[0]
    args.out.mkdir(parents=True, exist_ok=True)
    transcript = []

    def log(text):
        transcript.append(text)
        print(text, flush=True)

    def curl(path, *, file=None, filename=None, auth=key, show=True):
        cmd = ["curl", "-sS", "--max-time", "30", "-w", "\nHTTP %{http_code}\n"]
        if auth is not None:
            cmd += ["-H", f"X-API-Key: {auth}"]
        if file is not None:
            field = f"file=@{file}"
            if filename:
                field += f";filename={filename}"
            cmd += ["-F", field]
        cmd += [args.url + path]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        raw, status = proc.stdout.rsplit("\nHTTP ", 1)
        if show:
            masked = [s.replace(key, "***MASKED***") for s in cmd]
            log("$ " + shlex.join(masked))
            if show == "redacted":
                log(json.dumps(redact_images(json.loads(raw)), ensure_ascii=False, indent=2)
                    + "\nHTTP " + status.strip())
            else:
                log(proc.stdout.rstrip())
        return int(status.strip()), json.loads(raw)

    durations = []
    for file in args.videos.glob("*.mov"):
        durations.append((probe(file).duration_sec, file))
    durations.sort()
    selected = [durations[0], durations[len(durations) // 2], durations[-1]]
    assert selected[0][1].stem.replace("Х", "X") == "X-20"
    assert selected[-1][1].stem.replace("Х", "X") == "X-14"

    log("AT-01: valid .mov")
    started = time.perf_counter()
    status, accepted = curl("/api/v1/analyze", file=selected[0][1])
    post_sec = time.perf_counter() - started
    assert status == 202 and set(accepted) == {"job_id"}
    first_id = accepted["job_id"]
    log("AT-02: missing / invalid key (POST and GET)")
    for auth in (None, "wrong-key"):
        for path, file in (("/api/v1/analyze", selected[0][1]), (f"/api/v1/jobs/{first_id}", None)):
            assert curl(path, file=file, auth=auth)[0] == 401
    log("AT-03: unknown id")
    assert curl("/api/v1/jobs/unknown-job-id")[0] == 404
    log("AT-04: non-.mov filename")
    assert curl("/api/v1/analyze", file=selected[0][1], filename="not-video.txt")[0] == 415

    results = []
    cfg = PipelineConfig.load(ROOT / "configs/inference.default.json")
    for index, (duration, file) in enumerate(selected):
        if index == 0:
            job_id, start, post = first_id, started, post_sec
        else:
            start = time.perf_counter()
            status, accepted = curl("/api/v1/analyze", file=file)
            post = time.perf_counter() - start
            assert status == 202
            job_id = accepted["job_id"]
        while True:
            status, body = curl(f"/api/v1/jobs/{job_id}", show=False)
            assert status == 200
            if body["status"] != "processing":
                break
            if time.perf_counter() - start > 285:
                raise RuntimeError("API failed to enforce deadline")
            time.sleep(0.2)
        elapsed = time.perf_counter() - start
        DoneJob.model_validate(body)  # A failed job is not a successful benchmark.
        exact_frames = 0
        with FrameReader(file, probe(file)) as reader:
            for event in body["violations"]:
                assert 0 <= event["timecode_sec"] <= duration
                for frame in event["evidence_frames"]:
                    assert 0 <= frame["timestamp_sec"] <= duration
                    image = reader.read_frame_at(frame["timestamp_sec"])
                    jpeg = encode_frame(image, frame["timestamp_sec"],
                                        max_width=cfg.evidence.max_width, quality=cfg.evidence.jpeg_quality)
                    assert base64.b64decode(frame["image_base64"], validate=True) == jpeg.jpeg_bytes
                    exact_frames += 1
        (args.out / f"{job_id}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        curl(f"/api/v1/jobs/{job_id}", show="redacted")
        row = dict(file=file.name, job_id=job_id, duration_sec=duration,
                   post_sec=round(post, 3), post_to_done_sec=round(elapsed, 3),
                   status=body["status"], verdict=body["verdict"], exact_source_jpegs=exact_frames)
        results.append(row)
        log(json.dumps(row, ensure_ascii=False))
    (args.out / "timings.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    (args.out / "curl.txt").write_text("\n\n".join(transcript) + "\n")


if __name__ == "__main__":
    main()

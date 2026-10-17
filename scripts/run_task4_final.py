#!/usr/bin/env python3
"""Single authorized task-4 full evaluate, with an exclusive invocation marker."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT / "artifacts/task4"
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / "full_run.json"
    # A failed run still consumes the invocation: do not silently replace it.
    with marker.open("x") as f:
        record = {"invocations": 1, "started_at": datetime.now(timezone.utc).isoformat(),
                  "state": "started", "python": sys.executable}
        json.dump(record, f, indent=2)
    out = folder / "final"
    out.mkdir(exist_ok=False)
    cmd = [sys.executable, "-u", str(ROOT / "scripts/evaluate.py"),
           "--no-sheets", "--oracle-events", str(ROOT / "data/annotations/events.csv"),
           "--baseline-dir", str(folder / "baseline"), "--out-dir", str(out)]
    record["argv"] = cmd
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    with (out / "stdout.log").open("w") as log:
        result = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    record.update(state="completed", returncode=result.returncode,
                  completed_at=datetime.now(timezone.utc).isoformat(), output=str(out))
    marker.write_text(json.dumps(record, indent=2))
    print(f"Full task-4 evaluate: exit {result.returncode}; {out}", flush=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

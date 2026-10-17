#!/usr/bin/env python3
"""One isolated local load run, own server ONLY on 127.0.0.1:8001.

Fresh data directory and random ephemeral API key. Always stops its own server.
Raw HTTP polling, worker reports/logs and docs status codes are retained.
"""
from datetime import datetime
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time

import httpx
from load_test import run_load_test

ROOT = Path(__file__).resolve().parents[1]


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "artifacts/load" / f"{ts}.json"
    data = ROOT / "artifacts/load/runtime" / ts
    data.mkdir(parents=True, exist_ok=False)
    # Fail before sending jobs if another process owns the dedicated test port.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 8001))
    env = dict(os.environ)
    key = secrets.token_urlsafe(24)
    env.update(FV_API_KEYS=key, FV_DATA_DIR=str(data), FV_PROJECT_ROOT=str(ROOT),
               FV_INFERENCE_CONFIG=str(ROOT / "configs/inference.default.json"), FV_ENABLE_DOCS="0",
               FV_WORKER_CONCURRENCY="2", FV_WORKER_POLL_SEC="0.1", FV_JOB_TIMEOUT_SEC="280",
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    env["PYTHONPATH"] = ":".join([str(ROOT / p) for p in ("apps/api", "packages/vision", "packages/rules")]+[env.get("PYTHONPATH", "")])
    base = "http://127.0.0.1:8001"
    result = {"base_url": base, "created_at": ts, "worker_concurrency": 2,
              "config": json.loads((ROOT / "configs/inference.default.json").read_text())}
    with (data / "server.log").open("w") as log:
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                   "--port", "8001", "--workers", "1"], cwd=ROOT, env=env,
                                  stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            with httpx.Client(base_url=base, timeout=5) as client:
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError("test server exited; see server.log")
                    try:
                        if client.get("/api/v1/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.2)
                else:
                    raise RuntimeError("server startup timeout")
                paths = ["/docs", "/redoc", "/openapi.json", "/api/docs", "/api/redoc", "/api/openapi.json"]
                result["docs_status"] = {p: client.get(p).status_code for p in paths}
                if any(status != 404 for status in result["docs_status"].values()):
                    raise RuntimeError("docs must be closed before load run")
            # Exactly ONE invocation / five concurrent uploads.
            result.update(run_load_test(base, key, ROOT.parent / "data_raw/open/videos"))
            for row in result["results"]:
                report = data / "reports" / f"{row.get('job_id')}.json"
                if report.exists():
                    row["worker_report"] = json.loads(report.read_text())
                if report.with_suffix(".log").exists():
                    row["worker_log"] = report.with_suffix(".log").read_text(errors="replace")
            with __import__("sqlite3").connect(data / "jobs.db") as conn:
                conn.row_factory = __import__("sqlite3").Row
                result["raw_jobs"] = [dict(row) for row in conn.execute("SELECT * FROM jobs")]
        except Exception as exc:
            result["error"] = repr(exc)
            raise
        finally:
            if server.poll() is None:
                os.killpg(server.pid, signal.SIGTERM)
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait(timeout=5)
            result["server_returncode"] = server.returncode
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Load artifact: {out}", flush=True)
    return 0 if all(r["status"] == "done" for r in result["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Child-process entry point: ``python -m app.job_entry``.

Running the analysis in a separate OS process gives the API a hard kill switch
and keeps the web process responsive while one job occupies the CPU.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for extra in ("apps/api", "packages/vision", "packages/rules"):
    candidate = str(ROOT / extra)
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from vision.job_runner import run_analysis_job  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="ForteVision analysis child process")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--db", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--project-root", default=str(ROOT))
    args = parser.parse_args()

    # Keep the child single-threaded and modest about memory: the target box has
    # 2 vCPU and under 1 GB free RAM.
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    run_analysis_job(
        job_id=args.job_id,
        video_path=args.video,
        out_json=args.out,
        evidence_dir=args.evidence_dir,
        config_path=args.config,
        db_path=args.db,
        project_root=args.project_root,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

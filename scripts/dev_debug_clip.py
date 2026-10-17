#!/usr/bin/env python3
"""Debug a single clip: dump zone choice, states and (optionally) the trace.

Usage:
    python scripts/dev_debug_clip.py CLIP [--trace] [--detector color|nanodet]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in ("packages/vision", "packages/rules", "tests"):
    sys.path.insert(0, str(ROOT / extra))

from vision.config import PipelineConfig  # noqa: E402
from vision.pipeline import AnalysisOptions, VideoAnalyzer  # noqa: E402
from rules.registry import RuleRegistry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("clip")
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--detector", choices=["color", "nanodet"], default="nanodet")
    parser.add_argument("--config", default=str(ROOT / "configs" / "inference.default.json"))
    args = parser.parse_args()

    cfg = PipelineConfig.load(args.config)
    detector = None
    if args.detector == "color":
        from support import ColorPersonDetector

        detector = ColorPersonDetector()
    analyzer = VideoAnalyzer(
        cfg=cfg, detector=detector, registry=RuleRegistry.load(), project_root=ROOT
    )
    result = analyzer.analyze(args.clip, options=AnalysisOptions(job_id="debug", keep_trace=args.trace))
    payload = {
        "verdict": result.verdict,
        "duration": result.meta.duration_sec,
        "zones": [z.as_dict() for z in result.zones],
        "quality": {k: v for k, v in result.quality.items() if k != "trace"},
        "events": [
            {
                "code": e.event_code,
                "start": e.start_sec,
                "peak": e.peak_sec,
                "end": e.end_sec,
                "conf": e.confidence,
                "meta": {k: v for k, v in e.meta.items() if k != "evidence"},
            }
            for e in result.events
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.trace:
        trace = result.quality.get("trace", [])
        header = "    t  post client n_post n_cntr n_oth  motion     fg    blob  bx_h"
        print(header)
        for row in trace:
            print(
                f"{row['t']:6.2f}  {int(row['post']):>4} {int(row['client']):>6} "
                f"{row['n_post']:>6} {row['n_counter']:>6} {row['n_other']:>6} "
                f"{row['motion']:>6.1f} {row['fg']:>7.4f} {row['blob']:>7.4f} {row['post_box_h']:>5.2f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

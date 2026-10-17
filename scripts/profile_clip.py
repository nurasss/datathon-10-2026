#!/usr/bin/env python3
"""Profile the real two-pass pipeline; save every timing to an artifact."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for extra in ("vision", "rules"):
    sys.path.insert(0, str(ROOT / "packages" / extra))
from vision.config import PipelineConfig
from vision.pipeline import AnalysisOptions, VideoAnalyzer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/inference.default.json")
    parser.add_argument("--size", type=int, choices=[320, 416], help="Uniform resolution in both passes; omit for production hybrid")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    cfg = PipelineConfig.load(args.config)
    if args.size:
        cfg.detector.input_size = cfg.detector.coarse_input_size = args.size
    if args.size == 320:
        cfg.detector.model_path = "packages/vision/models/nanodet_320.onnx"
    start = time.perf_counter()
    analyzer = VideoAnalyzer(cfg, project_root=ROOT)
    init_sec = time.perf_counter()-start
    progress = []
    out = args.out or ROOT / "artifacts/profile" / f"{datetime.now():%Y%m%d_%H%M%S}_{args.video.stem}_{args.size}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    def report(phase, pct):
        progress.append({"phase": phase, "progress": pct, "elapsed_sec": time.perf_counter()-start})
        out.with_suffix(".progress.json").write_text(json.dumps(progress, indent=2))
    result = analyzer.analyze(args.video, AnalysisOptions(keep_trace=True), progress=report)
    payload = {"video": args.video.name, "size": args.size, "init_sec": init_sec,
               "wall_including_init_sec": time.perf_counter()-start, "timings": result.timings,
               "config": cfg.to_dict(), "detector_sha256": analyzer.detector.sha256,
               "quality": result.quality, "verdict": result.verdict,
               "events": [{"code": e.event_code, "start": e.start_sec, "end": e.end_sec} for e in result.events],
               "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"artifact": str(out), "wall_sec": payload["wall_including_init_sec"], "timings": result.timings,
                      "windows": result.quality["windows"], "events": payload["events"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

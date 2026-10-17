#!/usr/bin/env python3
"""Verify final task-6 artifacts, no detector/scene inference or HTTP calls."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for package in ('vision','rules'):
    sys.path.insert(0,str(ROOT/'packages'/package))
from rules.registry import RuleRegistry, TAXONOMY
from vision.evidence import encode_frame
from vision.videoprobe import FrameReader, probe


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    directory = ROOT/'artifacts/task6/final'
    report = ROOT/'artifacts/task6/report'
    report.mkdir(exist_ok=True)
    run = json.loads((directory/'run.json').read_text())
    marker = json.loads((directory.parent/'full_run.json').read_text())
    registry = RuleRegistry.load()
    failures, source_records, zero_onsets = [], [], []
    for path,expected in run['source_sha256'].items():
        if sha(ROOT/path)!=expected:
            failures.append(f'source changed after evaluation start: {path}')
    for name in ('events.py','deskobjects.py'):
        path = f'packages/vision/vision/{name}'
        old = subprocess.check_output(['git','show',f'5f4ad60:{path}'],cwd=ROOT)
        if old!=(ROOT/path).read_bytes():
            failures.append(f'legacy module changed: {name}')
    for file in [ROOT/'packages/vision/vision/scene_state.py',*(ROOT/'packages/vision/vision/scene').glob('*.py')]:
        path = str(file.relative_to(ROOT))
        old = subprocess.check_output(['git','show',f'7554a6b:{path}'],cwd=ROOT)
        if old!=file.read_bytes():
            failures.append(f'scene modified: {path}')
    source = json.loads((directory.parent/'provenance/registry_source.json').read_text())
    approved = json.loads((ROOT/'packages/rules/rules/registry.json').read_text())
    if source['rules']!=approved['rules'] or source['acts']!=approved['acts']:
        failures.append('curator registry quote/act mismatch')
    if registry.for_event('LEAVE_DURING_SERVICE') is None:
        failures.append('service norm not approved')
    paths = {p.stem.replace('Х','X'):p for p in (ROOT.parent/'data_raw/open/videos').glob('*.mov')}
    settings = run['effective_config']['evidence']
    count, image_count = 0, 0
    for file in sorted((directory/'trace').glob('*.json')):
        trace = json.loads(file.read_text())
        count += 1
        if trace.get('error'):
            failures.append(f'analysis error: {file.stem}')
        hybrid = trace['quality'].get('hybrid',{})
        if not hybrid.get('enabled') or hybrid.get('detector_calls_added')!=0:
            failures.append(f'B inference reuse contract: {file.stem}')
        if trace['quality']['scene_context'].get('enable_colab_yolox') is not False:
            failures.append(f'YOLOX not explicitly disabled: {file.stem}')
        if trace['quality']['inference_calls']!=len(trace['quality']['trace']):
            failures.append(f'number of model calls differs from unique saved observations: {file.stem}')
        all_frames = [f for event in trace['events'] for f in event['evidence_frames']]
        originals = {}
        if all_frames:
            video = paths[trace['video']]
            with FrameReader(video,probe(video)) as reader:
                originals = reader.read_frames_at([f['timestamp_sec'] for f in all_frames])
        for event in trace['events']:
            if event['code'] not in TAXONOMY or registry.for_event(event['code']) is None:
                failures.append(f'unknown or unapproved event: {file.stem} {event["code"]}')
            frames = event['evidence_frames']
            if len(frames)!=3 or frames[1]['timestamp_sec']!=event['start_sec'] or event['timecode_sec']!=event['start_sec']:
                failures.append(f'evidence/onset mismatch: {file.stem}')
            if event['start_sec']==0:
                zero_onsets.append(dict(video=file.stem,code=event['code']))
            for record in frames:
                image_count += 1
                path = directory/record['path']
                image = cv2.imdecode(np.frombuffer(path.read_bytes(),dtype=np.uint8),cv2.IMREAD_COLOR)
                if image is None or sha(path)!=record['sha256']:
                    failures.append(f'invalid JPEG: {path}')
                original = originals.get(record['timestamp_sec'])
                if original is None:
                    failures.append(f'missing source frame: {path}')
                    continue
                encoded = encode_frame(original,record['timestamp_sec'],max_width=settings['max_width'],quality=settings['jpeg_quality'])
                actual = hashlib.sha256(encoded.jpeg_bytes).hexdigest()
                equal = actual==record['sha256']
                source_records.append(dict(path=str(path),timestamp_sec=record['timestamp_sec'],source_sha256=actual,equal=equal))
                if not equal:
                    failures.append(f'JPEG differs from source: {path}')
    if count!=40 or marker.get('invocations')!=1 or marker.get('returncode')!=0:
        failures.append('full-run protocol/count/completion failure')
    payload = dict(traces=count,images=image_count,source_frames_checked=len(source_records),
                   failures=failures,full_run=marker,source_git_head=run['git_head'],
                   scene_unchanged_from='7554a6b',legacy_unchanged_from='5f4ad60',
                   onsets_at_recording_start_cannot_have_earlier_frame=zero_onsets,
                   source_frame_records=source_records)
    (report/'integrity.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in payload.items() if k!='source_frame_records'},ensure_ascii=False,indent=2))
    return bool(failures)


if __name__=='__main__':
    raise SystemExit(main())

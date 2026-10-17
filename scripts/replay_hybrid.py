#!/usr/bin/env python3
"""Replay A+B using frozen detections/scenes and a tiny source-pixel cache.

Cache preparation decodes source video, but NEVER calls a detector or scene model.
Subsequent parameter trials read only trace JSON and NPZ, not video.
"""
import argparse
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time
import subprocess

import numpy as np
import cv2

ROOT = Path(__file__).resolve().parents[1]
for package in ('vision','rules'):
    sys.path.insert(0,str(ROOT/'packages'/package))
from vision.config import PipelineConfig
from vision.deskobjects import DeskWatcher
from vision.events import detect_ev01
from vision.hybrid import baseline_states, combine_layers, desk_candidates, infer_baseline_zones
from vision.oracle import load_annotations
from vision.evaluation import type_comparison
from vision.rule_engine import evaluate_rules
from vision.scene_state import SceneState, DeskItem
from vision.tracking import track_people
from vision.triggers import Trigger
from vision.types import Detection, FrameObservation, ZoneSpec
from vision.videoprobe import FrameReader, probe
from rules.registry import RuleRegistry


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=ROOT/'artifacts/task5/baseline')
    p.add_argument('--config',type=Path,default=ROOT/'configs/inference.default.json')
    p.add_argument('--cache',type=Path,default=ROOT/'artifacts/task6/pixels')
    p.add_argument('--videos',type=Path,default=ROOT.parent/'data_raw/open/videos')
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--prepare-cache',action='store_true')
    p.add_argument('--only',default='')
    args = p.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    args.cache.mkdir(parents=True,exist_ok=True)
    cfg = PipelineConfig.load(args.config)
    source_hashes = {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest()
                     for pattern in ('packages/vision/vision/*.py','packages/rules/rules/*.py','configs/*.json')
                     for f in ROOT.glob(pattern)}
    source_run = json.loads((args.source/'run.json').read_text())
    source_cfg = source_run.get('effective_config', source_run['config'])
    annotations = load_annotations(args.source/'events_input.csv')
    source_rows = {r['video'].split(' ')[0]:r for r in csv.DictReader((args.source/'results.csv').open())}
    paths = {f.stem.replace('Х','X'):f for f in args.videos.glob('*.mov')}
    registry = RuleRegistry.load()
    rows = []
    wanted = set(args.only.split(',')) if args.only else None
    for file in sorted((args.source/'trace').glob('*.json'),key=lambda f:int(f.stem.split('-')[1])):
        if wanted and file.stem not in wanted:
            continue
        started = time.perf_counter()
        data = json.loads(file.read_text())
        width,height = data['quality'].get('frame_size',[464,848])
        frames = data['quality']['trace']
        coarse = (frames if cfg.hybrid.input_grid=='observed' else
                  [f for f in frames if f['pass'] in {'coarse','dense_reused'}])
        if not coarse:
            raise ValueError(f'{file}: original coarse sample markers unavailable')
        cache_key = hashlib.sha256(file.read_bytes()+cfg.hybrid.input_grid.encode()).hexdigest()[:20]
        cache = args.cache/(file.stem+'_'+cache_key+'.npz')
        if not cache.exists():
            if not args.prepare_cache:
                raise ValueError(f'missing pixel cache: {cache}; explicitly prepare once')
            selected = {round(f['t'],4):f['pass'] for f in coarse}
            grays = {}
            watcher = DeskWatcher(cfg.desk)
            video = paths[data['video']]
            with FrameReader(video,probe(video)) as reader:
                for t,frame in reader.iter_sampled(source_cfg['sampling']['dense_fps']):
                    key = round(t,4)
                    if key in selected:
                        if selected[key]=='dense':
                            _,encoded = cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,95])
                            frame = cv2.imdecode(encoded,cv2.IMREAD_COLOR)
                        grays[key] = watcher.downscale(frame)
            if set(grays) != set(selected):
                raise ValueError(f'{file}: source frame grid mismatch')
            np.savez_compressed(cache,times=np.array([f['t'] for f in coarse]),
                                gray=np.stack([grays[f['t']] for f in coarse]))
        cached = np.load(cache)
        if cached['times'].tolist() != [f['t'] for f in coarse]:
            raise ValueError('cached timestamp mismatch')
        gray_frames = list(cached['gray'])
        # Resample only existing cached observations; no synthesized detections.
        interval = 1/(cfg.sampling.dense_fps if cfg.hybrid.input_grid=='observed' else cfg.sampling.coarse_fps)
        chosen, target = [], 0.
        for i,f in enumerate(coarse):
            if f['t']+interval*.3 >= target:
                chosen.append(i)
                target += interval
        coarse = [coarse[i] for i in chosen]
        gray_frames = [gray_frames[i] for i in chosen]
        observations = []
        for i,f in enumerate(coarse):
            raw = f.get('tracking_detections',f['detections'])
            people = [Detection(d['box'][0]*width,d['box'][1]*height,d['box'][2]*width,d['box'][3]*height,d['score'])
                      for d in raw if d['cls']=='person']
            observations.append(FrameObservation(f['t'],i,people))
        zones = [ZoneSpec(**z) for z in data['quality']['zones']]
        post,counter = zones[:2]
        door = next(z for z in zones if z.name=='door')
        tracks,assigned = track_people(observations,post,door,width,height,cfg.triggers)
        high_assigned = {i:[(tr,d) for tr,d in ps if d.score>=cfg.detector.min_score] for i,ps in assigned.items()}
        for obs in observations:
            obs.detections = [d for d in obs.detections if d.score>=cfg.detector.min_score]
        b_zones = infer_baseline_zones(observations,width,height,cfg)
        post,counter = b_zones
        dt = interval
        states = baseline_states(observations,high_assigned,post,counter,dt)
        raw1 = detect_ev01(states,cfg.ev01,dt,{'zone_confidence':post.confidence})
        raw2,blobs = desk_candidates(observations,gray_frames,post,width,cfg,states,dt)
        scenes,presence = [],{}
        for f in frames:
            presence[f['t']] = {k:f.get(k) for k in ('cashier_present','cashier_seated','cashier_near_safe')}
            if f.get('scene'):
                s = dict(f['scene'])
                s['desk_items'] = [DeskItem(**d) for d in s['desk_items']]
                scenes.append(SceneState(**s))
        triggers = [Trigger(**t) for t in data['quality']['triggers']]
        a = evaluate_rules(triggers,scenes,presence,data['duration_sec'],registry,cfg.rules)[:cfg.max_events]
        decisions = []
        events = combine_layers(a,raw1,raw2,scenes,data['duration_sec'],registry,cfg,decisions)
        truth = source_rows[data['video']]['label']=='violation'
        pred = bool(events)
        outcome = ('TP' if truth else 'FP') if pred else ('FN' if truth else 'TN')
        row = dict(video=data['video'],outcome=outcome,pred_codes=';'.join(sorted({e.event_code for e in events})),
                   events=';'.join(f'{e.event_code}@{e.start_sec:.2f}' for e in events),
                   **type_comparison(events,annotations.get(data['video'],[])))
        rows.append(row)
        payload = {'mode':'cached input replay, NOT end-to-end evaluation','source':str(file),
                   'pixel_cache':str(cache),'neural_inference_calls':0,'states':[asdict(s) for s in states],
                   'A_events':[{'code':e.event_code,'start':e.start_sec,'end':e.end_sec} for e in a],
                   'raw_ev01':raw1,'raw_ev02':raw2,'decisions':decisions,'result':row,
                   'replay_runtime_sec':time.perf_counter()-started}
        (args.out/file.name).write_text(json.dumps(payload,ensure_ascii=False,indent=2))
        print(row['video'],outcome,row['events'],flush=True)
    tp = [r for r in rows if r['outcome']=='TP']
    summary = {k:sum(r['outcome']==k for r in rows) for k in ('TP','FP','FN','TN')}
    summary.update(mode='cached replay, not a vision score',neural_inference_calls=0,
                   type_hit_among_tp=sum(r['type_hit'] for r in tp)/len(tp) if tp else None,
                   config=cfg.to_dict(),source=str(args.source),source_sha256=source_hashes,
                   git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                   pixel_cache_note='observed-grid dense frames recreate JPEG95 cache before downscaling; coarse/reused frames use original pixels')
    (args.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    with (args.out/'results.csv').open('w') as f:
        writer = csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


if __name__=='__main__':
    main()

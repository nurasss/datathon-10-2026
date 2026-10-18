#!/usr/bin/env python3
"""File-only before/after and mirror report; never starts inference."""
import csv
import hashlib
import json
from pathlib import Path
from statistics import median
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT/'artifacts/task8'
sys.path.insert(0,str(ROOT/'packages/vision'))
from vision.evidence import encode_frame
from vision.videoprobe import FrameReader, probe


def summarize(folder):
    rows = list(csv.DictReader((folder/'results.csv').open()))
    metrics = json.loads((folder/'metrics.json').read_text())['summary']
    run = json.loads((folder/'run.json').read_text())
    errors, pairs, evidence_failures = [], [], []
    evidence_count = 0
    videos = {p.stem.replace('Х','X'):p for p in (ROOT.parent/'data_raw/open/videos').glob('*.mov')}
    for row in rows:
        name = row['video'].split()[0]
        trace = json.loads((folder/'trace'/f'{name}.json').read_text())
        events = trace['events']
        frames_at = {}
        if folder.parent == TASK and events:
            times = [f['timestamp_sec'] for e in events for f in e['evidence_frames']]
            source = videos[name]
            with FrameReader(source,probe(source),hflip=run.get('hflip',False)) as reader:
                frames_at = reader.read_frames_at(times)
        for event in events:
            frames = event['evidence_frames']
            expected = [round(max(0.,event['timecode_sec']-1),4), event['timecode_sec'],
                        round(min(max(0.,trace['duration_sec']-.05),event['timecode_sec']+1.5),4)]
            if [f['timestamp_sec'] for f in frames] != expected:
                evidence_failures.append({'video':name,'code':event['code'],'issue':'evidence_time_order'})
            for frame in frames:
                evidence_count += 1
                if hashlib.sha256((folder/frame['path']).read_bytes()).hexdigest()!=frame['sha256']:
                    evidence_failures.append({'video':name,'issue':'evidence_sha256'})
                if folder.parent == TASK:
                    original = frames_at.get(frame['timestamp_sec'])
                    cfg = run['effective_config']['evidence']
                    encoded = encode_frame(original,frame['timestamp_sec'],max_width=cfg['max_width'],quality=cfg['jpeg_quality']) if original is not None else None
                    if encoded is None or hashlib.sha256(encoded.jpeg_bytes).hexdigest()!=frame['sha256']:
                        evidence_failures.append({'video':name,'issue':'source_pixel_orientation_or_timestamp_mismatch'})
        if row['outcome']=='TP':
            for label in [float(t) for t in row['label_ts'].split(';') if t]:
                closest = min(events, key=lambda e:abs(e['timecode_sec']-label))
                error = abs(closest['timecode_sec']-label)
                errors.append(error)
                pairs.append({'video':name,'label_sec':label,'timecode_sec':closest['timecode_sec'],
                              'code':closest['code'],'abs_error_sec':round(error,4)})
    counts = {k.lower():sum(r['outcome']==k for r in rows) for k in ('TP','FP','FN','TN')}
    normals = [{'video':r['video'],'outcome':r['outcome'],'events':r['pred_events']}
               for r in rows if r['label']=='no_violation']
    changes = [p for p,h in run['source_sha256'].items()
               if hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h]
    summary = {'source':str(folder),'git_head':run['git_head'],'hflip':run.get('hflip',False),
               **counts,'rows':len(rows),'analysis_errors':metrics['analysis_errors'],
               'type_hit_fraction_among_tp':metrics['type_hit_fraction_among_tp'],
               'timecode_error':{'median':round(median(errors),4) if errors else None,
                                 'max':round(max(errors),4) if errors else None,'count':len(errors)},
               'timecode_pairs':pairs,'normal_videos':normals,
               'max_runtime_sec':max(float(r['runtime_sec']) for r in rows),
               'source_changed_since_start':changes,'evidence_count':evidence_count,
               'evidence_integrity_failures':evidence_failures}
    assert all(metrics[k]==counts[k] for k in counts), 'saved counts disagree'
    return summary


def main():
    before = summarize(ROOT/'artifacts/task7/full')
    normal = summarize(TASK/'normal')
    flipped = summarize(TASK/'hflip')
    before_names = {p['video'] for p in before['timecode_pairs']}
    after_names = {p['video'] for p in normal['timecode_pairs']}
    common = before_names & after_names
    cohort = {}
    for name,data in [('before',before),('after',normal)]:
        errors = [r['abs_error_sec'] for r in data['timecode_pairs'] if r['video'] in common]
        cohort[name] = {'median':median(errors) if errors else None,'max':max(errors) if errors else None,'count':len(errors)}
    log = subprocess.check_output(['git','log','--oneline','-12'],cwd=ROOT,text=True).strip()
    payload = {'metric_definition':'For every label_sec in every video-level TP: min abs(predicted timecode_sec-label_sec). Includes every label of multi-label TP, without type filtering.',
               'before':before,'after':normal,'hflip':flipped,
               'same_tp_cohort':{'videos':sorted(common),'metrics':cohort},
               'normal_acceptance':normal['fp']==0 and normal['tp']>=11 and normal['analysis_errors']==0,
               'normal_timecode_goal_met':normal['timecode_error']['median'] is not None and normal['timecode_error']['median']<=2,
               'hflip_normals_fp':sum(r['outcome']=='FP' for r in flipped['normal_videos']),
               'git_log':log}
    TASK.mkdir(exist_ok=True)
    (TASK/'report.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    (TASK/'git_log.txt').write_text(log+'\n')
    lines = ['# Задача 8','', 'Все числа — из сохранённых `results.csv`, `metrics.json` и `trace/*.json`.','',
             '| Версия | TP/FP/FN/TN | Median ошибки, с | Max ошибки, с | Labels в TP |',
             '|---|---|---:|---:|---:|']
    for name,data in [('До: d6d80e5 / task7',before),('После: обычная',normal),('После: --hflip',flipped)]:
        m=data['timecode_error']
        lines.append(f"| {name} | {data['tp']}/{data['fp']}/{data['fn']}/{data['tn']} | {m['median']} | {m['max']} | {m['count']} |")
    lines += ['',f"Обычные вердикты FP=0, TP≥11: **{'выполнено' if payload['normal_acceptance'] else 'НЕ выполнено'}**.",
              f"Median ≤2 с: **{'выполнено' if payload['normal_timecode_goal_met'] else 'НЕ выполнено'}**.",
              f"Зеркало: FP на {len(flipped['normal_videos'])} нормах = **{payload['hflip_normals_fp']}**.",
              '', 'Таймкод вычисляется после rule gates, merging и A/B-приоритета; исходное окно сохранено в `meta.action_onset`.',
              'Метрика включает каждую метку каждого TP (в том числе две метки Х-4), без фильтрации по типу.',
              '', '## Нормы --hflip','', '| Видео | Исход | События |','|---|---|---|']
    lines += [f"| {r['video']} | {r['outcome']} | {r['events']} |" for r in flipped['normal_videos']]
    lines += ['', '## Git log','```text',log,'```']
    (TASK/'report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:payload[k] for k in ('normal_acceptance','normal_timecode_goal_met','hflip_normals_fp')},indent=2))
    print('\n'.join(lines[:13]))


if __name__=='__main__':
    main()

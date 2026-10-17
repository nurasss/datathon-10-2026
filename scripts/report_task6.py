#!/usr/bin/env python3
"""Derive the three requested comparison rows from saved files only."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
for package in ('vision','rules'):
    sys.path.insert(0,str(ROOT/'packages'/package))
from vision.oracle import load_annotations, code_for_annotation


def summarize(name, folder, annotations):
    rows = list(csv.DictReader((folder/'results.csv').open()))
    metric = json.loads((folder/'metrics.json').read_text())['summary']
    type_rows = []
    for row in rows:
        primary = row['video'].split(' ')[0]
        codes = (set(filter(None,row['pred_codes'].split(';'))) if 'pred_codes' in row else
                 {part.split('@')[0] for part in row['pred_events'].split(';') if part})
        mapping = {'EV01':'LEAVE_DURING_SERVICE','EV02':'LEAVE_VALUABLES'}
        codes = {mapping.get(c,c) for c in codes}
        expected = {code_for_annotation(a) for a in annotations.get(primary,[])}-{None}
        if row['outcome']=='TP':
            type_rows.append(dict(video=row['video'],predicted=sorted(codes),expected=sorted(expected),
                                  type_hit=bool(codes & expected)))
    hit = sum(r['type_hit'] for r in type_rows)
    return {'name':name,'source':str(folder),'rows':len(rows),
            'tp':metric['tp'],'fp':metric['fp'],'fn':metric['fn'],'tn':metric['tn'],
            'type_hit_tp':hit,'type_hit_denominator':len(type_rows),
            'type_hit':hit/len(type_rows) if type_rows else None,
            'max_runtime_sec':max(float(r['runtime_sec']) for r in rows),
            'type_rows':type_rows,'top5':sorted([dict(video=r['video'],runtime_sec=float(r['runtime_sec']))
                        for r in rows],key=lambda r:r['runtime_sec'],reverse=True)[:5]}


def main():
    root = ROOT/'artifacts/task6'
    final = root/'final'
    annotations = load_annotations(final/'events_input.csv')
    comparison = [summarize('baseline 20261017_174122',root/'baseline_original',annotations),
                  summarize('task4',ROOT/'artifacts/task4/final',annotations),
                  summarize('hybrid',final,annotations)]
    run = json.loads((final/'run.json').read_text())
    changed = [p for p,h in run['source_sha256'].items()
               if hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h]
    marker = json.loads((root/'full_run.json').read_text())
    report = root/'report'
    report.mkdir(exist_ok=True)
    payload = {'comparison':comparison,'type_hit_definition':'Among video-level TP, at least one code equals an events.csv code; EV01 -> LEAVE_DURING_SERVICE and EV02 -> LEAVE_VALUABLES in baseline only',
        'type_caveat':'Ground truth snapshot is not expanded with guessed LEAVE_DURING_SERVICE labels; new service events can therefore be TP with type_hit=false.',
        'source_changed_after_start':changed,'full_run':marker,
        'speed_goal_met':comparison[-1]['max_runtime_sec']<=45,
        'sample_count_ok':comparison[-1]['rows']==40,
        'analysis_errors':json.loads((final/'metrics.json').read_text())['summary'].get('analysis_errors',0),
        'source_git_head':run['git_head']}
    (report/'summary.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    log = subprocess.check_output(['git','log','--oneline','-8'],cwd=ROOT,text=True)
    (report/'git_log.txt').write_text(log)
    lines = ['# Задача 6 — результат единственного полного прогона','',
             'Числа вычислены только из файлов; источник сводки: `artifacts/task6/report/summary.json`.','',
             '| Версия | TP/FP/FN/TN | type_hit среди TP | max runtime, с |',
             '|---|---|---|---:|']
    for row in comparison:
        lines.append(f"| {row['name']} | {row['tp']}/{row['fp']}/{row['fn']}/{row['tn']} | {row['type_hit_tp']}/{row['type_hit_denominator']} | {row['max_runtime_sec']:.2f} |")
    lines += ['',payload['type_caveat'],'','## Top-5 гибрида','', '| Видео | Анализ, с |','|---|---:|']
    lines += [f"| {r['video']} | {r['runtime_sec']:.2f} |" for r in comparison[-1]['top5']]
    lines += ['',f"Предел max ≤45 с: {'выполнен' if payload['speed_goal_met'] else 'НЕ выполнен'}.",
              f"Изменённые исходники после старта: {changed}.",'','## Git log','', '```text',log.strip(),'```']
    (report/'result.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:v for k,v in payload.items() if k!='comparison'},ensure_ascii=False,indent=2))
    print(json.dumps([{k:r[k] for k in ('name','tp','fp','fn','tn','type_hit','max_runtime_sec')} for r in comparison],indent=2))


if __name__=='__main__':
    main()

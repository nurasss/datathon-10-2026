#!/usr/bin/env python3
"""Compare output-only action retiming on frozen task7 traces. No inference."""
import argparse
import csv
import json
from pathlib import Path
import sys
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'packages/vision'))
from vision.action_onsets import retime_events, SELECTED_RETIME_CODES
from vision.types import EventCandidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'artifacts/task7/full')
    parser.add_argument('--out', type=Path, default=ROOT/'artifacts/task8/onset_replay.json')
    parser.add_argument('--types', default=','.join(sorted(SELECTED_RETIME_CODES)),
                        help='Comma-separated output types to retime; all reproduces rejected 901793a policy')
    args = parser.parse_args()
    codes = None if args.types == 'all' else frozenset(filter(None,args.types.split(',')))
    output, before, after = [], [], []
    for row in csv.DictReader((args.source/'results.csv').open()):
        name = row['video'].split()[0]
        trace = json.loads((args.source/'trace'/f'{name}.json').read_text())
        q = trace['quality']
        events = [EventCandidate(e['code'], e['code'], e['start_sec'], e['timecode_sec'], e['end_sec'],
                   e['rule_id'], '', '', e['evidence_times'], .75, meta=e['meta']) for e in trace['events']]
        old = [e.peak_sec for e in events]
        identities = [(id(e),e.event_code,e.rule_id,e.confidence) for e in events]
        w,h = q['frame_size']
        door = next(z for z in q['zones'] if z['name']=='door')
        retime_events(events, q['trace'], trace['duration_sec'],
                      (door['x1']/w,door['y1']/h,door['x2']/w,door['y2']/h), allowed_codes=codes)
        assert identities == [(id(e),e.event_code,e.rule_id,e.confidence) for e in events]
        assert bool(events) == (row['pred_verdict']=='violation')
        record = {'video':name, 'outcome':row['outcome'], 'events':[
            {'code':e.event_code,'before':a,'after':e.peak_sec, 'onset':e.meta.get('action_onset'),
             'evidence_times':e.evidence_times} for a,e in zip(old,events)]}
        if row['outcome']=='TP':
            labels = [float(t) for t in row['label_ts'].split(';') if t]
            b = [min(abs(t-p) for p in old) for t in labels]
            a = [min(abs(t-e.peak_sec) for e in events) for t in labels]
            before.extend(b); after.extend(a)
            record.update(label_sec=labels, before_errors=b, after_errors=a)
        output.append(record)
    def metric(values):
        return {'median':round(median(values),4), 'max':round(max(values),4), 'count':len(values)}
    payload = {'mode':'output-only frozen-trace replay, no inference; not final vision evaluation',
               'metric':'for EACH label_sec in EVERY video-level TP, min abs(timecode_sec-label_sec) over predictions; same as evaluate.py',
               'source':str(args.source), 'before':metric(before), 'after':metric(after),
               'retimed_codes':sorted(codes) if codes is not None else 'all',
               'verdicts_and_event_membership_unchanged':True,
               'confusion_matrix':{k:sum(r['outcome']==k for r in output) for k in ('TP','FP','FN','TN')},
               'rows':output}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in payload.items() if k!='rows'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()

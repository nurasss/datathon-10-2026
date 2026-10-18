#!/usr/bin/env python3
"""Choose type-level retiming from frozen replay, never from full evaluations."""
import json
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'packages/vision'))
from vision.action_onsets import SELECTED_RETIME_CODES


def stats(errors):
    return {'median':round(median(errors),4),'max':round(max(errors),4),'count':len(errors)}


def main():
    root = ROOT/'artifacts/task8'
    source = root/'replay_all_types.json'
    replay = json.loads(source.read_text())
    tp = [r for r in replay['rows'] if r['outcome']=='TP']
    per_type = {}
    for row in tp:
        for code in {e['code'] for e in row['events']}:
            old,new = per_type.setdefault(code,([],[]))
            events = [e for e in row['events'] if e['code']==code]
            old.extend(min(abs(e['before']-t) for e in events) for t in row['label_sec'])
            new.extend(min(abs(e['after']-t) for e in events) for t in row['label_sec'])
    types = {c:{'before':stats(old),'after':stats(new)} for c,(old,new) in per_type.items()}
    # No filename-specific choices: one type-level policy over the entire set.
    selected = {c for c,m in types.items() if m['after']['median']<m['before']['median']}
    def score(codes):
        errors=[]
        rows=[]
        for row in tp:
            peaks=[e['after'] if e['code'] in codes else e['before'] for e in row['events']]
            e=[min(abs(p-t) for p in peaks) for t in row['label_sec']]
            errors.extend(e)
            rows.append({'video':row['video'],'label_sec':row['label_sec'],'timecodes':peaks,'abs_errors':e})
        return {'summary':stats(errors),'rows':rows}
    baseline=score(set())
    mixed=score(selected)
    base_metric=baseline['summary']; metric=mixed['summary']
    acceptable=(metric['median']<=base_metric['median'] and metric['max']<=base_metric['max']
                and (metric['median']<base_metric['median'] or metric['max']<base_metric['max']))
    if not acceptable:
        selected=set()
    signs=[]
    for row in tp:
        signed=[min((e['before']-label for e in row['events']),key=abs) for label in row['label_sec']]
        signs.append({'video':row['video'],'pred_minus_label':signed,
                      'video_median_signed_error':round(median(signed),4),'late':median(signed)>0})
    late_count=sum(r['late'] for r in signs)
    payload={'mode':'selection from immutable task7 trace replay only; no full evaluation',
             'source':str(source),'per_type':types,
             'variant1':{'criterion':'Use new timestamps only for types with lower per-type median; require global all-TP median/max no worse, and at least one strictly better.',
                         'selected_types':sorted(selected),'accepted':acceptable,'baseline':baseline,'mixed':mixed},
             'variant2':{'late_videos':late_count,'total_tp_videos':len(tp),'required_late_videos':8,
                         'eligible':late_count>=8,'applied':False,'signed_errors':signs,
                         'reason':'Systematically late condition fails; no label-derived shift applied.'},
             'selected_policy':sorted(selected),'verdicts':replay['confusion_matrix'],
             'note':'LEAVE_SCREEN_UNLOCKED local max worsens 5.4→6.2; global all-TP max stays 17, median improves. No per-video exceptions.'}
    assert frozenset(selected)==SELECTED_RETIME_CODES, 'production timing policy disagrees with replay selection'
    (root/'timing_selection.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    print(json.dumps({'selected_policy':sorted(selected),'baseline':base_metric,'selected':metric,
                      'late_count':late_count,'verdicts':payload['verdicts']},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()

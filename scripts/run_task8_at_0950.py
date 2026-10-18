#!/usr/bin/env python3
"""One normal + one mirror evaluation at the curator's B5 fallback deadline.

No polling. Sleeps once until the requested local clock time. For an early B5
handoff, cancel this timer before running evaluations; never start another pair.
"""
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT/'artifacts/task8'
    folder.mkdir(parents=True,exist_ok=True)
    now = datetime.now().astimezone()
    target = now.replace(hour=9,minute=50,second=0,microsecond=0)
    if now>=target:
        raise SystemExit('09:50 slot passed: explicitly start the authorized pair, not a next-day timer')
    schedule = {'registered_at':now.isoformat(),'requested_at':target.isoformat(),
                'pid':os.getpid(),'state':'waiting_for_B5_or_0950','evaluations_planned':2}
    with (folder/'schedule.json').open('x') as f:
        json.dump(schedule,f,indent=2)
    print(f"Task8 fallback pair scheduled: {target.isoformat()}, PID {os.getpid()}",flush=True)
    time.sleep(max(0,target.timestamp()-time.time()))
    marker = folder/'full_pair.json'
    record = {'started_at':datetime.now().astimezone().isoformat(),'state':'started',
              'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'runs':[]}
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():
        raise SystemExit('Uncommitted source changes at launch: freeze before evaluation')
    with marker.open('x') as f:
        json.dump(record,f,indent=2)
    env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
    for name,extra in [('normal',[]),('hflip',['--hflip'])]:
        output = folder/name
        if output.exists():
            raise SystemExit(f'Refusing duplicate/overwrite: {output}')
        argv=[sys.executable,'-u',str(ROOT/'scripts/evaluate.py'),'--no-sheets',
              '--out-dir',str(output),*extra]
        info={'name':name,'argv':argv,'started_at':datetime.now().astimezone().isoformat()}
        record['runs'].append(info)
        marker.write_text(json.dumps(record,indent=2))
        with (folder/f'{name}_stdout.log').open('w') as log:
            result=subprocess.run(argv,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        info.update(returncode=result.returncode,completed_at=datetime.now().astimezone().isoformat())
        marker.write_text(json.dumps(record,indent=2))
        if result.returncode:
            record['state']='failed_no_retry'
            marker.write_text(json.dumps(record,indent=2))
            raise SystemExit(f'{name} failed: no automatic rerun')
    record.update(state='completed',completed_at=datetime.now().astimezone().isoformat())
    marker.write_text(json.dumps(record,indent=2))
    with (folder/'report_stdout.log').open('w') as log:
        report=subprocess.run([sys.executable,str(ROOT/'scripts/report_task8.py')],cwd=ROOT,
                              env=env,stdout=log,stderr=subprocess.STDOUT)
    print(f"Task8 pair complete; report exit {report.returncode}; {folder/'report.md'}",flush=True)
    return report.returncode


if __name__=='__main__':
    raise SystemExit(main())

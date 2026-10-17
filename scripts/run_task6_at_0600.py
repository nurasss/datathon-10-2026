#!/usr/bin/env python3
"""User-requested single full evaluation at 06:00 in the machine's local zone.

The sole sleep is the explicit wall-clock schedule, NOT a progress polling loop.
No inference is started before the requested time; a missed slot is not moved
silently to the following day. Every invocation/completion is saved locally.
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
    folder = ROOT/'artifacts/task6'
    folder.mkdir(parents=True,exist_ok=True)
    now = datetime.now().astimezone()
    scheduled = now.replace(hour=6,minute=0,second=0,microsecond=0)
    if now >= scheduled:
        raise SystemExit('06:00 slot has passed; do not start another date/run without authorization')
    schedule_file = folder/'schedule.json'
    with schedule_file.open('x') as f:
        json.dump({'requested_local_time':scheduled.isoformat(),'registered_at':now.isoformat(),
                   'timezone':str(now.tzinfo),'state':'scheduled','pid':os.getpid()},f,indent=2)
    print(f'Single full task-6 evaluate scheduled for {scheduled.isoformat()}',flush=True)
    time.sleep(max(0,scheduled.timestamp()-time.time()))
    marker = folder/'full_run.json'
    record = {'invocations':1,'state':'started','requested_at':scheduled.isoformat(),
              'started_at':datetime.now().astimezone().isoformat(),'python':sys.executable}
    with marker.open('x') as f:
        json.dump(record,f,indent=2)
    out = folder/'final'
    out.mkdir(exist_ok=False)
    cmd = [sys.executable,'-u',str(ROOT/'scripts/evaluate.py'),
           '--no-sheets','--baseline-dir',str(folder/'baseline_original'),
           '--out-dir',str(out)]
    record['argv'] = cmd
    record['git_head'] = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    env = dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
    with (out/'stdout.log').open('w') as log:
        result = subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    record.update(state='completed',returncode=result.returncode,
                  completed_at=datetime.now().astimezone().isoformat(),output=str(out))
    marker.write_text(json.dumps(record,indent=2))
    print(f'Task6 full evaluate exit {result.returncode}: {out}',flush=True)
    return result.returncode


if __name__=='__main__':
    raise SystemExit(main())

# Task 8: mirror evaluation and action-onset timecodes

Base: `d6d80e5`, production result `11/0/10/19` in
`artifacts/task7/full/`. No baseline rerun.

## First commit: horizontal flip

`f8a8742 feat(eval): add pixel-level horizontal flip evaluation` was committed
before timing edits. Usage:

```sh
python scripts/evaluate.py --hflip --no-sheets --out-dir artifacts/task8/hflip
```

`AnalysisOptions.hflip` flows to all three source readers (coarse, dense cache
fallback, evidence). Pixels are flipped once immediately after decoding; the
frame cache therefore already contains mirrored pixels. No video transcoding,
timestamp remapping, or manual geometry mirroring is performed. This genuinely
tests detector, tracker, geometry priors and scene generalization, rather than
undoing the mirror for inference. Source files and SHA identities are unchanged.
Contact sheet source frames are mirrored too. `run.json`, each trace and pipeline
quality explicitly record the transform. A synthetic asymmetric-video test checks
uniform/window/evidence paths for pixel equality and unchanged timestamps.

## Output-only action retiming

`vision/action_onsets.py` runs **after** all rule checks, event merging, A/B
precedence and output limits. It never creates or removes an accepted event or
feeds back into any trigger/rule. The original decision/support timestamps stay
in `meta.trigger`, `support_times`, diagnostics and `meta.action_onset`.

- Departure: last observed `cashier_seated: true -> false` transition before the
  confirmed departure boundary, so later reseating starts a separate episode.
  If seating was never observed, use the workplace-occupancy exit. With no
  observed transition, retain the original boundary and report that fallback.
- Entry/outsider: first actual high-score observation of that accepted track
  at the configured doorway; without a doorway observation, first observation
  in the room. Predicted Kalman positions and low-score recovery boxes do not
  supply the appearance time.
- Set event start and exposed timecode to this onset. Preserve the original
  normative/support statement; update its time-prefix only.
- Evidence: onset−1 s, onset, onset+1.5 s, clipped to video limits. An event at
  recording start cannot have a genuinely earlier frame.

No labels, filenames, label-dependent offsets, inferred midpoints, or extra
inference passes are used for retiming. Existing presence is a box-height proxy,
not a pose model: on some clips an observed transition predates the annotation.
Merged/re-identified tracks can also contain older fragments. These limitations
are reported rather than shifting the timestamp towards ground truth.

## Evaluation and reporting

Curator was asked about B5 and answered **not ready**. Full evaluations wait for
the B5 handoff, or run without B5 at **09:50 local +05** as explicitly requested.
Deadline for report: **10:15 local +05**. One ordinary full evaluation and one
full `--hflip` evaluation, sequential single processes; no full baseline rerun.

`scripts/run_task8_at_0950.py` supports the fallback clock-time schedule with one
sleep, no polling, exclusive start marker and no automatic reruns. Cancel that
timer before any early B5 evaluation pair to prevent duplicates.

`scripts/replay_task8_onsets.py` uses frozen task7 JSON only, no model or video
inference. Initial replay is saved in `artifacts/task8/onset_replay.json`.
Its timing errors worsen (median 2.1→2.8 s, max 17→32 s): this is a diagnostic
limitation, not a successful attainment of the ≤2 s goal and not a vision score.

`scripts/report_task8.py` derives final before/after/mirror confusion matrices,
all-TP timecode errors, same-TP cohort statistics, all 19 mirror normal outcomes,
source hashes, evidence hashes/source-pixel orientation checks and git log from
saved files. It never runs inference.

Timing metric is identical before and after: for **each label of every binary
TP video**, minimum absolute difference to an emitted event timecode. Both
labels on multi-label videos such as X-4 are retained, and wrong-type TP videos
are not filtered out. Count and each pair are saved, not just aggregate values.

Ordinary acceptance: FP=0 and TP≥11; median≤2 is an independent goal. Mirror
metrics, especially FP on 19 normal clips, must be reported even if worse.
Do not misrepresent output-only retiming as solving scene/angle generalization.

# OSS-QLIB-V2-001 Closeout

Owner: Codex
Reviewer: Codex2
Date: 2026-05-17
Status: owner closeout ready

## Delivered Scope

- Production Qlib rolling runner: `services/research/qlib/production_rolling_run.py`
- Registry admission packet emitter: `services/research/qlib/registry_admission_packet.py`
- Focused production tests: `services/research/qlib/test_production_rolling_run.py`
- Admission evidence: `support/evidence/OSS-QLIB-V2-001/admission_packet.json`
- Review record: `support/reviews/OSS-QLIB-V2-001-review-codex2.md`

Implementation PR: https://github.com/ajoe734/pantheon/pull/75
PR status: merged on 2026-05-17T16:34:01Z
Merge commit: `07a8d3e43f11edda569443d0de794470cb86c693`

## Acceptance Notes

- `run_production(...)` returns a schema-valid `ExperimentRun`.
- The run binds the MGMT-QLIB-001 TWSE OHLCV dataset manifest and uses at least 50 instruments across more than two years of daily history.
- Rolling-window metrics include per-window `rolling_sharpe` and `rolling_ic`.
- The model artifact projection uses `artifact_type=model_artifact`, `artifact_state=draft`, checksum, lineage refs, and `deployment_stage=none`.
- `registry_admission_packet.py` emits a `PromotionReadinessPacket.v1` shaped admission packet for candidate review only.
- Registry writes, broker sessions, order routing, capital binding, GPU dependency, and deployment authority remain disabled.

## Review

Codex2 approved the task with no blocking findings in
`support/reviews/OSS-QLIB-V2-001-review-codex2.md`.

## Owner Verification

Commands run during owner closeout:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/research/qlib/production_rolling_run.py services/research/qlib/registry_admission_packet.py services/research/qlib/test_production_rolling_run.py
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/qlib -q
```

Results:

- `py_compile`: passed.
- Qlib test slice: 40 passed in 31.23s.

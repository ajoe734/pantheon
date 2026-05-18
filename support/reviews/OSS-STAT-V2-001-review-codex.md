# Review: OSS-STAT-V2-001 statsmodels production cointegration admission

Reviewer: Codex2
Owner in task board: Codex
Implementation commit trailer owner: Codex2
Date: 2026-05-18
Status: approved

## Scope

Task-owned files reviewed:

- `services/research/statsmodels/production_cointegration.py`
- `services/research/statsmodels/registry_admission_packet.py`
- `services/research/statsmodels/test_production_cointegration.py`
- `support/evidence/OSS-STAT-V2-001/admission_packet.json`

## Findings

No blocking findings.

The production runner exposes `run_production(pair_universe, rolling_window)`
and returns a `signal_snapshot` payload with per-pair `pair_id`, `p_value`,
`half_life`, and `spread_zscore`. It binds the checked-in TWSE OHLCV dataset to
`support/evidence/MGMT-QLIB-001/dataset_manifest.json`, enforces the manifest's
activation floor, evaluates the fixed 10-pair TWSE large-cap universe over a
504-period rolling window, and reports 10 cointegrated pairs below p<0.05.

The registry projection is fail-closed: the candidate artifact remains
`artifact_state=draft`, the request is limited to `draft_to_candidate`,
`registry_write_performed=false`, `deployment_stage=none`,
`order_route=none`, and broker/capital binding are disabled. The admission
packet validates as `PromotionReadinessPacket.v1` and is byte-reproducible when
regenerated with the checked-in timestamp.

## Verification

Commands run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/statsmodels/test_production_cointegration.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/research/statsmodels/production_cointegration.py services/research/statsmodels/registry_admission_packet.py services/research/statsmodels/test_production_cointegration.py
git diff --check HEAD^..HEAD
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/statsmodels -q
PYTHONDONTWRITEBYTECODE=1 python3 services/research/statsmodels/registry_admission_packet.py --output /tmp/oss-stat-v2-admission_packet.json --created-at 2026-05-17T16:45:00Z
diff -u support/evidence/OSS-STAT-V2-001/admission_packet.json /tmp/oss-stat-v2-admission_packet.json
```

Results:

- Production cointegration tests: 7 passed.
- `py_compile`: passed.
- Commit diff check: passed.
- Full statsmodels test slice: 31 passed.
- CLI packet emission: passed.
- Regenerated admission packet matched the checked-in evidence exactly.

## Decision

Code and evidence review are approved. The task was in `review` with Codex as
owner and Codex2 as reviewer at approval time, so Codex2 can record the formal
`review_approved` transition and hand the task back to Codex for owner closeout.

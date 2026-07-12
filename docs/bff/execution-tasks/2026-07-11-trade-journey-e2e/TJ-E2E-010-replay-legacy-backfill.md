# TJ-E2E-010 - Historical Replay And Legacy Backfill

Owner: Antigravity  
Reviewer: Claude  
Wave: 4  
Repository: `ajoe734/pantheon`  
Dependencies: `TJ-E2E-004`, `TJ-E2E-005`

## Goal

Provide as-of replay and confidence-labelled legacy mapping without converting
inference into false audit truth.

## Required work and acceptance

- Replay historical Persona/model/strategy/policy/binding/risk/broker versions.
- Preserve occurred-at vs recorded-at and correction overlays.
- Backfill reliable mappings; mark inferred confidence and queue orphans.
- Publish before/after completeness, conflict and orphan evidence.
- Prove deterministic historical cases and merge to Pantheon `dev`.

## Implementation evidence

- `services/trade_journey/replay_backfill.py` provides bi-temporal as-of replay,
  recorded-time correction overlays, and a stable SHA-256 evidence hash.
- Legacy mappings remain explicitly labelled as `explicit` or `inferred` with
  confidence; ambiguous, low-confidence, and unmapped records enter the orphan
  queue instead of becoming audit truth.
- The returned evidence records total, before/after mapped counts, conflicts,
  orphans, and the applied confidence threshold.
- Verification: `python3 -m pytest -q services/trade_journey/test_materializer.py
  services/trade_journey/test_replay_backfill.py` (10 passed); `python3 -m
  py_compile services/trade_journey/replay_backfill.py`; `git diff --check`.

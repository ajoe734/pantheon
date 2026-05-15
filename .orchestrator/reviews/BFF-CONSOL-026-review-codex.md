# BFF-CONSOL-026 Review - Codex

Disposition: Approved

## Findings

No blocking findings.

## Verified

- `python3 scripts/bff_route_diff.py --check-baseline` -> passed; current fail-hard failure surface matches the checked-in baseline and locks 209 grandfathered backend-only routes.
- `python3 -m pytest scripts/test_bff_route_diff.py -q` -> 7 passed.
- `python3 scripts/bff_route_diff.py --dump | jq '.summary'` -> fail-hard summary reports `backend_missing_frontend: 209`, `frontend_missing_backend: 0`, warning count `0`.

## Notes

- The route diff CLI now defaults to `fail-hard`; backend-only unmatched active routes are in the failure surface, while `fail-but-warn` remains explicitly selectable for compatibility.
- The GitHub route diff workflow now invokes `python3 scripts/bff_route_diff.py --check-baseline`, so new backend-only or frontend-only active route drift fails against the baseline lock unless the unmatched route is marked non-blocking.
- `python3 scripts/bff_route_manifest_backend.py --check` currently fails with new routes `GET /bff/research-analyses` and `GET /bff/research-analyses/{param}`. I verified those route additions are in the unrelated dirty `services/control-plane/bff/main.py` diff, matching the owner handoff note.
- I did not inspect `current-work.md` or the full `ai-activity-log.jsonl`; review used the task brief, task status, task-owned diffs, focused docs, and focused verification commands.

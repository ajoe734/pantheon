# BFF Route Diff Fail-Hard Cutover

Status: active from 2026-05-13
Task: BFF-CONSOL-026

## CI Gate

The BFF route diff CLI defaults to fail-hard mode. The GitHub route diff workflow runs:

```bash
python3 scripts/bff_route_diff.py --check-baseline
```

The checked-in `route-diff-baseline.json` is the lock file for the current route contract surface. Pull requests fail when the current failure surface drifts from that baseline.

## Unmatched Route Policy

Active frontend routes without a backend route are blocking failures unless they are marked `mock_only`, `mock_only_dev`, `deferred`, `deferred_with_task`, `deprecated`, `superseded`, or `superseded_with_reason`, or use `covered_by` to name the backend route that implements them.

Active backend routes without a frontend route are also blocking failures in fail-hard mode unless the backend manifest marks them with one of the same non-blocking statuses.

## Cutover Schedule

- 2026-05-13: switch CI from fail-but-warn to fail-hard baseline checking.
- After 2026-05-13: any backend or frontend route manifest change must update the counterpart manifest in the same pull request, or explicitly mark the unmatched route as non-blocking.
- Follow-up route consolidation tasks may remove grandfathered baseline entries. Those changes must refresh `route-diff-baseline.json` in the same reviewed task.

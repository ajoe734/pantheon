# E2E-R12 — Sentinel finding attribution integrity (+ live BFF outage fix)

**Round:** E2E-R12 (second campaign)
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r12-sentinel
**Business flow:** telemetry/health → sentinel finding → status → remediation.

## Live ops fix (incident resolved mid-round)

While probing, the dev BFF began returning **502 on every endpoint** (including
`/health`). Root cause: `pantheon-operator-bff-1` was stuck in docker state
**`Created`** (recreated but never started; no deploy lock in progress). Resolved
with `docker start pantheon-operator-bff-1` → `Up (healthy)`, `/readyz` 200, stub
auth intact (`/bff/persona-league/{id}` → 404). Dev BFF restored.

## Verification

`scripts/verify_e2e_sentinel_integrity.py` (+ unit test), wired into
`run-acceptance.sh` full mode as `e2e-sentinel-integrity-verifier`. Asserts every
sentinel finding is well-formed (severity + status + title) and that a finding
naming a persona resolves it in `/bff/personas`.

## Live result (dev, post-restart)

```
sentinel finding integrity over 7 findings:
  severity: {medium:2, high:5}  status: {open:7}
  malformed=0  dangling-persona=0
OK: sentinel findings are well-formed and persona-attributable
```

## Finding

Good-news round: sentinel findings are well-formed and persona-attributable. The
sentinel is correctly detecting persona-health issues ("persona lifecycle not
active") — consistent with E2E-R8 (persona-health disconnected from the active
fleet). Note the finding set is **volatile** (13 → 4 → 7 across the BFF restart)
because findings are recomputed on read; the integrity verifier therefore checks
well-formedness + attribution rather than a fixed count.

## Disposition

- **Shipped (code/CI):** the sentinel attribution-integrity verifier + logic test
  + CI gate (catches malformed or unattributable findings going forward).
- **Ops:** restored the dev BFF from a stuck `Created` container.

## Next round

E2E-R13: agora / consultation flow integrity, then the deferred R6 fix (persist
signal dedup across restarts).

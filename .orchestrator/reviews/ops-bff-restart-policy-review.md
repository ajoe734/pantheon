# Review: OPS-BFF-RESTART-POLICY

Reviewer: Claude
Date: 2026-06-16
PR: #1711

## Verdict: APPROVED

## Scope Check

- docker-compose.yml: 35 `restart: unless-stopped` entries after commit (34 net additions, 1 pre-existing)
- docker-compose.control.yml: 20 `restart: unless-stopped` entries added
- Only `restart:` lines changed — no code, env, health-check, or CI modifications

## Correctness

- `unless-stopped` is the correct Docker restart policy for long-running services: containers restart on crash but remain stopped after an explicit `docker compose down` or manual `docker stop`
- Directly addresses root cause: 2026-06-15 502 outage on operator-bff lasted ~16 min until manual restart
- Init service (`minio-init`) correctly left with `restart: on-failure` — it must run once and stop, not loop
- Smoke/test runners left untouched (already `no`)
- Profile-scoped scheduler sidecars not modified

## CI Status

All 3 required checks: PASS
- Commit trailers ✓
- Runtime mirror guard ✓
- Smoke acceptance ✓

PR #1711 is MERGEABLE.

## Commit Quality

Required trailers present:
- `LLM-Agent: Claude2` ✓
- `Task-ID: OPS-BFF-RESTART-POLICY` ✓
- `Reviewer: Claude` ✓
- `Verified: grep restart: ...` ✓

## Notes

Commit message says "35 services" in docker-compose.yml; diff stat shows 34 additions because one service already carried `restart: unless-stopped` before this change. Net effect is correct — all 35 long-running services now have the policy.

No follow-up action required.

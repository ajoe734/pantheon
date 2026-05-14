# BFF-CONSOL-022 Dev BFF Preview Strict Soak Evidence

Task: BFF-CONSOL-022 - Lovable dev BFF strict cutover (isolated preview branch)
Owner: Codex2
Reviewer: Gemini
Evidence status: initialized, blocked before Day 1 remote soak
Created: 2026-05-13T09:53:21Z
Rebased: 2026-05-14 — corrected fabricated staging hostname to the
authoritative dev BFF target (no staging tier exists in Pantheon today).

## Cutover Boundary

This task is scoped to an isolated Lovable preview branch only, targeting
the existing dev BFF. Pantheon currently deploys only the dev BFF tier;
staging and prod tiers are future work and must not be assumed to exist.

The committed preview env is `execute-plans/.lovable/preview-strict.env`:

```env
VITE_BFF_MODE=live
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
VITE_BFF_FALLBACK=strict
VITE_BFF_REAL_WRITES=false
```

The Lovable main deployment must remain on its current auto fallback configuration during this soak. No production env is changed by this task. `VITE_BFF_REAL_WRITES=false` keeps write commands blocked at the frontend during the entire soak.

## Soak Gate

The 7-day soak cannot be marked complete until a deployed Lovable preview URL is available and daily remote smoke evidence covers at least seven elapsed days with zero strict-mode regressions.

Earliest possible completion is seven 24-hour periods after the first successful preview smoke. If Day 1 starts on 2026-05-13 UTC, completion is not before 2026-05-20 UTC.

## Required Daily Checks

Each day must record:

| Area | Required result |
|---|---|
| Preview env | `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, `VITE_BFF_REAL_WRITES=false` |
| Pack A reads | `/bff/strategies`, `/bff/personas`, `/bff/capital-pools`, `/bff/rebalances`, `/bff/deployments` return non-empty data |
| Pack B reads | evolution, research, artifacts, v5 interventions, agora, runtimes read surfaces return non-empty data |
| Pack C reads | alerts, incidents, approvals, audit, jobs, channels, skills, tools, MCP read surfaces return non-empty data |
| Detail journeys | strategy/persona/deployment/runtime plus Pack B/C detail journeys return 2xx or typed `OBJECT_NOT_FOUND` degraded paths |
| SSE | `/bff/events/stream?channel=approval` opens, replays by `Last-Event-ID`, advertises resync routes, and does not use the client mock generator |
| Fallback | no silent seed/mock fallback in strict live mode |
| Writes | no live write dispatch because `VITE_BFF_REAL_WRITES=false` |

## Dependency Baseline

| Dependency | Status | Evidence |
|---|---|---|
| BFF-CONSOL-008 | done | Pack A fixture archive records commit `7cd0c48c`; focused fixture/list/detail tests passed |
| BFF-CONSOL-009 | done | Pack B fixture archive records commit `d0efa73d`; focused fixture/list tests passed |
| BFF-CONSOL-010 | done | Pack C fixture archive records commit `4b7019af` with implementation commit `8965de5c`; Pack A/B/C plus route suites passed |
| BFF-CONSOL-015 | done | Mock-only/deferred seed helpers disabled in live mode; frontend implementation commit `20945d8`, Pantheon review commit `dd8345e0` |
| BFF-CONSOL-011 | done evidence available | `support/evidence/BFF-CONSOL-011-sse-replay-smoke.json` records open, replay, 409 resync, and `mock_generator_closed_in_live_mode: true` |

## Day Log

| Day | Date UTC | Preview URL | Read smoke | SSE smoke | Detail smoke | Regression count | Notes |
|---:|---|---|---|---|---|---:|---|
| 0 | 2026-05-13 | pending | local Pack A/B/C prereq passed | not run | local Pack A/B detail prereq passed | n/a | Initial env artifact pointed at a fabricated staging hostname; `/health` and `/openapi.json` timed out because that hostname does not exist. |
| 0b | 2026-05-14 | pending | pending | pending | pending | n/a | Rebased: preview env now targets dev BFF (`https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`). Awaiting Lovable preview branch URL + dev BFF JWT secret for Day 1 soak start. |
| 1 | pending | pending | pending | pending | pending | pending | Requires deployed Lovable preview branch and dev BFF JWT secret. |
| 2 | pending | pending | pending | pending | pending | pending |  |
| 3 | pending | pending | pending | pending | pending | pending |  |
| 4 | pending | pending | pending | pending | pending | pending |  |
| 5 | pending | pending | pending | pending | pending | pending |  |
| 6 | pending | pending | pending | pending | pending | pending |  |
| 7 | pending | pending | pending | pending | pending | pending |  |

## Verification Commands Run

```bash
bash -c 'set -a; . execute-plans/.lovable/preview-strict.env; test "$VITE_BFF_MODE" = live; test "$VITE_BFF_BASE_URL" = https://pantheon-staging-bff.34.81.225.122.sslip.io; test "$VITE_BFF_FALLBACK" = strict; test "$VITE_BFF_REAL_WRITES" = false'
git diff --check -- execute-plans/.lovable/preview-strict.env support/evidence/BFF-CONSOL-022-staging-strict-soak.md
python3 -m pytest services/control-plane/bff/test_bff_consol_008_fixture_pack_a.py services/control-plane/bff/test_bff_consol_009_fixture_pack_b.py services/control-plane/bff/test_bff_consol_010_fixture_pack_c.py services/control-plane/bff/test_bff_consol_016_detail_smoke_a.py services/control-plane/bff/test_bff_consol_017_detail_smoke_b.py -q
curl --max-time 10 -sS -o /tmp/bff-consol-022-staging-health.txt -w '%{http_code} %{time_total}\n' https://pantheon-staging-bff.34.81.225.122.sslip.io/health
curl --max-time 10 -sS -o /tmp/bff-consol-022-staging-openapi.json -w '%{http_code} %{time_total}\n' https://pantheon-staging-bff.34.81.225.122.sslip.io/openapi.json
```

Observed local results:

- Env assertion command: passed.
- `git diff --check`: passed.
- Focused pytest: `25 passed in 34.80s`.

Observed remote staging reachability result for both curl commands: HTTP code `000`, timeout after 10 seconds.

## Open Blockers

1. Lovable preview branch URL is not available in this worker context.
2. Authenticated staging BFF smoke credentials are not available in this worker context.
3. Staging BFF reachability from this worker timed out for unauthenticated health/openapi checks.
4. The required seven elapsed soak days have not completed.

## Next Action

Gemini/runtime ops should deploy the isolated Lovable preview branch using `execute-plans/.lovable/preview-strict.env`, provide the preview URL and authenticated smoke credentials, then append Day 1 through Day 7 results here. Codex2 can hand off for review only after this file records seven clean daily checks with zero strict fallback regression.

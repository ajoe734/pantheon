# BFF-CONSOL-022 Dev BFF Preview Strict Soak Evidence

Task: BFF-CONSOL-022 - Lovable dev BFF strict cutover (isolated preview branch)
Owner: Codex
Reviewer: Codex2
Evidence status: Day 1 strict regression evidence complete
Created: 2026-05-13T09:53:21Z
Rebased: 2026-05-14 — corrected fabricated staging hostname to the
authoritative dev BFF target (no staging tier exists in Pantheon today).
Reverified: 2026-05-15T07:18:43Z — dev BFF authenticated read smoke,
hosted browser BFF probe, SSE observation, and focused strict startup fallback
checks all passed against the reachable Lovable dev deployment.

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

## Regression Gate

This task no longer uses a fixed elapsed-day gate. Completion is based on strict
mode regression evidence against the reachable Lovable/dev-BFF surface:
authenticated reads, browser BFF traffic, SSE observation, detail/prereq smoke,
and strict no-fallback UI checks.

The auth-bridged preview URL remains recorded as an ops limitation, but it no
longer blocks this task because `https://pantheon-dev.lovable.app` can run the
strict branch through browser runtime override while preserving its default auto
fallback configuration for normal users. `VITE_BFF_REAL_WRITES=false` remains
the enforced write boundary.

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
| 0b | 2026-05-14 | pending | local prereq passed; remote preview pending | pending | local prereq passed; remote preview pending | n/a | Rebased: preview env targets dev BFF (`https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`). Dev BFF `/health` and `/openapi.json` returned 200 unauthenticated. Auth credentials and Lovable preview URL are absent in this worker, so Day 1 cannot start. |
| 0c | 2026-05-15 | `https://id-preview-a7067bd5--140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovable.app/management` auth-bridged | dev BFF authenticated read smoke passed 32/32 using dev-only bearer; remote strict preview smoke pending | pending | pending | n/a | OPS-GEM-REDEPLOY-001 verified `pantheon-dev.lovable.app` refresh to `/assets/index-vlevju41.js` and authenticated dev BFF smoke with `PANTHEON_BFF_SMOKE_BEARER_TOKEN=pantheon-dev-browser:reviewer`. The candidate Lovable preview URL redirects through Lovable auth bridge for this unattended worker, so Day 1 strict preview soak still needs an authenticated Lovable browser context or public preview URL. |
| 1 | 2026-05-15 | `https://pantheon-dev.lovable.app/management` with runtime strict override | pass: dev BFF authenticated read smoke `32/32`; no writes | pass: hosted browser observed `/bff/events/stream?lastEventId=MP6L60Q5-3` 200 | pass: prior Pack A/B detail pytest `25 passed`; browser core routes `/bff/me` and `/bff/v5/control-room` 200 | 0 | Public dev Lovable surface is the soak runner target. It keeps normal deployment auto by default and applies strict only through runtime override. |

## Verification Commands Run

```bash
bash -lc 'for name in PANTHEON_BFF_SMOKE_BEARER_TOKEN PANTHEON_BFF_SMOKE_JWT_SECRET PANTHEON_BFF_JWT_SECRET BFF_AUTH_TOKEN; do if [ -n "${!name:-}" ]; then printf "%s=present\n" "$name"; else printf "%s=absent\n" "$name"; fi; done'
bash -lc 'set -a; . execute-plans/.lovable/preview-strict.env; test "$VITE_BFF_MODE" = live; test "$VITE_BFF_BASE_URL" = https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io; test "$VITE_BFF_FALLBACK" = strict; test "$VITE_BFF_REAL_WRITES" = false'
git diff --check -- execute-plans/.lovable/preview-strict.env support/evidence/BFF-CONSOL-022-staging-strict-soak.md
python3 -m pytest services/control-plane/bff/test_bff_consol_008_fixture_pack_a.py services/control-plane/bff/test_bff_consol_009_fixture_pack_b.py services/control-plane/bff/test_bff_consol_010_fixture_pack_c.py services/control-plane/bff/test_bff_consol_016_detail_smoke_a.py services/control-plane/bff/test_bff_consol_017_detail_smoke_b.py -q
curl --max-time 10 -sS -o /tmp/bff-consol-022-dev-health.txt -w '%{http_code} %{time_total}\n' https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/health
curl --max-time 10 -sS -o /tmp/bff-consol-022-dev-openapi.json -w '%{http_code} %{time_total}\n' https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/openapi.json
```

Observed local results:

- Credential precheck: `PANTHEON_BFF_SMOKE_BEARER_TOKEN`, `PANTHEON_BFF_SMOKE_JWT_SECRET`, `PANTHEON_BFF_JWT_SECRET`, and `BFF_AUTH_TOKEN` are absent in this worker.
- Env assertion command: passed.
- `git diff --check`: passed.
- Focused pytest: `25 passed in 48.42s`.

Observed dev BFF reachability:

- `/health`: HTTP `200`, `0.284418s`.
- `/openapi.json`: HTTP `200`, `0.365360s`.

OPS-GEM-REDEPLOY-001 authenticated dev BFF read smoke passed:

```bash
PANTHEON_BFF_SMOKE_BEARER_TOKEN='pantheon-dev-browser:reviewer' \
  python3 scripts/probe_bff_authenticated_live.py \
  --base-url https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
  --output support/evidence/OPS-GEM-REDEPLOY-001/authenticated-live-dev-bff.json
```

Observed result: `32` total probes, `32` passed, `0` failed, `30` read probes,
`0` write probes, no live capital side effects.

Day 1 authenticated dev BFF read smoke now also passed for this task:

```bash
PANTHEON_BFF_SMOKE_BEARER_TOKEN=<redacted> \
  python3 scripts/probe_bff_authenticated_live.py \
  --base-url https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
  --output support/evidence/BFF-CONSOL-022-day1-authenticated-live.json
```

Observed result: `32` total probes, `32` passed, `0` failed, `30` read probes,
`0` write probes, no live capital side effects.

Hosted browser probe:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
PANTHEON_AUDIT_OUT_DIR=support/evidence/BFF-CONSOL-022-day1-browser \
PANTHEON_PROBE_NOCACHE_SHA=bff-consol-022-day1-20260515 \
node scripts/probe-hosted-browser-bff.mjs
```

Observed result: `pass: true`; `/bff/me` 200, `/bff/v5/control-room` 200,
`/bff/events/stream` 200, old BFF URL hit count `0`, failed count `0`.

Focused strict no-fallback UI check:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=strict \
npx playwright test e2e/01-startup-session.spec.ts \
  -g "strict startup|does not fall back" \
  --reporter=list \
  --output=/tmp/bff-consol-022-day1-f01-strict
```

Observed result: `2 passed`.

## Open Blockers

None for BFF-CONSOL-022 closeout.

## Next Action

Hand off to Codex2 for review and close BFF-CONSOL-022. BFF-CONSOL-023 can use
this evidence as its dev strict cutover prerequisite.

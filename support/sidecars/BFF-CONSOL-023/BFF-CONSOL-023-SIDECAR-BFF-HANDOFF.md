# BFF-CONSOL-023 Sidecar: BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `BFF-CONSOL-023-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `BFF-CONSOL-023` - Lovable prod strict cutover (preview-soak verification gate) |
| Parent owner / reviewer | Codex / Gemini2 |
| Prepared by | Codex2 |
| Reviewer | Codex |
| Date | 2026-05-15 |
| Mutates canonical truth | false |

## Purpose

This support-only packet gives the parent owner the current BFF query gap map,
operator journey, and frontend handoff checklist for `BFF-CONSOL-023`.

The parent task targets the Lovable main/dev frontend deployment at
`https://pantheon-dev.lovable.app`. Pantheon currently has only the dev BFF
tier, so this is not a backend production promotion. This packet does not
change L1 canonical truth, BFF runtime code, route manifests, contract truth,
registry/governance implementation, or execute-plans source.

## Current State Snapshot

Source material checked for this packet:

- Task brief:
  `.orchestrator/task-briefs/bff_consol_023_sidecar_bff_handoff.md`
- Active task state in `ai-status.json` for `BFF-CONSOL-023` and this sidecar.
- Parent evidence:
  `support/evidence/BFF-CONSOL-023-prod-strict-soak.md`,
  `support/evidence/BFF-CONSOL-023-authenticated-live.json`, and
  `support/evidence/BFF-CONSOL-023-main-browser/hosted-browser-bff-probe-2026-05-15.md`
- Prerequisite evidence:
  `support/evidence/BFF-CONSOL-022-staging-strict-soak.md` and
  `support/evidence/BFF-CONSOL-022-day1-browser/hosted-browser-bff-probe-2026-05-15.md`
- Target env file: `execute-plans/.lovable/prod-strict.env`
- Frontend write gate reference: `execute-plans/src/lib/bff/runAction.ts`

Current parent state:

| Area | State |
|---|---|
| BFF-CONSOL-022 prerequisite | Complete enough for 023 handoff: dev BFF strict regression evidence passed; fixed elapsed-day soak gate removed. |
| Main Lovable health | Healthy against dev BFF: hosted browser BFF probe passed with 11/11 responses and no old-BFF URL hits. |
| Dev BFF authenticated read smoke | Passed: 32 total, 32 passed, 0 failed, 30 read probes, 0 write probes. |
| Strict cutover proof | Blocked: current hosted bundle `/assets/index-vlevju41.js` does not contain build-time `VITE_BFF_FALLBACK:"strict"`. |
| Write posture | Must remain disabled: `VITE_BFF_REAL_WRITES=false`; evidence records no live capital side effects. |

Important blocker: the parent evidence proves current main Lovable health and
runtime strict regression checks, but not the required build-time main
deployment cutover. `BFF-CONSOL-023` should not move to review until the
Lovable main build is republished with strict fallback embedded and the smoke
set is rerun.

## BFF Query Gap Analysis

### Closed for the 023 Cutover Gate

The current dev BFF endpoint is reachable and authenticated smoke did not find
transport or route-contract failures:

```text
target=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
health=200
openapi=200
total=32
passed=32
failed=0
read_probes=30
write_probes=0
live_capital_side_effects=false
```

The hosted browser probe against `https://pantheon-dev.lovable.app/management`
observed successful BFF traffic for `/bff/me`, `/bff/v5/control-room`,
`/health`, `/bff/alerts`, `/bff/approvals`, `/bff/jobs`, persona/strategy
health routes, and `/bff/events/stream`. It also confirmed the page bundle
contains the intended dev BFF URL and does not contain or hit the old BFF URL.

### Still Open

| Gap | Evidence | Parent action |
|---|---|---|
| Main bundle is not build-time strict | `support/evidence/BFF-CONSOL-023-prod-strict-soak.md` records asset `index-vlevju41.js` with `VITE_BFF_MODE` and `VITE_BFF_BASE_URL`, but no `VITE_BFF_FALLBACK:"strict"` or `VITE_BFF_REAL_WRITES:"false"` string. | Set Lovable main/dev env from `execute-plans/.lovable/prod-strict.env`, rebuild/publish, then rerun asset env check. |
| Browser probe is core-route evidence, not full Pack A/B/C coverage | Browser probe required only `/bff/v5/control-room`; `/bff/me` was optional and observed. | After republish, rerun authenticated read smoke plus hosted browser probe; do not rely on browser probe alone for full route coverage. |
| Some authenticated read families are live-empty | Smoke passed but several families reported `data_count: 0` or object envelopes without a count, including strategies, capital-pools, rebalances, artifacts, MCP/tools/skills, ranking formulas, research experiments, agora signals/journal, loop-runs, and sentinel findings. | Treat as empty live state, not mock fallback. If operator acceptance requires visible rows for any family, seed/live data readiness is a parent follow-up, not a sidecar implementation change. |
| Full F01 startup spec has a dev-stub capability mismatch | Parent evidence records focused F01 strict/no-fallback passed, while full F01 had one failure expecting `runtime.read` from the current dev stub token. | Keep using the focused strict/no-fallback subset for 023. Track capability vocabulary separately if the parent wants full F01 green under the dev stub token. |

No new BFF route is identified by this sidecar. The gate is a deployment/env
publish gap, plus a distinction between route availability and visible live
data density.

## Operator Journey

### Main Strict Read Journey

```text
Operator opens https://pantheon-dev.lovable.app/management
  -> bundle must be rebuilt with VITE_BFF_MODE=live
  -> bundle must include VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
  -> bundle must include VITE_BFF_FALLBACK=strict
  -> UI establishes session through /bff/me
  -> operator reads control room, alerts, approvals, jobs, health panels,
     persona/strategy health, and available list/detail surfaces through BFF
  -> SSE opens through /bff/events/stream
  -> strict mode must surface typed unavailable/degraded states on failure;
     it must not silently substitute seed/mock data
```

Post-publish acceptance should record both:

- The asset env check proving the deployed bundle was built with strict mode.
- The same smoke evidence that already passed pre-cutover: authenticated read
  smoke, hosted browser BFF probe, F15 strict regression, and focused F01
  strict/no-fallback checks.

### Write Journey During 023

```text
Operator attempts a governed write
  -> frontend sees VITE_BFF_REAL_WRITES=false
  -> live write dispatch remains blocked
  -> no /bff/v1/commands or /bff/actions/* request should be sent by normal UI
  -> no live capital side effects are allowed
```

`execute-plans/src/lib/bff/runAction.ts` also denies stub sessions when
`VITE_BFF_MODE=live` or `VITE_BFF_FALLBACK=strict`. For 023, this is a second
safety boundary; the primary publish requirement remains
`VITE_BFF_REAL_WRITES=false`.

## Frontend Handoff

### Target Lovable Main Env

Use the committed target env as the source for Lovable main/dev publish:

```env
VITE_BFF_MODE=live
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
VITE_BFF_FALLBACK=strict
VITE_BFF_REAL_WRITES=false
```

Do not point this task at a fabricated staging or production BFF hostname.
Do not set `VITE_BFF_REAL_WRITES=true` as part of 023.

### Required Post-Publish Checks

Parent owner should rerun and paste concise results into
`support/evidence/BFF-CONSOL-023-prod-strict-soak.md`:

```bash
# 1. Verify target env file values locally.
set -a; . execute-plans/.lovable/prod-strict.env; \
  test "$VITE_BFF_MODE" = live; \
  test "$VITE_BFF_BASE_URL" = https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io; \
  test "$VITE_BFF_FALLBACK" = strict; \
  test "$VITE_BFF_REAL_WRITES" = false

# 2. Verify the hosted asset now embeds strict fallback.
asset=$(curl -fsSL https://pantheon-dev.lovable.app/management | \
  rg -o '/assets/index-[^"<>]+\.js' | head -1); \
curl -fsSL "https://pantheon-dev.lovable.app${asset}" | \
  rg -o 'VITE_BFF_BASE_URL:"[^"]*"|VITE_BFF_DEV_BEARER_TOKEN:"[^"]*"|VITE_BFF_MODE:"[^"]*"|VITE_BFF_FALLBACK:"[^"]*"|VITE_BFF_REAL_WRITES:"[^"]*"'

# 3. Verify authenticated BFF reads against the dev BFF target.
PANTHEON_BFF_SMOKE_BEARER_TOKEN=<redacted> \
python3 scripts/probe_bff_authenticated_live.py \
  --base-url https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
  --output support/evidence/BFF-CONSOL-023-authenticated-live.json

# 4. Verify hosted browser traffic uses the intended BFF and SSE path.
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
PANTHEON_AUDIT_OUT_DIR=/home/lupin/code/pantheon/support/evidence/BFF-CONSOL-023-main-browser \
PANTHEON_PROBE_NOCACHE_SHA=bff-consol-023-main-post-publish-<date> \
node execute-plans/scripts/probe-hosted-browser-bff.mjs
```

If the asset env check still omits `VITE_BFF_FALLBACK:"strict"`, keep the
parent blocked even if all route smoke passes.

### Reviewer Checklist for Codex

- Confirm this sidecar only adds
  `support/sidecars/BFF-CONSOL-023/BFF-CONSOL-023-SIDECAR-BFF-HANDOFF.md`
  plus L0 status updates made through `scripts/ai-status.sh`.
- Confirm no L1 canonical truth, BFF runtime implementation, route snapshots,
  registry/governance code, or execute-plans source was modified by this sidecar.
- Confirm the packet does not claim 023 is complete; it identifies the build-time
  strict publish blocker.
- Confirm the packet preserves the dev-BFF-only deployment boundary and keeps
  `VITE_BFF_REAL_WRITES=false`.
- Confirm route/data wording distinguishes successful route contracts from
  live-empty business data.

## Sidecar Verification

Focused checks used for this support packet:

```bash
jq '.tasks[] | select(.id=="BFF-CONSOL-023-SIDECAR-BFF-HANDOFF")' ai-status.json
sed -n '1,260p' .orchestrator/task-briefs/bff_consol_023_sidecar_bff_handoff.md
sed -n '1,260p' support/evidence/BFF-CONSOL-023-prod-strict-soak.md
sed -n '1,260p' support/evidence/BFF-CONSOL-023-main-browser/hosted-browser-bff-probe-2026-05-15.md
sed -n '1,980p' support/evidence/BFF-CONSOL-023-authenticated-live.json
sed -n '1,240p' support/evidence/BFF-CONSOL-022-staging-strict-soak.md
sed -n '1,220p' execute-plans/src/lib/bff/runAction.ts
git diff --check -- support/sidecars/BFF-CONSOL-023/BFF-CONSOL-023-SIDECAR-BFF-HANDOFF.md
```

Observed summary:

- Parent evidence records pre-cutover smoke as healthy but blocked because the
  current main asset is not build-time strict.
- BFF-CONSOL-022 evidence now uses the existing dev BFF target and no longer
  requires a fixed elapsed-day soak gate.
- Authenticated read smoke and hosted browser probe are parseable and show no
  failed routes or old-BFF URL hits.
- This packet is support-only and does not mutate canonical truth or runtime
  implementation.

## Owner Closeout

Closeout state on 2026-05-15:

- Reviewer `Codex` approved the packet in `ai-status.json` with no blocking
  findings and confirmed commit `5c9995d5` only added this support artifact.
- Owner `Codex2` confirmed the approved sidecar scope is still true: the packet
  remains support-only and does not modify L1 canonical truth, BFF runtime code,
  route manifests, registry/governance code, or execute-plans source.
- Parent `BFF-CONSOL-023` remains blocked on Lovable main build-time strict
  publish/rebuild and post-publish smoke; this sidecar does not claim parent
  completion.

Closeout verification:

```bash
jq '.tasks[] | select(.id=="BFF-CONSOL-023-SIDECAR-BFF-HANDOFF")' ai-status.json
git show --stat --format=fuller 5c9995d5 --
git diff --check -- support/sidecars/BFF-CONSOL-023/BFF-CONSOL-023-SIDECAR-BFF-HANDOFF.md
git status --short
```

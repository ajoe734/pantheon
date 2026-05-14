# BFF-CONSOL-022 Sidecar: Strict Preview Handoff Packet

| Field | Value |
|---|---|
| Task ID | BFF-CONSOL-022-SIDECAR-BFF-HANDOFF |
| Parent task | BFF-CONSOL-022 - Lovable staging strict cutover (isolated preview branch) |
| Helper kind | bff_handoff_packet |
| Prepared by | Codex2 |
| Reviewer | Claude2 |
| Date | 2026-05-13 |
| Mutates canonical truth | false |
| Status | review approved; ready for parent-owner absorption |

## Purpose

This is a support-only packet for Gemini2, the BFF-CONSOL-022 parent owner.
It consolidates the strict-mode BFF query surface, frontend environment
handoff, operator journey, and soak evidence expectations needed to open an
isolated Lovable preview branch with:

```env
VITE_BFF_MODE=live
VITE_BFF_BASE_URL=https://pantheon-staging-bff.34.81.225.122.sslip.io
VITE_BFF_FALLBACK=strict
VITE_BFF_REAL_WRITES=false
```

This packet does not change L1 canonical truth, runtime code, registry code,
governance implementation, or the parent task's acceptance status.

## Current State Snapshot

| Item | Current state | Handoff implication |
|---|---|---|
| Parent task | `BFF-CONSOL-022` is active `todo`, owner `Gemini2`, reviewer `Gemini` | This packet is input for parent execution; it is not parent completion evidence. |
| Dependency | `BFF-CONSOL-015` is archived `done` with live-mode mock-only badge/empty-state behavior reviewed by Claude | Strict preview can rely on mock-only/deferred helpers returning empty/unavailable in live mode instead of silently seeding. |
| Pantheon repo env artifact | `execute-plans/.lovable/preview-strict.env` exists and already carries the four expected strict-preview values | Gemini2 should verify the Lovable preview branch consumes this file or an equivalent branch-scoped env. |
| Frontend sibling checkout | `/home/lupin/code/execute-plans` has `.env.staging-live.example` with the same strict values, but no `.lovable/preview-strict.env` at inspection time | If Gemini2 deploys from the sibling frontend checkout, create or wire the preview-branch env there without changing main staging. |
| Main staging | Active deployment env was not inspected by this sidecar | Do not infer active staging fallback from example files. Parent acceptance requires existing staging to remain auto fallback or otherwise unchanged; only the preview branch should be strict. |

## Strict Mode Behavior To Preserve

In `/home/lupin/code/execute-plans/src/lib/bff-v1/liveTransport.ts`:

- `VITE_BFF_FALLBACK=strict` converts network, 5xx, and transport failures
  into typed `BffError` paths instead of returning mock data.
- Backend 4xx/409/428 responses propagate as real typed responses, not mock
  fallback events.
- `VITE_BFF_REAL_WRITES=false` keeps write dispatch disabled unless the
  frontend write gate is intentionally opened later.

In the BFF-CONSOL-015 live badge work:

- `mock_only_dev` helpers return empty/null in `VITE_BFF_MODE=live`.
- `deferred` helpers return explicit empty/unavailable state in live mode.
- `live_required` helpers are not labeled as helper-owned mock data.

Expected preview signal:

- Healthy strict live reads show no seed fallback banner.
- Strict transport failure shows typed-error UI and "seed fallback blocked"
  semantics.
- Write actions remain blocked client-side and should not submit
  `/bff/v1/commands` or `/bff/actions/*` during BFF-CONSOL-022.

## BFF Query Surface For The 7-Day Soak

The backend route manifest currently includes implemented read routes for the
strict-preview surface. `GET /bff/mcp-servers` and `GET /bff/mcp-tools` are
registered as aliases in the manifest; use the hyphenated paths in frontend
probes.

Minimum daily read probe set:

| Pack | Routes to verify | Required result |
|---|---|---|
| Session | `GET /bff/me`, `GET /health`, `GET /openapi.json` | 2xx and required fields. |
| Pack A | `/bff/strategies`, `/bff/personas`, `/bff/capital-pools`, `/bff/rebalances`, `/bff/deployments` | 2xx and `data`/`items` count >= 1. |
| Pack B | `/bff/evolution-programs`, `/bff/research-experiments`, `/bff/artifacts`, `/bff/v5/interventions`, `/bff/runtimes`, `/bff/agora/signals`, `/bff/agora/ask/sessions` | 2xx and count >= 1 where list-backed. |
| Pack C | `/bff/alerts`, `/bff/incidents`, `/bff/approvals`, `/bff/audit`, `/bff/jobs`, `/bff/channels`, `/bff/skills`, `/bff/tools`, `/bff/mcp-servers`, `/bff/mcp-tools` | 2xx and count >= 1 where list-backed. |
| SSE | `/bff/events/stream?channel=approval` | Stream opens using real BFF transport; no client-side mock SSE generator. |

Fixture facts checked for this packet:

- Pack A declares one strategy, persona, capital pool, rebalance, and
  deployment family row; tests assert non-empty BFF list routes and detail
  linkages for strategy, persona, and deployment.
- Pack B declares non-empty evolution, research, artifacts, v5 intervention,
  agora, and runtime families; tests assert governed remediation skeleton,
  active Agora session with `sse_topic`, research-analysis linkage, and
  paper-canary runtime fail-closed flags.
- Pack C declares non-empty alerts, incidents, approvals, audit, jobs,
  channels, skills, tools, and MCP families; tests assert linked alert/incident
  and approval/deployment references, append-only audit sample, SSE channel
  catalog alignment, detail routes, and logs.

## Known Gaps And Parent-Owner Checks

| Gap or risk | Parent-owner check |
|---|---|
| Existing staging must stay auto fallback or otherwise unchanged | Verify actual deployment variables, not only `.env.staging-live.example`, before and after preview branch creation. |
| Pantheon repo has `execute-plans/.lovable/preview-strict.env`, sibling frontend checkout did not | Ensure the checkout used by Lovable has the preview env file or equivalent branch-scoped env before claiming criterion 1. |
| BFF-CONSOL-011 evidence file exists but local artifact inspection did not show populated route probes | Re-run SSE probe against staging BFF during Day 1 and record fresh output under BFF-CONSOL-022 evidence. |
| Strict mode will expose any missing/empty live read instead of masking it with seed | Treat any network/5xx typed error, empty required route, or seed fallback signal as a soak regression. |
| Parent task requires 7 days | Do not mark parent done from Day 1 smoke. Record daily results until the soak window is complete. |

## Operator Journey Under Strict Preview

### Read and detail path

```text
Operator opens isolated Lovable preview
  -> preview env resolves VITE_BFF_MODE=live and VITE_BFF_FALLBACK=strict
  -> frontend authenticates with cookie or bearer session
  -> GET /bff/me confirms user, tenant, and capabilities
  -> operator opens strategy/persona/deployment and Pack B/C list pages
  -> each list returns 2xx with at least one live-backed row
  -> detail routes return 2xx or typed 404 where the record is intentionally absent
  -> no seed fallback banner appears during healthy strict live reads
```

### SSE path

```text
Operator opens approval/event surface
  -> frontend opens /bff/events/stream?channel=approval with credentials
  -> BFF responds with replay-capable SSE headers
  -> event envelopes flow through the real BFF SSE transport
  -> if replay cursor is unavailable, frontend resyncs from /bff/approvals
     or /bff/v5/interventions before opening a fresh stream
```

BFF-CONSOL-012 locked the relevant SSE bounds: replay window 500 events,
subscriber queue 1000 events, newest-drop for a saturated subscriber queue,
oldest-evict for the replay buffer, and 409 `SSE_REPLAY_UNAVAILABLE` with
resync routes when the cursor is outside the replay window.

### Write path

```text
Operator attempts deploy, approve, remediate, kill-switch, or similar write
  -> frontend checks VITE_BFF_REAL_WRITES
  -> false blocks dispatch
  -> no /bff/v1/commands or /bff/actions/* request is sent
  -> UI shows the disabled-write guard state
```

BFF-CONSOL-022 is a read/SSE strict soak. Write enablement belongs to later
parent work and should remain off for this preview.

## Evidence Packet Template For `support/evidence/BFF-CONSOL-022-staging-strict-soak.md`

Suggested fields:

```markdown
# BFF-CONSOL-022 Staging Strict Soak

Preview URL:
Preview branch:
BFF base URL: https://pantheon-staging-bff.34.81.225.122.sslip.io
Preview env:
- VITE_BFF_MODE=live
- VITE_BFF_FALLBACK=strict
- VITE_BFF_REAL_WRITES=false

Main staging fallback verified as unchanged:

| Day | Date (UTC) | Read probe | SSE probe | UI smoke | Fallback regression | Notes |
|---|---|---|---|---|---|---|
| 1 | 2026-05-13 | pending | pending | pending | pending | |
| 2 |  | pending | pending | pending | pending | |
| 3 |  | pending | pending | pending | pending | |
| 4 |  | pending | pending | pending | pending | |
| 5 |  | pending | pending | pending | pending | |
| 6 |  | pending | pending | pending | pending | |
| 7 |  | pending | pending | pending | pending | |

Regression threshold: zero unhandled 5xx, zero required-route empty results,
zero mock fallback activations, zero write dispatches while
VITE_BFF_REAL_WRITES=false.
```

## Suggested Verification Commands

Local non-network checks used or recommended for parent setup:

```bash
python3 -m py_compile scripts/probe_bff_authenticated_live.py scripts/probe_bff_sse_stream.py
python3 -m json.tool support/evidence/BFF-CONSOL-012-sse-backpressure.json >/tmp/bff-consol-012-sse.json
python3 -m pytest \
  services/control-plane/bff/test_bff_consol_008_fixture_pack_a.py \
  services/control-plane/bff/test_bff_consol_009_fixture_pack_b.py \
  services/control-plane/bff/test_bff_consol_010_fixture_pack_c.py \
  services/control-plane/bff/tests/test_sse_backpressure.py -q
```

Daily staging read probe:

```bash
PANTHEON_BFF_SMOKE_JWT_SECRET=<redacted> \
python3 scripts/probe_bff_authenticated_live.py \
  --base-url https://pantheon-staging-bff.34.81.225.122.sslip.io \
  --output support/evidence/BFF-CONSOL-022-day1-authenticated-live.json
```

Daily staging SSE probe:

```bash
PANTHEON_BFF_SMOKE_JWT_SECRET=<redacted> \
PANTHEON_BFF_JWT_SECRET=<redacted> \
python3 scripts/probe_bff_sse_stream.py \
  --base-url https://pantheon-staging-bff.34.81.225.122.sslip.io \
  --output support/evidence/BFF-CONSOL-022-day1-sse.json
```

Lovable/GitHub integration gate, after the preview URL exists:

```bash
gh workflow run pantheon-integration-gate.yml \
  --field fe_base_url=<preview-branch-lovable-url> \
  --field bff_base_url=https://pantheon-staging-bff.34.81.225.122.sslip.io
```

## Handoff Checklist For Gemini2

| Check | Status for this sidecar | Parent action |
|---|---|---|
| Support-only packet created | complete | Review and absorb into BFF-CONSOL-022 execution. |
| Canonical truth untouched | complete | Keep parent edits scoped to preview env and evidence. |
| Strict env content supplied | complete | Verify branch deployment consumes it. |
| BFF route gap summarized | complete | Run Day 1 live probes against actual staging BFF. |
| Operator journey documented | complete | Use it as the manual UI smoke path. |
| 7-day soak evidence | pending parent work | Create and maintain `support/evidence/BFF-CONSOL-022-staging-strict-soak.md`. |

## Sidecar Verification Record

This packet was refreshed from the task brief, `ai-status.json`, BFF route
manifest, fixture pack data/tests, BFF-CONSOL-015 archive/review artifacts,
frontend env/config files, and BFF-CONSOL-012 SSE evidence. It intentionally
removed the stale archived closeout claim from the previous copy of this file.

Commands run before reviewer handoff:

```bash
git diff --check -- support/sidecars/BFF-CONSOL-022/BFF-CONSOL-022-SIDECAR-BFF-HANDOFF.md
```

Result: passed.

```bash
python3 -m py_compile scripts/probe_bff_authenticated_live.py scripts/probe_bff_sse_stream.py
```

Result: passed.

```bash
python3 -m json.tool support/evidence/BFF-CONSOL-012-sse-backpressure.json >/tmp/bff-consol-012-sse.json
```

Result: passed.

```bash
python3 -m pytest \
  services/control-plane/bff/test_bff_consol_008_fixture_pack_a.py \
  services/control-plane/bff/test_bff_consol_009_fixture_pack_b.py \
  services/control-plane/bff/test_bff_consol_010_fixture_pack_c.py \
  services/control-plane/bff/tests/test_sse_backpressure.py -q
```

Result: `24 passed in 13.84s`.

Local ASCII scan result: `non_ascii_count 0`.

## Owner Closeout Record

Closeout was run by Codex2 after Claude2 review approval. No canonical truth,
runtime, registry, or governance files were changed by this sidecar.

Commands rerun during owner finalization:

```bash
python3 -m py_compile scripts/probe_bff_authenticated_live.py scripts/probe_bff_sse_stream.py
```

Result: passed.

```bash
python3 -m json.tool support/evidence/BFF-CONSOL-012-sse-backpressure.json
```

Result: passed.

```bash
python3 -m pytest services/control-plane/bff/test_bff_consol_008_fixture_pack_a.py services/control-plane/bff/test_bff_consol_009_fixture_pack_b.py services/control-plane/bff/test_bff_consol_010_fixture_pack_c.py services/control-plane/bff/tests/test_sse_backpressure.py -q
```

Result: passed, 24 passed in 23.21s.

# BFF-LUV-SEM-006 Sidecar BFF Handoff Packet

Task ID: BFF-LUV-SEM-006-SIDECAR-BFF-HANDOFF
Parent Task: BFF-LUV-SEM-006
Helper kind: bff_handoff_packet
Owner: Claude
Reviewer: Codex2
Prepared: 2026-05-09T11:00:00Z

## Scope

This is a support-only sidecar for the BFF-LUV-SEM-006 parent deployment task. It does not define canonical architecture, update route truth, or change runtime behavior. The parent owner should use it as a short handoff packet when deciding how to finish and verify the execute-plans BFF semantic completion cutover on lupin dev.

## Current Semantic Completion Status

| Task | Title | Status | Key deliverable |
|---|---|---|---|
| BFF-LUV-SEM-001 | Session auth lifecycle | **done** | `/bff/auth/refresh`, `/bff/logout`, `/bff/switch-tenant`, `PATCH /bff/me/locale` are real session mutation routes backed by `session_lifecycle_store.py` |
| BFF-LUV-SEM-003 | Entity detail read models | **done** | All final `{id}` aliases project real DTOs from `read_store`; unknown entity returns honest 404 or degraded DTO |
| BFF-LUV-SEM-005 | Agora extended semantics | **done** | inbox, ask/sessions, skill-coaching, persona-lab, evaluations, postmortems wired to read-store adapters; `POST /bff/agora/ask` persists session/message before command receipt |
| BFF-LUV-SEM-002 | Command execution bridge | **review** | Deployment/rebalance/audit/confirm-token/v5-intervention/sentinel command routes backed by command store; idempotency hash and replay fixes committed (b02bce71); pending Codex2 re-review |
| BFF-LUV-SEM-004 | v5 loop/sentinel runtime | **in_progress** | loop-runs and sentinel findings wired to incidents-derived read-store adapters; control-room composes real child models; source-aware metadata fixes being reviewed |

**Blocker**: SEM-006 parent deployment cannot proceed until SEM-002 and SEM-004 reach `done` and their changes are present in the branch that gets deployed to lupin dev.

## BFF Query Gap — What Has Landed

The following surfaces are semantically complete and ready for live probe once deployed:

### Session / Auth (SEM-001)

- `GET /bff/me` — returns current user + session overrides (tenant, locale, refresh/logout state)
- `POST /bff/auth/refresh` — persists refresh; idempotent via `Idempotency-Key` / `X-Idempotency-Key`
- `POST /bff/logout` — marks session logged-out; idempotent
- `POST /bff/switch-tenant` — validates against allowed tenant scope, persists selected tenant
- `PATCH /bff/me/locale` — normalizes BCP-47 locale (e.g. `zh_tw` → `zh-TW`); persists

### Entity Detail Read Models (SEM-003)

- All final contract `{id}` aliases return projection DTOs for seeded records and honest 404 / degraded DTO for missing records:
  - strategies, personas, capital pools, rebalances, deployments
  - runtimes, research experiments, artifacts, ranking formulas
  - MCP servers, MCP tools, channels, skills, tools
  - incidents, alerts, v5 interventions

### Agora Extended (SEM-005)

- `GET /bff/agora/inbox` — read-store backed
- `GET /bff/agora/ask/sessions` — session/message store backed
- `POST /bff/agora/ask` — persists session + message, then command receipt; no LLM call in handler
- `GET /bff/agora/skill-coaching/sessions`, `GET /bff/agora/persona-lab/runs`
- `GET /bff/agora/postmortems`
- `GET /bff/agora/evaluation-suites`, `GET /bff/agora/evaluation-runs`
- Signal feedback validates payload; writes feedback record + command; degraded metadata when source absent

## BFF Query Gap — What Is Still Pending

### Command Execution Bridge (SEM-002 — review)

Pending routes that need SEM-002 to reach `done`:

- `POST /bff/deployments` — create deployment + command store record
- `PATCH /bff/deployments/{id}` — deployment patch command
- `PATCH /bff/rebalances/{id}` — rebalance patch command
- `POST /bff/audit/export` — audit export command
- `POST /bff/confirm-tokens`, `GET /bff/confirm-tokens/{id}`, `DELETE /bff/confirm-tokens/{id}`, `POST /bff/confirm-tokens/{id}/redeem` — confirm-token lifecycle backed by command store
- `POST /bff/actions/{entityType}/{entityId}/{actionId}` — canonical action bridge, command store backed
- `POST /bff/v5/interventions/{id}/actions/{actionId}` — v5 intervention command
- `POST /bff/v5/sentinel/findings/{id}/status` — sentinel finding status command
- `POST /bff/v5/sentinel/remediation/build`, `POST /bff/v5/sentinel/remediation/execute` — sentinel remediation commands

Known idempotency behavior after b02bce71:
- Same key + same payload replays → same receipt (201/202)
- Same key + different payload → 409 conflict
- No-body-id creates (deployments, sentinel-build) → server-generated id excluded from hash

### v5 Loop/Sentinel (SEM-004 — in_progress)

Pending routes that need SEM-004 to reach `done`:

- `GET /bff/v5/control-room` — composed read model (loop-runs + interventions + sentinel findings)
- `GET /bff/v5/loop-runs`, `GET /bff/v5/loop-runs/{id}`
- `GET /bff/v5/execution/persona-health`, `GET /bff/v5/execution/strategy-health`
- `GET /bff/v5/sentinel/findings`, `GET /bff/v5/sentinel/findings/{id}`

Known fix pending in SEM-004:
- Source-aware metadata: when `PANTHEON_BFF_LOOP_RUN_STORE` has data and incidents is absent, meta must report `loop_runs` as source; not `incidents`
- Empty incidents source (healthy but empty) must report source as available, not missing/degraded

## Operator Journey — Frontend Readiness

### Journey 1: Operator Daily Brief + Agora

**Status: Ready** (SEM-001 + SEM-003 + SEM-005 done)

Operator flow:
1. Login → `/bff/auth/refresh` persists session
2. View daily brief → Agora signals, watchlist, and notes routes respond with real DTOs
3. Signal feedback → validated and written to feedback store + command record
4. Ask session → session and message persisted; command receipt returned

### Journey 2: Deployment Management

**Status: Pending SEM-002 done**

Operator flow:
1. Create deployment → `POST /bff/deployments` must write command record, return tracking URL
2. Patch deployment → `PATCH /bff/deployments/{id}` same
3. Idempotent retries → same Idempotency-Key replays; different payload → 409

### Journey 3: v5 Control Room

**Status: Pending SEM-004 done**

Operator flow:
1. Open control room → `GET /bff/v5/control-room` composes loop-runs + interventions + sentinel findings
2. View loop run detail → `GET /bff/v5/loop-runs/{id}` returns seeded record or 404
3. Sentinel finding → `GET /bff/v5/sentinel/findings/{id}` same
4. Remediation build → `POST /bff/v5/sentinel/remediation/build` (SEM-002 scope)

## Live Probe Checklist for SEM-006 Parent Owner

After SEM-002 and SEM-004 are `done` and deployed to lupin dev:

### Step 1 — Verify OpenAPI schema

```bash
curl -s https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/openapi.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('paths',{})), 'paths')"
# Expected: ≥ 200 paths; status 200 (not 500)
```

### Step 2 — Anonymous route probe

Run the anonymous BFF probe (no auth token). Expect:
- Final contract auth-required routes → 401 (not 404, not 500)
- Public/health routes → 200

```bash
# Health check
curl -s https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/health | python3 -m json.tool

# Anonymous probe on a sample of final routes:
for path in /bff/me /bff/strategies /bff/deployments /bff/v5/control-room /bff/agora/signals; do
  status=$(curl -s -o /dev/null -w "%{http_code}" https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io$path)
  echo "$status  $path"
done
# Expected: 401 for auth-required routes, not 404 or 500
```

### Step 3 — Stub-auth smoke (local BFF, regression gate)

Before deploying, confirm the focused suite still passes on the local build:

```bash
python3 -m pytest \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
  services/control-plane/bff/test_bff_session_auth_me_contract.py \
  services/control-plane/bff/test_final_command_execution_bridge.py \
  services/control-plane/bff/test_bff_agora_core_contract.py \
  services/control-plane/bff/test_bff_agora_extended_contract.py \
  services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py \
  -q
# Expected: ≥ 60 passed, 0 failed
```

### Step 4 — Lovable / frontend handoff gate

The handoff note for `VITE_BFF_MODE=live` readiness must address:

| Check | Required state |
|---|---|
| `/openapi.json` → 200 on live target | Yes |
| Final contract anonymous routes → 401, not 404/500 | Zero 404, zero 500 |
| SEM-001 session routes on live target | 401 for unauthenticated, not 404 |
| SEM-002 command routes on live target | 401 for unauthenticated, not 404 |
| SEM-004 v5 control-room on live target | 401 for unauthenticated, not 404 |
| Lovable live mode safe? | Yes if all above pass; No if any 404/500 remain |

## Suggested Reviewer Focus

Codex2 reviewing this packet should check:

1. The status table reflects the current `ai-status.json` dependency states.
2. The pending-routes lists accurately reflect what is not yet `done` in SEM-002/SEM-004.
3. The live probe commands point to the correct public target URL (`pantheon-lupin-dev-bff.34.81.75.241.sslip.io`).
4. The operator journey readiness statements match the actual task acceptance criteria in the respective SEM task artifacts.
5. This packet does not introduce any canonical route or architecture truth.

## Reviewer Handoff

Reviewer should confirm that this packet stays support-only and that the parent owner can trace every suggestion back to:

- `docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-006-lupin-dev-live-cutover.md`
- `docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-001-session-auth-lifecycle.md`
- `docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-002-command-execution-bridge.md`
- `docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-003-entity-detail-read-models.md`
- `docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-004-v5-loop-sentinel-runtime.md`
- `docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-005-agora-extended-semantics.md`

This packet is ready for Codex2 review and parent-owner absorption decisions.

## Closeout Note (2026-05-09)

**Status: review_approved → done**

Reviewer Codex2 approved this packet as a historical checklist on 2026-05-09T11:32:28Z.

Post-approval updates (absorbed from parent task live evidence):

- SEM-002 (command execution bridge): now **done** — was listed as `review` at packet preparation time; archived done snapshot recorded in `ai-task-archive/tasks/BFF-LUV-SEM-002.json`.
- SEM-004 (v5 loop/sentinel runtime): now **done** — was listed as `in_progress` at packet preparation time; archived done snapshot recorded in `ai-task-archive/tasks/BFF-LUV-SEM-004.json`.
- The "Blocker" entry in the status table above no longer applies. Parent task BFF-LUV-SEM-006 completed live cutover with OpenAPI 200, zero 404/500, and `VITE_BFF_MODE=live safe` confirmed.

This sidecar packet is fully superseded by the parent task artifact:
- `docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-006-lupin-dev-live-cutover.md`

No canonical truth was modified. Sidecar scope boundary maintained throughout.

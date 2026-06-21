# AG-BE-ID-003 Sidecar BFF and Frontend Handoff Packet - Followup 15

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-15` |
| Helper parent | `AG-BE-ID-003` - Interactive/trainer/research session BFF facade |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Sidecar owner / reviewer | `Claude` / `Claude2` |
| Date | `2026-06-21` |
| Status | `in_progress; packet prepared for review` |
| Current dev base | `7bab8c5d5289f33ae427fa0bbba293ddb6495ac2` |
| Previous sidecar closeout | Followup-14 archived `done`; closeout PR `#2011` merged at `0d872d414517a2adc292553791beff02ff31731f` |
| New relevant dev delta | AG-BE-SW-001 workshop session/event persistence (PR `#2009`) and OPS-BFF-NLASK-GRACE nl/ask grace reduction (PR `#2012`) only; two subsequent PRs (MGMT-LIVE-EVIDENCE-PREFLIGHT-DIAG `#2015` and AGENT-USABILITY-OPENCLAW iterative OODA proof `#2016`) do not touch servant-session or AG-BE-ID-003 surfaces |
| Execute-plans compatibility PR | `#63` remains `OPEN` / `UNSTABLE`; `integration-gate` failed |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI/source-of-truth contract semantics, BFF runtime code,
route registries, governance policy, database migrations, OpenClaw adapter
code, compatibility manifest source, or execute-plans source files.

## 1. Purpose

This followup refreshes the `AG-BE-ID-003` BFF/frontend handoff after
followup-14 was reviewed, closed, and archived.

The material conclusion is unchanged: parent `AG-BE-ID-003` remains blocked,
waiting for `Claude` to decide how the servant-session create contract carries
or derives `interactive`, `trainer`, and `research_task`.

The fresh post-followup-14 facts are:

1. `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` is archived `done`; its
   support-only closeout PR `#2011` merged at `0d872d41`.
2. `origin/dev` advanced to `eb7e9ee0` through two changes: AG-BE-SW-001
   workshop session/event persistence (PR `#2009`) and OPS-BFF-NLASK-GRACE
   nl/ask inline grace time reduction from 12 s to 3 s (PR `#2012`).
3. The AG-BE-SW-001 workshop delta adds strategy workshop session and event
   persistence routes (`strategy_workshop/__init__.py`, `router.py`, `store.py`,
   `tests/test_agora_strategy_workshop.py`) and registers them in `main.py`.
   It does not add or change servant-session routes, `ServantSessionCreateRequest`,
   or any AG-BE-ID-003 support paths.
4. The nl/ask grace delta only changes the inline provider response wait from
   12 s to 3 s in `main.py`. It is operationally unrelated to AG-BE-ID-003.
5. Parent `AG-BE-ID-003` is still active `blocked`, owner `Codex2`, reviewer
   `Claude`, waiting for `Claude`.
6. Execute-plans PR `#63` is still open and unstable, with the
   `integration-gate` check failed.

This packet does not approve, reopen, or implement parent `AG-BE-ID-003`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Claude` and read the central status root via
`PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-15` | active `in_progress`; owner `Claude`, reviewer `Claude2` | This packet is the support-only artifact for review. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` | archived `done`; closeout PR `#2011` merged at `0d872d41` | Previous packet is durable; it kept the parent blocked. |
| `AG-BE-ID-003` | active `blocked`; owner `Codex2`, reviewer `Claude`, waiting for `Claude` | Parent implementation must not proceed until the servant-session type-contract decision is recorded. |
| `AG-BE-ID-002` | archived `done` | `/bff/agora/servant/ensure` remains the accepted upstream servant ensure/provision/reconcile surface. |
| `AG-BE-SW-001` | archived `done`; PR `#2009` merged | Workshop session/event persistence landed; separate from servant sessions. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-28` | archived `done`; closeout PR `#2008` merged at `b52dff9c` | Latest FE sidecar keeps session controls gated on blocked `AG-BE-ID-003`. |
| `AG-FE-ID-001` | active `todo`; depends on `AG-FE-000` and `AG-BE-ID-003` | Frontend parent implementation has not started in durable task state. |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-17` | archived `done`; PR `#2010` merged before followup-14 | Dashboard editor support context only; no servant-session implication. |
| `AG-XR-003` | archived `done` | Pantheon-side compatibility manifest/gate task is closed; execute-plans PR `#63` remains separate frontend follow-through risk. |

Dependency honesty rule: frontend and sidecar work may use identity,
capability, and servant-profile readiness as limited support context, but it
must not claim interactive, trainer, or research-task servant-session readiness
while `AG-BE-ID-003` is blocked.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_15.md` | This task-scoped assignment and support-only boundary. |
| `AI_NAME=Claude PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-15` | Confirms active `in_progress` owner/reviewer, artifact path, dependency, and support-only acceptance. |
| `AI_NAME=Claude PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-BE-ID-003` | Confirms parent remains blocked on the servant-session type-contract decision. |
| `AI_NAME=Claude PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` | Confirms predecessor archived `done`, with closeout PR `#2011` merged at `0d872d41`. |
| `AI_NAME=Claude PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-BE-SW-001` | Confirms workshop persistence task archived `done`; separate from servant sessions. |
| `AI_NAME=Claude PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-28` | Confirms latest FE support packet is archived `done` and still gates session UI on AG-BE-ID-003. |
| `AI_NAME=Claude PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-FE-ID-001` | Confirms FE parent remains `todo` and depends on blocked `AG-BE-ID-003`. |
| `git log --oneline 0d872d41..origin/dev` | Shows AG-BE-SW-001 workshop and nl/ask grace activity after followup-14 closeout. |
| `git diff --name-status 0d872d41..origin/dev -- <checked pathset>` | Shows only workshop service files, task briefs, and nl/ask grace constant in `main.py`; no AG-BE-ID-003 support path or OpenAPI/spec/contract runtime delta. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` and `agora_v1_2.openapi.yaml` | `ServantSessionCreateRequest` still lacks a public type field and rejects undeclared top-level fields. |
| `services/control-plane/bff/agora/servant/router.py` | Runtime still owns `/bff/agora/servant/ensure`; no servant-session route family is implemented there. |
| `rg -n "servant/sessions\|/bff/agora/servant/sessions\|OPENCLAW_UPSTREAM_DEGRADED\|ServantSession" services/control-plane/bff` | No matches; no runtime servant-session route family or accepted degraded code found in checked BFF paths. |
| `gh pr view 63 --repo ajoe734/execute-plans` | Confirms PR `#63` remains `OPEN`, `UNSTABLE`, and failed `integration-gate`. |
| `/home/lupin/code/execute-plans` remote tree probes after `git fetch origin --prune` | Confirm target Agora shell/client files remain absent from both remote trees except `types.ts` on `origin/dev`, `AskPersonas.tsx`, and `src/lib/bff/agora.ts`. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup 14

Baseline: followup-14 closeout PR `#2011` merged at
`0d872d414517a2adc292553791beff02ff31731f`.

| Change | What changed | Parent implication |
|---|---|---|
| Followup-14 closed | Archived `done`; closeout says parent remains blocked on Claude's servant-session type-contract decision. | Treat followup-14 as accepted support evidence. |
| AG-BE-SW-001 closed | PR `#2009` merged workshop session/event persistence (`strategy_workshop/router.py`, `store.py`, `__init__.py`, tests) and registered routes in `main.py`. | Workshop strategy routes are separate from servant sessions; no AG-BE-ID-003 unblock. |
| OPS-BFF-NLASK-GRACE | PR `#2012` lowered `_MGMT_NL_PROVIDER_INLINE_GRACE_DEFAULT_SECONDS` from `12.0` to `3.0` in `main.py`. | Management nl/ask operational tweak; no servant-session implication. |
| Checked BFF/OpenAPI/spec paths | No changed files in the post-followup-14 checked pathset for servant sessions, OpenAPI, or AG-BE-ID-003 support paths. | No new evidence unblocks AG-BE-ID-003. |
| MGMT-LIVE-EVIDENCE-PREFLIGHT-DIAG (PR `#2015`) | Adds BFF live evidence preflight scripts (`write_bff_live_evidence_preflight.py`, `test_write_bff_live_evidence_preflight.py`), updates release-gate aggregation in `execute-plans/scripts/aggregate-release-gate.mjs`, and adds CI workflow steps. | Management/ops surface only; no servant-session or AG-BE-ID-003 path changes. |
| AGENT-USABILITY-OPENCLAW iterative OODA proof (PR `#2016`) | Expands `services/persona/openclaw_adapter_backed_flow.py` with iterative OODA validation proof and adds accompanying test. | Persona/OpenClaw adapter backed flow; separate from BFF servant-session routes and AG-BE-ID-003 contract surfaces. |
| `ServantSessionCreateRequest` | v1.1 and v1.2 still expose only `intent`, `strategy_ref`, and open `metadata`, with `additionalProperties: false` at top level. | Strict clients still cannot send undeclared top-level `session_type`, `sessionType`, or `session_kind`. |
| Runtime servant sessions | Targeted BFF grep found no `servant/sessions`, `/bff/agora/servant/sessions`, `ServantSession`, or `OPENCLAW_UPSTREAM_DEGRADED`. | Parent still needs implementation after the contract decision. |
| Execute-plans PR `#63` | Still `OPEN` / `UNSTABLE`; `integration-gate` failed. | Frontend compatibility follow-through remains a separate risk. |

## 5. Contract Decision Request

### D1 - Public create schema still has no type field

Both v1.1 and v1.2 define `ServantSessionCreateRequest` as a strict top-level
object with only:

```yaml
intent:
  type: string
strategy_ref:
  type: string
metadata:
  type: object
  additionalProperties: true
additionalProperties: false
```

No public `session_type`, `sessionType`, `session_kind`, or equivalent
top-level field exists.

### D2 - Metadata is not an approved type contract

`metadata` is open, so `metadata.session_type` is technically possible, but it
is still a hidden contract unless `Claude` explicitly approves that rule and
names the mapping to OpenClaw.

### D3 - Runtime OpenClaw ops route is not the servant facade

`POST /api/v1/operator/openclaw/sessions` requires `agent_id` and
`session_type`, but it is an operator OpenClaw ops surface, not the Agora
servant-session facade required by AG-BE-ID-003. It does not resolve where the
Agora client sends or how BFF derives `interactive`, `trainer`, and
`research_task`.

### D4 - Research-task mapping remains unresolved

Checked evidence still does not name the approved OpenClaw skill/session kind
for `research_task`. Parent should stay blocked until this is decided.

## 6. Current Route Evidence

| Surface | Current observation at dev `eb7e9ee0` | Readiness impact |
|---|---|---|
| v1.1/v1.2 OpenAPI servant-session routes | Paper routes exist for create/message/terminate/stream. | Contract family exists on paper, but create is underspecified for type. |
| `ServantSessionCreateRequest` | No public type field; top-level unknown properties rejected. | Blocks strict create clients and parent implementation review. |
| Runtime servant router | Implements `/bff/agora/servant/ensure` only. | Compose with AG-BE-ID-002; do not overwrite ensure behavior. |
| Runtime servant sessions | No checked BFF match for `/bff/agora/servant/sessions*`. | Parent must still implement after contract decision. |
| Workshop session routes | AG-BE-SW-001 added `POST /bff/agora/strategy_workshop/sessions` and related routes. | Workshop routes are distinct; do not conflate with servant sessions. |
| Legacy `/bff/agora/sessions*` | Existing route in `main.py` creates legacy ask/session records and accepts `mode` or `sessionType`. | Not a safe substitute for servant sessions. |
| OpenClaw ops session create | Requires `agent_id` and `session_type` at `/api/v1/operator/openclaw/sessions`. | Useful lower-level context only; not the Agora servant-session public contract. |
| Degraded error | `OPENCLAW_UPSTREAM_DEGRADED` not found in checked BFF runtime paths. | Parent must add this exact code or record reviewer approval for a precise equivalent. |
| Frontend parent state | `AG-FE-ID-001` remains `todo`; latest FE sidecar says session controls stay disabled. | No frontend runtime readiness to absorb. |

## 7. Frontend Handoff

Until the parent records the type-contract decision and lands the runtime route
family, execute-plans should keep servant-session create/message/stream/terminate
controls disabled in strict live mode.

Remote probe source: `/home/lupin/code/execute-plans` after `git fetch origin
--prune`. The local checkout remains ahead/behind origin/main, so
implementation truth should come from remote trees or a clean task worktree.

| Surface | Current remote-tree state | Handoff rule |
|---|---|---|
| `src/agora/AgoraApp.tsx` | Missing from both `origin/main` and `origin/dev`. | Parent FE work still needs an approved shell file or explicit blocker. |
| `src/lib/bff-v1/agora/identity.ts` | Missing from both checked remote trees. | Parent must add strict identity/capability clients before claiming shell readiness. |
| `src/lib/bff-v1/agora/servant.ts` | Missing from both checked remote trees. | Parent must add strict servant ensure client before shell can show servant readiness. |
| `src/lib/bff-v1/agora/types.ts` | Present on `origin/dev`, missing from `origin/main`. | Generated types are branch-dependent and not runtime proof. |
| `src/entries/agora-main.tsx`, `vite.agora.config.ts`, `agora.html` | Missing from both checked remote trees. | Parent must verify frontend delivery base before depending on Agora-specific entry points. |
| `src/agora/pages/AskPersonas.tsx` | Present on both checked remote trees. | Ask/session controls must remain gated by backend readiness. |
| `src/lib/bff/agora.ts` | Present on both checked remote trees. | Existing broad client is not enough for AG-FE-ID-001 strict v1 client acceptance. |
| execute-plans PR `#63` | `OPEN`; `UNSTABLE`; `integration-gate` failed. | Do not claim strict deployment compatibility from Pantheon-side status alone. |

### Safe now

| Frontend action | Surface | Caveat |
|---|---|---|
| Resolve operator Agora scope | `GET /bff/agora/me` | Identity scope only. |
| Display filtered capability readiness | `GET /bff/agora/capabilities` | Discovery/readiness only; not session runtime proof. |
| Show servant readiness after user action | `POST /bff/agora/servant/ensure` | Upstream AG-BE-ID-002 is merged; send required idempotency/request headers. |
| Display no-authority policy facts | `ServantProfile` returned by ensure route | Safety context only; no broker/capital/RuntimeBinding controls. |

### Still blocked

| Frontend action | Blocker |
|---|---|
| Create interactive servant session | No approved public type field or derivation rule. |
| Create trainer servant session | Same type-contract blocker. |
| Create research-task servant session | No approved research-task OpenClaw mapping. |
| Send servant session message | BFF runtime implementation is absent. |
| Terminate servant session | BFF runtime implementation is absent. |
| Stream servant session events | BFF runtime implementation is absent; legacy stream is not servant-session proof. |
| Show accepted OpenClaw degraded state | `OPENCLAW_UPSTREAM_DEGRADED` not present in checked BFF runtime paths. |
| Claim strict frontend compatibility | execute-plans PR `#63` remains open with a failed integration gate. |

## 8. Operator Journey

### Current honest journey

```text
Operator opens the approved Agora entry
  -> frontend verifies Agora-scoped auth/audience
  -> frontend calls GET /bff/agora/me through a strict identity client
  -> frontend calls GET /bff/agora/capabilities through a strict identity client
  -> frontend calls POST /bff/agora/servant/ensure with Idempotency-Key
     and X-Request-Id
  -> BFF returns current servant profile readiness and no-authority policy facts
  -> servant-session create/message/stream/terminate controls stay disabled
     because AG-BE-ID-003 is blocked
```

### Future session journey, still blocked

```text
Claude approves how create carries or derives session type
  -> parent implements /bff/agora/servant/sessions* without overwriting
     AG-BE-ID-002 ensure behavior
  -> BFF maps interactive/trainer/research_task to approved OpenClaw kinds
  -> BFF writes trace_id/request_id/actor_id/user_id/persona_id/session_id
  -> BFF emits session-scoped SSE events and terminal events
  -> BFF surfaces OPENCLAW_UPSTREAM_DEGRADED on upstream degradation
  -> frontend enables strict session clients only after runtime evidence lands
```

## 9. Parent Absorption Gates

| Gate | Required parent decision or implementation |
|---|---|
| P0 upstream servant | Compose with merged `POST /bff/agora/servant/ensure` from AG-BE-ID-002. |
| P1 type contract | Record how create carries or derives `interactive`, `trainer`, and `research_task`. |
| P2 OpenAPI/schema alignment | Update or explicitly approve the public create contract; do not accept undeclared top-level fields. |
| P3 metadata rule | If using `metadata.session_type`, record it as an approved public contract rule, not an inferred workaround. |
| P4 research mapping | Name the OpenClaw skill/session kind that owns `research_task`. |
| P5 package placement | Add servant-session logic without overwriting AG-BE-ID-002 ensure behavior. |
| P6 audit fields | Include `trace_id`, `request_id`, `actor_id`, `user_id`, `persona_id`, and `session_id`. |
| P7 degradation code | Preserve `OPENCLAW_UPSTREAM_DEGRADED` or record reviewer approval for a precise equivalent. |
| P8 SSE scope | Implement servant session stream scoped by `session_id`. |
| P9 legacy route policy | State whether legacy `/bff/agora/sessions` remains compatibility-only, becomes an alias, or is out of scope. |
| P10 frontend dependency | Do not unblock AG-FE-ID-001 session controls until AG-BE-ID-003 lands runtime/session contract evidence. |
| P11 execute-plans follow-through | Account for PR `#63` remaining open/failed before making deployment compatibility claims. |
| P12 workshop boundary | Workshop session routes (`/bff/agora/strategy_workshop/sessions`) landed in AG-BE-SW-001; do not conflate with servant sessions in AG-BE-ID-003. |
| P13 tests | Cover create for all approved types, invalid/missing type handling, message post, terminate, stream, audit meta, idempotency, and degradation. |

## 10. Verification Performed

Commands run while preparing this packet:

```bash
git branch --show-current
git status -sb
git fetch origin
AI_NAME=Claude PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-15
AI_NAME=Claude PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-BE-ID-003
AI_NAME=Claude PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14
AI_NAME=Claude PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-BE-SW-001
AI_NAME=Claude PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-28
AI_NAME=Claude PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-FE-ID-001
AI_NAME=Claude PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-17
AI_NAME=Claude PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-XR-003
git log --oneline 0d872d414517a2adc292553791beff02ff31731f..origin/dev
git diff --name-status 0d872d414517a2adc292553791beff02ff31731f..origin/dev -- .orchestrator/task-briefs support/sidecars/AG-BE-ID-003 support/sidecars/AG-FE-ID-001 services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora ai-task-archive/tasks
git diff 0d872d414517a2adc292553791beff02ff31731f..origin/dev -- services/control-plane/bff/main.py
rg -n "servant/sessions|/bff/agora/servant/sessions|OPENCLAW_UPSTREAM_DEGRADED|ServantSession" services/control-plane/bff
grep -A 20 "ServantSessionCreateRequest" services/control-plane/openapi/agora_v1_1.openapi.yaml
grep -A 20 "ServantSessionCreateRequest" services/control-plane/openapi/agora_v1_2.openapi.yaml
gh pr view 63 --repo ajoe734/execute-plans --json number,state,mergeStateStatus,headRefOid,statusCheckRollup,updatedAt,url,title,baseRefName,headRefName
git -C /home/lupin/code/execute-plans fetch origin --prune
git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/main -- src/agora/AgoraApp.tsx src/lib/bff-v1/agora/identity.ts src/lib/bff-v1/agora/servant.ts src/lib/bff-v1/agora/types.ts src/entries/agora-main.tsx vite.agora.config.ts agora.html src/agora/pages/AskPersonas.tsx src/lib/bff/agora.ts
git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/dev -- src/agora/AgoraApp.tsx src/lib/bff-v1/agora/identity.ts src/lib/bff-v1/agora/servant.ts src/lib/bff-v1/agora/types.ts src/entries/agora-main.tsx vite.agora.config.ts agora.html src/agora/pages/AskPersonas.tsx src/lib/bff/agora.ts
```

Results:

- Current branch is `task/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-15`.
  Packet initially prepared against `origin/dev` at `eb7e9ee0`; updated before
  handoff after `git fetch origin` confirmed dev advanced to
  `7bab8c5d5289f33ae427fa0bbba293ddb6495ac2` via PR `#2015`
  (MGMT-LIVE-EVIDENCE-PREFLIGHT-DIAG) and PR `#2016`
  (AGENT-USABILITY-OPENCLAW iterative OODA proof). Checked pathset diff
  `eb7e9ee0..origin/dev` confirmed no servant-session or AG-BE-ID-003 surface
  changes in either new commit.
- Before packet edits, `git status -sb` showed only the generated task brief as
  untracked.
- Parent `AG-BE-ID-003` remains active `blocked`, waiting for `Claude`.
- Followup-14 is archived `done`; closeout PR `#2011` merged at `0d872d41`.
- Post-followup-14 dev delta contains only AG-BE-SW-001 workshop persistence
  routes and the nl/ask grace constant change; no servant-session or AG-BE-ID-003
  support path delta.
- AG-BE-SW-001 workshop routes are confined to `strategy_workshop/` package;
  no overlap with servant sessions.
- Targeted BFF runtime grep for servant-session route family and
  `OPENCLAW_UPSTREAM_DEGRADED` returned no matches.
- v1.1 and v1.2 `ServantSessionCreateRequest` still have no public type field.
- execute-plans PR `#63` remains `OPEN`, `UNSTABLE`, with failed
  `integration-gate`.
- Execute-plans remote probes show `AgoraApp.tsx`, `identity.ts`, `servant.ts`,
  Agora entry/config/html files still absent from both checked remote trees.

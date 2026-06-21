# AG-BE-ID-003 Sidecar BFF and Frontend Handoff Packet - Followup 14

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` |
| Helper parent | `AG-BE-ID-003` - Interactive/trainer/research session BFF facade |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Date | `2026-06-21` |
| Status | `in_progress; packet prepared for review` |
| Current dev base | `38341b12abff963e652bc1d15f7ea6ae0489c743` |
| Previous sidecar closeout | Followup-13 archived `done`; closeout PR `#2001` merged at `b4a74e92fd4ad270c6933ad81fbf4698ce96243a`; final task branch HEAD `5a84eea2d03701a401db2c48e1d1881c19d5cd17` |
| New relevant dev delta | AG-FE-ID-001 followup-26 review/closeout and AG-FE-DB-002 followup-16/17 support/review/closeout material only |
| Execute-plans compatibility PR | `#63` remains `OPEN` / `UNSTABLE`; `integration-gate` failed |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI/source-of-truth contract semantics, BFF runtime code,
route registries, governance policy, database migrations, OpenClaw adapter
code, compatibility manifest source, or execute-plans source files.

## 1. Purpose

This followup refreshes the `AG-BE-ID-003` BFF/frontend handoff after
followup-13 was reviewed, closed, and archived.

The material conclusion is unchanged: parent `AG-BE-ID-003` remains blocked,
waiting for `Claude` to decide how the servant-session create contract carries
or derives `interactive`, `trainer`, and `research_task`.

The fresh post-followup-13 facts are:

1. `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13` is archived `done`; its
   support-only closeout PR `#2001` merged at `b4a74e92`.
2. `origin/dev` advanced to `38341b12` through AG-FE-ID-001 followup-26
   review/closeout and AG-FE-DB-002 followup-16/17 support material.
3. The checked dev delta after `b4a74e92` contains only sidecar task brief,
   review, and closeout support documents. It does not add or change Agora BFF
   runtime, OpenAPI, specs, canonical contract files, AG-BE-ID-003 support
   paths, or execute-plans source.
4. Parent `AG-BE-ID-003` is still active `blocked`, owner `Codex2`, reviewer
   `Claude`, waiting for `Claude`.
5. Execute-plans PR `#63` is still open and unstable, with the
   `integration-gate` check failed.

This packet does not approve, reopen, or implement parent `AG-BE-ID-003`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex` and read the central status root via
`PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` | active `in_progress`; owner `Codex`, reviewer `Codex2` | This packet is the support-only artifact for review. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13` | archived `done`; PR `#1996` packet, PR `#1998` review, PR `#2001` closeout | Previous packet and review are durable; it kept the parent blocked. |
| `AG-BE-ID-003` | active `blocked`; owner `Codex2`, reviewer `Claude`, waiting for `Claude` | Parent implementation must not proceed until the servant-session type-contract decision is recorded. |
| `AG-BE-ID-002` | archived `done` | `/bff/agora/servant/ensure` remains the accepted upstream servant ensure/provision/reconcile surface. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-26` | archived `done`; closeout PR `#2003` merged at `4192ba9c` | Latest FE sidecar still keeps session controls gated on blocked `AG-BE-ID-003`. |
| `AG-FE-ID-001` | active `todo`; depends on `AG-FE-000` and `AG-BE-ID-003` | Frontend parent implementation has not started in durable task state. |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-16` | archived `done`; closeout PR `#2002` merged at `0e95a754` | Dashboard editor support context only; no servant-session implication. |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-17` | active `in_progress`; packet PR `#2004` merged at `38341b12` | Dashboard editor support refresh only; no servant-session implication. |
| `AG-XR-003` | archived `done` | Pantheon-side compatibility manifest/gate task is closed; execute-plans PR `#63` remains separate frontend follow-through risk. |

Dependency honesty rule: frontend and sidecar work may use identity,
capability, and servant-profile readiness as limited support context, but it
must not claim interactive, trainer, or research-task servant-session readiness
while `AG-BE-ID-003` is blocked.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_14.md` | This task-scoped assignment and support-only boundary. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` | Confirms active `in_progress` owner/reviewer, artifact path, dependency, and support-only acceptance. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms parent remains blocked on the servant-session type-contract decision. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13` | Confirms predecessor archived `done`, with closeout PR `#2001` merged. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-26` | Confirms latest FE support packet is archived `done` and still gates session UI on AG-BE-ID-003. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms FE parent remains `todo` and depends on blocked `AG-BE-ID-003`. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-16` | Confirms DB002 followup-16 is archived `done` and support-only. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-17` | Confirms DB002 followup-17 is active `in_progress` and support-only. |
| `git log --oneline b4a74e92fd4ad270c6933ad81fbf4698ce96243a..origin/dev` | Shows FE followup-26 and DB002 followup-16 support/review/closeout activity after followup-13 closeout. |
| `git diff --name-status b4a74e92fd4ad270c6933ad81fbf4698ce96243a..origin/dev -- <checked pathset>` | Shows only sidecar task brief/review/closeout material; no BFF/OpenAPI/spec/contract runtime delta. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` and `agora_v1_2.openapi.yaml` | `ServantSessionCreateRequest` still lacks a public type field and rejects undeclared top-level fields. |
| `services/control-plane/bff/agora/router.py` | Runtime still exposes `/bff/agora/me` and `/bff/agora/capabilities` as identity/capability support routes. |
| `services/control-plane/bff/agora/servant/router.py` | Runtime still owns `/bff/agora/servant/ensure`; no servant-session route family is implemented there. |
| `services/control-plane/bff/main.py` | Legacy `/bff/agora/sessions*` and OpenClaw ops routes remain separate from the servant-session facade. |
| `rg -n "servant/sessions\|/bff/agora/servant/sessions\|OPENCLAW_UPSTREAM_DEGRADED\|ServantSession" services/control-plane/bff` | No matches; no runtime servant-session route family or accepted degraded code found in checked BFF paths. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-26.md` and review record | Latest FE support still says identity/servant readiness is safe context, but sessions remain blocked. |
| `support/sidecars/AG-FE-DB-002/AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-16.md` | DB followup-16 support is dashboard-editor context only and does not affect AG-BE-ID-003. |
| `support/sidecars/AG-FE-DB-002/AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-17.md` | DB followup-17 support is a later dashboard-editor blocker refresh and does not affect AG-BE-ID-003. |
| `gh pr view 63 --repo ajoe734/execute-plans` | Confirms PR `#63` remains `OPEN`, `UNSTABLE`, and failed `integration-gate`. |
| `/home/lupin/code/execute-plans` remote tree probes | Confirm target Agora shell/client files remain absent from checked remotes except the previously noted `types.ts` on `origin/dev`, `AskPersonas.tsx`, and `src/lib/bff/agora.ts`. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup 13

Baseline: followup-13 closeout PR `#2001` merged at
`b4a74e92fd4ad270c6933ad81fbf4698ce96243a`.

| Change | What changed | Parent implication |
|---|---|---|
| Followup-13 closed | Archived `done`; closeout says parent remains blocked on Claude's servant-session type-contract decision. | Treat followup-13 as accepted support evidence. |
| FE followup-26 closed | PR `#2003` merged at `4192ba9c`; packet and closeout preserve the frontend session gate. | Reinforces AG-FE-ID-001 must not enable session controls while AG-BE-ID-003 is blocked. |
| DB002 followup-16 closed | PR `#2002` merged review/closeout support records. | Dashboard layout/editor context only; no backend session implication. |
| DB002 followup-17 packet landed | PR `#2004` merged a support-only DB002 acceptance refresh; central status still shows active `in_progress`. | Dashboard layout/editor context only; no backend session implication. |
| Checked BFF/OpenAPI/spec paths | No changed files in the post-followup-13 checked pathset. | No new evidence unblocks AG-BE-ID-003. |
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

| Surface | Current observation at dev `38341b12` | Readiness impact |
|---|---|---|
| v1.1/v1.2 OpenAPI servant-session routes | Paper routes exist for create/message/terminate/stream. | Contract family exists on paper, but create is underspecified for type. |
| `ServantSessionCreateRequest` | No public type field; top-level unknown properties rejected. | Blocks strict create clients and parent implementation review. |
| Runtime servant router | Implements `/bff/agora/servant/ensure` only. | Compose with AG-BE-ID-002; do not overwrite ensure behavior. |
| Runtime servant sessions | No checked BFF match for `/bff/agora/servant/sessions*`. | Parent must still implement after contract decision. |
| Legacy `/bff/agora/sessions*` | Existing route in `main.py` creates legacy ask/session records and accepts `mode` or `sessionType`. | Not a safe substitute for servant sessions. |
| OpenClaw ops session create | Requires `agent_id` and `session_type` at `/api/v1/operator/openclaw/sessions`. | Useful lower-level context only; not the Agora servant-session public contract. |
| Degraded error | `OPENCLAW_UPSTREAM_DEGRADED` not found in checked BFF runtime paths. | Parent must add this exact code or record reviewer approval for a precise equivalent. |
| Frontend parent state | `AG-FE-ID-001` remains `todo`; latest FE sidecar says session controls stay disabled. | No frontend runtime readiness to absorb. |

## 7. Frontend Handoff

Until the parent records the type-contract decision and lands the runtime route
family, execute-plans should keep servant-session create/message/stream/terminate
controls disabled in strict live mode.

Remote probe source: `/home/lupin/code/execute-plans` after `git fetch origin
--prune`. The local checkout remains `main...origin/main [ahead 2, behind 467]`,
so implementation truth should come from remote trees or a clean task worktree.

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
| P12 tests | Cover create for all approved types, invalid/missing type handling, message post, terminate, stream, audit meta, idempotency, and degradation. |

## 10. Verification Performed

Commands run while preparing this packet:

```bash
git status -sb
git branch --show-current
git remote -v
git fetch origin
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-26
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-16
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-17
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-XR-003
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh progress AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14 "Preparing support-only followup-14 packet from post-followup-13 dev delta; parent remains blocked on Claude servant-session type-contract decision."
git log --oneline b4a74e92fd4ad270c6933ad81fbf4698ce96243a..origin/dev
git diff --name-status b4a74e92fd4ad270c6933ad81fbf4698ce96243a..origin/dev -- .orchestrator/task-briefs support/sidecars/AG-BE-ID-003 support/sidecars/AG-FE-ID-001 support/sidecars/AG-FE-DB-002 services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora ai-task-archive/tasks
rg -n "servant/sessions|/bff/agora/servant/sessions|OPENCLAW_UPSTREAM_DEGRADED|ServantSession" services/control-plane/bff
rg -n "ServantSessionCreateRequest|session_type|sessionType|session_kind|research_task" services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/openapi/agora_v1_2.openapi.yaml
gh pr view 63 --repo ajoe734/execute-plans --json number,state,mergeStateStatus,headRefOid,statusCheckRollup,updatedAt,url,title,baseRefName,headRefName
git -C /home/lupin/code/execute-plans fetch origin --prune
git -C /home/lupin/code/execute-plans status -sb
git -C /home/lupin/code/execute-plans rev-parse origin/main
git -C /home/lupin/code/execute-plans rev-parse origin/dev
git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/main -- src/agora/AgoraApp.tsx src/lib/bff-v1/agora/identity.ts src/lib/bff-v1/agora/servant.ts src/lib/bff-v1/agora/types.ts src/entries/agora-main.tsx vite.agora.config.ts agora.html src/agora/pages/AskPersonas.tsx src/lib/bff/agora.ts
git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/dev -- src/agora/AgoraApp.tsx src/lib/bff-v1/agora/identity.ts src/lib/bff-v1/agora/servant.ts src/lib/bff-v1/agora/types.ts src/entries/agora-main.tsx vite.agora.config.ts agora.html src/agora/pages/AskPersonas.tsx src/lib/bff/agora.ts
```

Results:

- Current branch is `task/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14`,
  refreshed with `origin/dev` `38341b12abff963e652bc1d15f7ea6ae0489c743`.
- Before packet edits, `git status -sb` showed only the generated task brief as
  untracked.
- Parent `AG-BE-ID-003` remains active `blocked`, waiting for `Claude`.
- Followup-13 is archived `done`; closeout PR `#2001` merged at `b4a74e92`.
- Post-followup-13 dev delta is sidecar support material only: FE followup-26
  and DB002 followup-16/17 support/review/closeout records.
- Targeted BFF runtime grep for servant-session route family and
  `OPENCLAW_UPSTREAM_DEGRADED` returned no matches.
- v1.1 and v1.2 `ServantSessionCreateRequest` still have no public type field.
- execute-plans PR `#63` remains `OPEN`, `UNSTABLE`, with failed
  `integration-gate`.
- Execute-plans remote probes show `AgoraApp.tsx`, `identity.ts`, `servant.ts`,
  Agora entry/config/html files still absent from both checked remote trees.

Final local validation before handoff:

```bash
git diff --check -- .orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_14.md support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14.md
rg -n "^(TBD|TODO|PLACEHOLDER|FIXME)$" .orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_14.md support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14.md
python3 scripts/agora_schema_bundle.py --verify
python3 -m pytest scripts/test_agora_v1_2_bundle.py -q
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py -q
```

- `git diff --check` passed.
- placeholder scan returned no matches.
- `python3 scripts/agora_schema_bundle.py --verify` passed.
- `python3 -m pytest scripts/test_agora_v1_2_bundle.py -q`: 5 passed.
- `python3 -m pytest services/control-plane/bff/tests/test_agora_router.py -q`:
  18 passed.

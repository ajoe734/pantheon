# AG-BE-ID-003 Sidecar BFF and Frontend Handoff Packet - Followup 13

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13` |
| Helper parent | `AG-BE-ID-003` - Interactive/trainer/research session BFF facade |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Sidecar owner / reviewer | `Codex2` / `Codex` |
| Date | `2026-06-21` |
| Status | `in_progress; packet prepared for Codex review` |
| Current dev base | `0f88261fbd18106589e95600412924380681f02a` |
| Previous sidecar closeout | Followup-12 archived `done`; closeout PR `#1980` merged at `ff92e8cb32bf1601920ea58afec1f1abb0ba24b1`; final task commit `dfa816cc275df2a69da5d801d40d0950fad4f5ae` |
| New relevant dev delta | Additive Agora v1.2 bundle closed; AG-FE-ID-001 followups 23-25 closed; execute-plans PR `#63` remains open with failed `integration-gate`; BFF runtime delta is unrelated management `nl/ask` async I/O |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI/source-of-truth contract semantics, BFF runtime code,
route registries, governance policy, database migrations, OpenClaw adapter
code, compatibility manifest source, or execute-plans source files.

## 1. Purpose

This followup refreshes the `AG-BE-ID-003` BFF/frontend handoff after
followup-12 closed and after `dev` advanced through v1.2 contract-bundle and
frontend support work.

The main conclusion is unchanged: parent `AG-BE-ID-003` remains blocked waiting
for `Claude` to decide how `ServantSessionCreateRequest` carries or derives
`interactive`, `trainer`, and `research_task`.

The material updates are:

1. `AG-XR-OPENAPI-002` is archived `done` and adds an additive Agora v1.2
   bundle. That bundle keeps the servant-session route family on paper, but it
   still does not add a public `session_type`/`sessionType`/`session_kind`
   field to `ServantSessionCreateRequest`.
2. `AG-XR-OPENAPI-002-SIDECAR-ACCEPTANCE` is archived `done`; it accepts the
   v1.2 bundle as support context, not BFF runtime readiness.
3. `AG-FE-ID-001` support followups 23, 24, and 25 are archived `done`. They
   keep the frontend session controls gated on blocked `AG-BE-ID-003`.
4. `AG-XR-003` is archived `done` in central status, but execute-plans PR `#63`
   remains `OPEN` with a failed `integration-gate`, so cross-repo frontend
   follow-through still has live risk outside the Pantheon-side done record.
5. The only BFF runtime delta in the checked range is management
   `POST /bff/management/nl/ask` read-surface I/O moving off the event loop.
   It does not implement Agora servant sessions.

This packet does not approve, reopen, or implement parent `AG-BE-ID-003`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex2` and read the central status root via
`PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13` | active `in_progress`; owner `Codex2`, reviewer `Codex` | This packet is the support-only deliverable for review. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12` | archived `done`; packet PR `#1964`, closeout PR `#1980` | Previous packet is durable and already recorded post-handoff status drift. |
| `AG-BE-ID-003` | `blocked`; owner `Codex2`, reviewer `Claude`, waiting for `Claude` | Parent implementation must not proceed until the servant-session type contract is decided. |
| `AG-BE-ID-002` | archived `done` | `/bff/agora/servant/ensure` remains the accepted upstream servant ensure/provision/reconcile surface. |
| `AG-XR-003` | archived `done` | Pantheon compatibility manifest/gate task closed; execute-plans PR `#63` remains separate frontend follow-through evidence. |
| `AG-XR-OPENAPI-002` | archived `done`; PR `#1983` implementation and PR `#1985` closeout merged | Additive Agora v1.2 bundle is accepted support context, not AG-BE-ID-003 runtime readiness. |
| `AG-XR-OPENAPI-002-SIDECAR-ACCEPTANCE` | archived `done`; PR `#1989` merged | Support-only acceptance packet; no backend session unlock. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-25` | archived `done`; packet PR `#1993`, review PR `#1994` | Latest frontend support packet keeps session UI gated on AG-BE-ID-003 and PR `#63` risk. |
| `AG-FE-ID-001` | `todo`; depends on `AG-FE-000` and `AG-BE-ID-003` | Frontend parent implementation has not started in durable task state. |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-15` | `review_approved` | New support packet is dashboard layout DB context; no servant-session implication. |

Dependency honesty rule: frontend work may use identity, capability, and
servant-profile readiness as support context, but it must not claim
interactive, trainer, or research-task session readiness while `AG-BE-ID-003`
is blocked.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_13.md` | This task-scoped assignment and support-only boundary. |
| `AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13` | Confirms active sidecar state, owner/reviewer, artifact path, and support-only acceptance. |
| `AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms parent remains blocked on the servant-session type-contract decision. |
| `AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12` | Confirms predecessor archived `done` and records packet/closeout PR merge evidence. |
| `AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-XR-003` | Confirms Pantheon-side compatibility manifest/gate task is archived `done`. |
| `AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-XR-OPENAPI-002` | Confirms additive v1.2 bundle is archived `done`. |
| `AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-XR-OPENAPI-002-SIDECAR-ACCEPTANCE` | Confirms v1.2 support acceptance packet is archived `done`. |
| `AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-25` | Confirms latest frontend support packet is archived `done` after PR `#1994`. |
| `AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms frontend parent remains `todo` and depends on blocked `AG-BE-ID-003`. |
| `git log --oneline ff92e8cb32bf1601920ea58afec1f1abb0ba24b1..origin/dev` | Shows dev advanced through AG-DES-SW-PRIV, AG-XR-OPENAPI-002, AG-FE-ID-001 followups 23-25, AG-FE-DB-002 followup-15, and unrelated management BFF/support work. |
| `git diff --name-status ff92e8cb32bf1601920ea58afec1f1abb0ba24b1..origin/dev -- ...` | Shows no AG-BE-ID-003 support-path change and no v1.1 frozen bundle change; relevant contract delta is additive v1.2. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | `ServantSessionCreateRequest` still lacks a public session type field. |
| `services/control-plane/openapi/agora_v1_2.openapi.yaml` | v1.2 repeats the same create request shape; additive bundle does not resolve the type-contract blocker. |
| `services/control-plane/bff/main.py` | Diff since followup-12 closeout is management `nl/ask` async I/O only. |
| `services/control-plane/bff/agora/servant/router.py` | Runtime servant router still owns `/servant/ensure`; checked runtime paths had no servant-session implementation. |
| `rg -n "servant/sessions\|/bff/agora/servant/sessions\|OPENCLAW_UPSTREAM_DEGRADED\|ServantSession" services/control-plane/bff` | No matches; no runtime servant-session route or accepted degraded code was found in BFF runtime paths. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-25.md` | Latest FE sidecar keeps AG-BE-ID-003 as the session gate and records execute-plans PR `#63` risk. |
| `gh pr view 63 --repo ajoe734/execute-plans` | Confirms PR `#63` remains `OPEN`; `integration-gate` failed. |
| `/home/lupin/code/execute-plans` remote tree probes | Confirm parent target files remain absent from checked remote trees except `types.ts` on `origin/dev`, `AskPersonas.tsx`, and `src/lib/bff/agora.ts`. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup 12

Baseline: followup-12 closeout PR `#1980` merged at
`ff92e8cb32bf1601920ea58afec1f1abb0ba24b1`. Current `origin/dev` is
`0f88261fbd18106589e95600412924380681f02a`.

| Change | What changed | Parent implication |
|---|---|---|
| Followup-12 closed | Archived `done`; packet PR `#1964` and closeout PR `#1980` are durable. | Treat followup-12 as accepted support evidence. |
| AG-XR-OPENAPI-002 closed | Additive v1.2 OpenAPI, capability manifest, and bundle index landed and were accepted. | Useful contract context, but not a BFF runtime implementation and not a servant-session type decision. |
| v1.1 frozen surface unchanged | Diff from followup-12 closeout shows no changes to `agora_v1_1.openapi.yaml` or v1.1 bundle files. | Existing v1.1 blocker remains valid. |
| v1.2 create schema checked | `ServantSessionCreateRequest` in v1.2 still has only `intent`, `strategy_ref`, and `metadata`; `additionalProperties: false`. | Strict clients still cannot send undeclared top-level `session_type`, `sessionType`, or `session_kind`. |
| BFF runtime changed outside Agora | `main.py` moved management `nl/ask` blocking read-store calls into worker threads. | Operationally useful but unrelated to AG-BE-ID-003 servant sessions. |
| No runtime servant-session match | Targeted BFF grep found no `servant/sessions`, `ServantSession`, or `OPENCLAW_UPSTREAM_DEGRADED` matches. | Parent still needs route handlers and degradation semantics after reviewer decision. |
| AG-FE-ID-001 sidecars 23-25 closed | Latest frontend support packets are durable; followup-25 review PR `#1994` merged at current dev tip. | Frontend support context is fresher, but it continues to gate session controls on AG-BE-ID-003. |
| AG-FE-ID-001 parent unchanged | Parent remains `todo`; `AG-FE-000` is done, but `AG-BE-ID-003` remains blocked. | No frontend implementation evidence exists to absorb. |
| AG-XR-003 status is done | Central status archives Pantheon-side manifest/gate task as `done`. | Does not erase execute-plans PR `#63` failed check; frontend compatibility follow-through remains separate risk. |
| Execute-plans PR #63 still open | PR `#63` state is `OPEN`; `integration-gate` failed. | Strict frontend deployment/readiness claims must stay conservative. |
| AG-FE-DB-002 support landed | Followup-15 support packet merged and is `review_approved`. | Dashboard DB layout context only; no AG-BE-ID-003 session implication. |

## 5. Contract Decision Request

### D1 - Public create schema still has no type field

Both v1.1 and v1.2 define `ServantSessionCreateRequest` as:

```yaml
properties:
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
top-level field exists. Because top-level additional properties are rejected,
strict clients cannot safely send a hidden type field.

### D2 - Additive v1.2 does not unblock AG-BE-ID-003

The v1.2 bundle is workshop/private-content/persistence oriented and preserves
the servant-session route family on paper. It does not define how
`interactive`, `trainer`, or `research_task` reaches BFF runtime or OpenClaw.

### D3 - Runtime route family is still absent

Checked BFF runtime paths do not implement
`/bff/agora/servant/sessions*`, do not expose a `ServantSession` runtime type,
and do not surface `OPENCLAW_UPSTREAM_DEGRADED`.

### D4 - Research-task mapping remains unresolved

The parent still needs a reviewer-approved OpenClaw skill/session kind for
`research_task`. Existing trainer and OpenClaw ops surfaces are not sufficient
to infer the missing mapping.

## 6. Decision Options For Parent Reviewer

| Option | Effect | Sidecar view |
|---|---|---|
| Add explicit public `session_type` to `ServantSessionCreateRequest` | Clients can send `interactive`, `trainer`, or `research_task`; OpenAPI can validate. | Preferred because it is least ambiguous. |
| Add explicit equivalent such as `session_kind` | Same result if field semantics and enum are documented. | Acceptable if reviewer names the field and mapping. |
| Derive type server-side from route/action/context | Public schema stays unchanged. | Acceptable only with a deterministic, documented derivation rule. |
| Use `metadata.session_type` | Schema technically permits it. | Hidden contract unless reviewer explicitly promotes it. |
| Default all creates to one type | Fast to code. | Should stay blocked; it fails parent acceptance for three visible types. |

## 7. Current Route Evidence

| Surface | Current observation at dev `0f88261f` | Readiness impact |
|---|---|---|
| v1.1 OpenAPI | Defines `/bff/agora/servant/sessions*`; create body has no public session type field. | Paper route family exists, but strict create is underspecified. |
| v1.2 OpenAPI | Additive bundle keeps `/bff/agora/servant/sessions*`; create body remains unchanged. | v1.2 does not resolve AG-BE-ID-003. |
| v1.2 capability/bundle | Accepted for workshop/private-content/persistence and exact-byte inheritance. | Downstream support context, not runtime proof. |
| Runtime servant router | Implements `/bff/agora/servant/ensure` only in checked runtime paths. | Compose with AG-BE-ID-002; do not overwrite ensure behavior. |
| Runtime servant sessions | No `servant/sessions` runtime match in checked BFF paths. | Parent must still implement after contract decision. |
| Legacy `/bff/agora/sessions` | Existing legacy routes in `main.py` are not the servant-session facade. | Not proof of interactive/trainer/research-task readiness. |
| Legacy SSE | Earlier packets found it delegates to ask-channel behavior; no new runtime delta changed that path. | Not proof of servant session-scoped SSE. |
| Degradation code | `OPENCLAW_UPSTREAM_DEGRADED` not found in checked BFF runtime paths. | Parent must add or get reviewer approval for exact equivalent. |
| Frontend state | `AG-FE-ID-001` is `todo`; latest FE sidecar is done but keeps sessions gated. | No frontend session UI readiness. |

## 8. Frontend Handoff

Until the parent records the type-contract decision and lands the runtime route
family, execute-plans should keep servant-session create/message/stream/terminate
controls disabled in strict live mode.

Remote probe source: `/home/lupin/code/execute-plans` without changing that
worktree. Current local worktree remains `main...origin/main [ahead 2, behind
467]`, so implementation should use remote tree checks or a clean task
worktree.

| Surface | Current remote-tree state | Handoff rule |
|---|---|---|
| `src/agora/AgoraApp.tsx` | Missing from both `origin/main` and `origin/dev`. | Parent FE work still needs an approved shell file or explicit blocker. |
| `src/lib/bff-v1/agora/identity.ts` | Missing from both checked remote trees. | Parent must add strict identity/capability client before claiming shell readiness. |
| `src/lib/bff-v1/agora/servant.ts` | Missing from both checked remote trees. | Parent must add strict servant ensure client before shell can show servant readiness. |
| `src/lib/bff-v1/agora/types.ts` | Present on `origin/dev`, missing from `origin/main`. | Generated types are branch-dependent and not runtime proof. |
| `src/agora/pages/AskPersonas.tsx` | Present on both checked remote trees. | Ask/session controls must remain gated by backend readiness. |
| `src/lib/bff/agora.ts` | Present on both checked remote trees. | Existing broad client is not enough for AG-FE-ID-001 strict v1 client acceptance. |
| execute-plans PR `#63` | `OPEN`; `integration-gate` failed. | Do not claim strict deployment compatibility from Pantheon-side status alone. |

### Safe now

| Frontend action | Surface | Caveat |
|---|---|---|
| Resolve operator Agora scope | `GET /bff/agora/me` | Identity scope only. |
| Display capability readiness | `GET /bff/agora/capabilities` or manifest context | Discovery/readiness only; not session runtime proof. |
| Show servant readiness after user action | `POST /bff/agora/servant/ensure` | Upstream AG-BE-ID-002 is merged; send required idempotency/request headers. |
| Use v1.2 bundle context | `AG-XR-OPENAPI-002` artifacts | Only for downstream workshop/private-content/contract planning; not AG-BE-ID-003 unlock. |

### Still blocked

| Frontend action | Blocker |
|---|---|
| Create interactive servant session | No approved public type field or derivation rule. |
| Create trainer servant session | Same type blocker. |
| Create research-task servant session | No approved research-task OpenClaw mapping. |
| Send servant session message | BFF runtime implementation is absent. |
| Terminate servant session | BFF runtime implementation is absent. |
| Stream servant session events | BFF runtime implementation is absent; legacy stream is not servant-session proof. |
| Show accepted OpenClaw degraded state | `OPENCLAW_UPSTREAM_DEGRADED` not present in checked BFF runtime paths. |
| Claim strict frontend compatibility | execute-plans PR `#63` remains open with a failed integration gate. |

## 9. Operator Journey

### Before parent decision

1. Operator resolves Agora identity through `GET /bff/agora/me`.
2. Operator checks capabilities through `GET /bff/agora/capabilities`.
3. Operator ensures servant readiness through `POST /bff/agora/servant/ensure`.
4. UI may show servant status and no-authority policy facts.
5. Servant session create/message/stream/terminate controls stay disabled or
   read-only with a backend-contract-unavailable state.

### After parent implementation

1. Parent records the approved representation of `interactive`, `trainer`, and
   `research_task`.
2. Parent implements `/bff/agora/servant/sessions*` without overwriting
   AG-BE-ID-002 ensure behavior.
3. BFF stores audit fields: `trace_id`, `request_id`, `actor_id`, `user_id`,
   `persona_id`, and `session_id`.
4. BFF maps each session type to an approved OpenClaw skill/session kind.
5. BFF emits session-scoped SSE events and terminal events.
6. BFF surfaces `OPENCLAW_UPSTREAM_DEGRADED` or an explicitly approved
   equivalent envelope on upstream degradation.
7. Frontend enables strict session clients only after runtime and contract
   evidence lands.

## 10. Parent Absorption Gates

| Gate | Required parent decision or implementation |
|---|---|
| P0 upstream servant | Compose with merged `POST /bff/agora/servant/ensure` from AG-BE-ID-002. |
| P1 type contract | Record how create carries or derives `interactive`, `trainer`, and `research_task`. |
| P2 OpenAPI/schema alignment | Update or explicitly approve the public create contract; do not accept undeclared top-level fields. |
| P3 v1.2 boundary | Treat v1.2 workshop/private-content bundle as additive context, not a session unblocker. |
| P4 research mapping | Name the OpenClaw skill/session kind that owns `research_task`. |
| P5 package placement | Add servant-session logic without overwriting AG-BE-ID-002 ensure behavior. |
| P6 audit fields | Include `trace_id`, `request_id`, `actor_id`, `user_id`, `persona_id`, and `session_id`. |
| P7 degradation code | Preserve `OPENCLAW_UPSTREAM_DEGRADED` or record reviewer approval for a precise equivalent. |
| P8 SSE scope | Implement servant session stream scoped by `session_id`. |
| P9 legacy route policy | State whether legacy `/bff/agora/sessions` remains compatibility-only, becomes an alias, or is out of scope. |
| P10 frontend dependency | Do not unblock AG-FE-ID-001 session controls until AG-BE-ID-003 lands runtime/session contract and compatibility disposition. |
| P11 execute-plans follow-through | Account for PR `#63` remaining open/failed before making deployment compatibility claims. |
| P12 tests | Cover create for all approved types, invalid/missing type handling, message post, terminate, stream, audit meta, idempotency, and degradation. |

## 11. Verification Performed

Commands run while preparing this packet:

```bash
git status -sb
git branch --show-current
git remote -v
./scripts/git/task_start.sh "AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13"
AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13
AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12
AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-XR-003
AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-XR-OPENAPI-002
AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-XR-OPENAPI-002-SIDECAR-ACCEPTANCE
AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-25
AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Codex2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-000
git log --oneline ff92e8cb32bf1601920ea58afec1f1abb0ba24b1..origin/dev
git diff --name-status ff92e8cb32bf1601920ea58afec1f1abb0ba24b1..origin/dev -- services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora support/sidecars/AG-BE-ID-003 support/sidecars/AG-FE-ID-001 support/sidecars/AG-XR-OPENAPI-002
rg -n "servant/sessions|/bff/agora/servant/sessions|OPENCLAW_UPSTREAM_DEGRADED|ServantSession" services/control-plane/bff
gh pr view 63 --repo ajoe734/execute-plans --json number,state,mergedAt,mergeCommit,url,headRefName,baseRefName,title,statusCheckRollup
git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/main -- src/agora/AgoraApp.tsx src/lib/bff-v1/agora/identity.ts src/lib/bff-v1/agora/servant.ts src/lib/bff-v1/agora/types.ts src/agora/pages/AskPersonas.tsx src/lib/bff/agora.ts
git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/dev -- src/agora/AgoraApp.tsx src/lib/bff-v1/agora/identity.ts src/lib/bff-v1/agora/servant.ts src/lib/bff-v1/agora/types.ts src/agora/pages/AskPersonas.tsx src/lib/bff/agora.ts
```

Results:

- Current branch is `task/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13` at
  `origin/dev` `0f88261fbd18106589e95600412924380681f02a`.
- Parent `AG-BE-ID-003` remains `blocked`, waiting for `Claude`.
- `AG-XR-OPENAPI-002`, its sidecar acceptance packet, `AG-XR-003`, and
  AG-FE-ID-001 followup-25 are archived `done`.
- v1.1 frozen servant-session create schema remains unchanged; v1.2 repeats the
  same no-type create shape.
- Targeted BFF runtime grep returned no matches for servant-session routes or
  `OPENCLAW_UPSTREAM_DEGRADED`.
- execute-plans PR `#63` remains `OPEN` with failed `integration-gate`.

Final validation before commit:

```bash
env GIT_INDEX_FILE=/tmp/git-index-check-AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13 git read-tree HEAD
env GIT_INDEX_FILE=/tmp/git-index-check-AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13 git add .orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_13.md support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13.md
env GIT_INDEX_FILE=/tmp/git-index-check-AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13 git diff --cached --check -- .orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_13.md support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13.md
rg -n "^(TBD|TODO|PLACEHOLDER|FIXME)$" .orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_13.md support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13.md
python3 scripts/agora_schema_bundle.py --verify
python3 -m pytest scripts/test_agora_v1_2_bundle.py -q
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py -q
```

Result:

- private-index staged `git diff --cached --check` passed.
- placeholder scan returned no matches.
- `python3 scripts/agora_schema_bundle.py --verify` passed.
- `python3 -m pytest scripts/test_agora_v1_2_bundle.py -q`: 5 passed.
- `python3 -m pytest services/control-plane/bff/tests/test_agora_router.py -q`: 18 passed.

## 12. Reviewer Handoff

Codex review focus:

1. Confirm support-only scope is preserved.
2. Confirm the v1.2 bundle is treated as additive contract context, not a
   servant-session runtime/type-contract unblocker.
3. Confirm parent `AG-BE-ID-003` remains correctly blocked on the
   `ServantSessionCreateRequest` type decision.
4. Confirm frontend handoff remains conservative despite FE support followups
   closing and AG-XR-003 being archived `done`.
5. Confirm execute-plans PR `#63` risk and missing FE target files are not
   hidden by Pantheon-side done states.

Recommended reviewer disposition if accurate: approve this sidecar as
support-only handoff material and return to Codex2 for closeout.

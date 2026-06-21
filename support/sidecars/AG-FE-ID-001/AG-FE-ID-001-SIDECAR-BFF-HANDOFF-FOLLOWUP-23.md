# AG-FE-ID-001 Followup-23 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-23` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-21` |
| Status | `in_progress; packet ready for reviewer handoff after PR merge` |
| Current dev base | `994dce7df17cc71a65abd516d0371d871b44141a` |
| Previous AG-FE-ID-001 sidecar closeout merge | `a93a26b980757ca96ebd6d76979f2a8409495c67` |
| Previous AG-FE-ID-001 packet PR | `#1955` merged at `a2d16e4c2758c7efc8e75be6da3fbd063eab364d` |
| Previous AG-FE-ID-001 closeout PR | `#1977` merged at `a93a26b980757ca96ebd6d76979f2a8409495c67` |
| New AG-BE-ID-003 sidecar closeout | PR `#1980` merged at `ff92e8cb32bf1601920ea58afec1f1abb0ba24b1` |
| New AG-XR-003 sidecar closeout | PR `#1976` merged at `d63c0eb47275072f6ccceca8dd218f9ff5cb8d75` |
| New AG-XR-OPENAPI-002 implementation merge | PR `#1983` merged at `dffa0ee5a0f310e20ab423749441ec7e032fdbdb` |
| Latest unrelated dev refresh | `a9347b7a942e17da05ac13d31c74cf64cdf3feea` updated AG-DES-SW-PRIV sidecar review support text |
| Latest GitHub behind refresh | `fe6136c80b20ae57d525191db0120a845b62d2a7` added Management/release-gate upload-path test coverage outside this handoff scope |
| Latest BFF runtime refresh | `994dce7df17cc71a65abd516d0371d871b44141a` updated Management `nl/ask` async context/audit handling outside `/bff/agora/*` servant-session routes |
| Execute-plans refs checked | `origin/main` at `7b2f17c4dee8dcafe62c2295504df03aed0ae16e`; `origin/dev` at `7aa4917272212452fe5e4dc99bf2d76fe48eacfd` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI/source-of-truth contract semantics, BFF runtime code,
route registries, governance policy, database migrations, OpenClaw adapter
code, compatibility manifest source, or execute-plans source files.

## 1. Purpose

This followup refreshes the `AG-FE-ID-001` BFF/frontend handoff after
`AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-22` closed through PR `#1977`.

The material delta is narrow:

1. `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12` is now archived `done`, but
   parent `AG-BE-ID-003` remains `blocked` on the servant-session type-contract
   decision.
2. `AG-XR-003` and its followup-14 sidecar are now archived `done`; local
   manifest sanity is green, but execute-plans PR `#63` is still `OPEN` and
   `UNSTABLE`, and the committed manifest still has a placeholder frontend
   runtime commit.
3. New Strategy Workshop private-content/storage material plus the additive
   `AG-XR-OPENAPI-002` v1.2 OpenAPI/capability/bundle implementation landed
   after followup-22, but the visible delta is workshop/private-content/storage
   oriented and does not implement the `AG-FE-ID-001` shell or strict frontend
   clients.
4. The v1.2 OpenAPI still carries the same servant-session create shape as
   v1.1: `ServantSessionCreateRequest` has `intent`, `strategy_ref`, and open
   `metadata`, but no public `session_type`, `sessionType`, or `session_kind`.
5. The execute-plans remote trees checked for this packet still lack
   `src/agora/AgoraApp.tsx`, `src/lib/bff-v1/agora/identity.ts`, and
   `src/lib/bff-v1/agora/servant.ts`.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-23` | `in_progress`; owner `Codex`, reviewer `Claude` | This packet is the only intended deliverable. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-22` | archived `done`; packet PR `#1955`, closeout PR `#1977` merged | Previous support packet is durable and is the baseline for this refresh. |
| `AG-FE-ID-001` | `todo`; owner `Claude`, reviewer `Codex`; depends on `AG-FE-000` and `AG-BE-ID-003` | Parent implementation has not started in durable task state. |
| `AG-BE-ID-003` | `blocked`; owner `Codex2`, reviewer `Claude`, waiting for `Claude` | Servant-session create/message/stream/terminate UI remains blocked. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12` | archived `done`; PR `#1964`, closeout PR `#1980` merged | New dependency-side support packet reinforces the same type-contract blocker. |
| `AG-XR-003` | archived `done` | Pantheon-side manifest gate work is closed; deployment readiness still requires the separate execute-plans/runtime-pin follow-through recorded in review material. |
| `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14` | archived `done`; PR `#1976` merged | Latest support evidence says local sanity is green, while PR `#63` and frontend runtime pin remain open risks. |
| `AG-XR-OPENAPI-002` | active `review`; owner `Codex`, reviewer `Claude2`; PR `#1983` merged | Additive v1.2 OpenAPI/bundle work is awaiting review and is not parent AG-FE-ID-001 shell/client completion. |
| `AG-XR-OPENAPI-002-SIDECAR-REVIEW` | active `in_progress`; owner `Antigravity`, reviewer `Codex` | Separate review-support packet for AG-XR-OPENAPI-002; not an AG-FE-ID-001 artifact. |

Dependency honesty rule: `AG-FE-ID-001` may continue to use identity,
capability, and servant-profile readiness as support context, but it must not
claim interactive, trainer, or research-task session readiness while
`AG-BE-ID-003` is blocked.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_23.md` | This sidecar's support-only assignment. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-23` | Confirms active sidecar owner/reviewer, artifact path, and support-only acceptance. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent remains `todo` and depends on `AG-BE-ID-003`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms parent dependency remains `blocked` waiting for `Claude`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12` | Confirms dependency-side support packet is archived `done` but keeps the parent blocker unchanged. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003` | Confirms AG-XR-003 is archived `done`; use its archive/review packet for terminal truth. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14` | Confirms followup-14 is archived `done` after Claude review and owner closeout. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-002` | Confirms additive v1.2 OpenAPI/bundle work is active `review` after PR `#1983` merged. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-002-SIDECAR-REVIEW` | Confirms separate AG-XR-OPENAPI-002 review-support sidecar is active `in_progress`. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-22.md` | Previous AG-FE-ID-001 support baseline. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12.md` | Latest dependency-side servant-session type-contract blocker packet. |
| `support/sidecars/AG-XR-003/AG-XR-003-SIDECAR-REVIEW.md` | Consolidated AG-XR-003 review/lifecycle summary after parent closeout. |
| `support/sidecars/AG-XR-003/REVIEW-AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14.md` | Claude approval notes for latest AG-XR support packet. |
| `git diff --name-status a93a26b9..origin/dev -- <checked handoff pathset>` | Shows only dependency-side support, AG-XR support, Strategy Workshop private-content/storage, and additive v1.2 materials after followup-22 closeout. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` and `agora_v1_2.openapi.yaml` | Both define servant-session routes on paper; both create schemas lack a public session type field. |
| `services/control-plane/bff/agora/servant/router.py` | Confirms `/bff/agora/servant/ensure` runtime behavior and required headers. |
| `services/control-plane/bff/main.py` | Confirms legacy ask/session routes remain separate from the servant-session facade. |
| `docs/contracts/agora/dev-compatibility-manifest.json` | Manifest is valid pending state with `frontend-runtime-commit-placeholder`. |
| `gh pr view 63 --repo ajoe734/execute-plans` | Confirms execute-plans PR `#63` remains `OPEN`, `UNSTABLE`, head `e1cb9125`, check `integration-gate` failed. |
| Execute-plans remote tree probes | Confirm parent frontend target files remain absent from checked `origin/main` and `origin/dev`. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-22 Closeout

Baseline: `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-22` closeout PR `#1977`
merged into `dev` at `a93a26b980757ca96ebd6d76979f2a8409495c67`.

| Change | What changed | Parent implication |
|---|---|---|
| AG-XR-003 followup-14 closed | PR `#1976` merged at `d63c0eb4`; support packet and Claude review are archived `done`. | Local manifest sanity evidence is durable; it does not add AG-FE-ID-001 shell/client files. |
| AG-XR-003 archived done | Status archive records parent `AG-XR-003` done after Pantheon-side gate implementation and support review. | Treat Pantheon-side gate work as closed, but keep the PR `#63` and frontend runtime pin as cross-repo follow-through risks. |
| AG-DES-SW-DB-001 landed | Adds `services/control-plane/bff/agora/strategy_workshop/store.py`, bootstrap tests, and v3 workshop/private-content storage schemas. | Strategy Workshop storage support, not AG-FE-ID-001 identity/servant shell implementation. |
| Additive v1.2 OpenAPI/spec material landed | Adds `services/control-plane/openapi/agora_v1_2.openapi.yaml`, `bundle_index.v1_2.json`, `capability_manifest_v1_2.json`, and v3 private-content/storage schemas. | Useful future contract context; does not supersede the AG-FE-ID-001 v1.1 identity/servant handoff. |
| AG-XR-OPENAPI-002 PR #1983 landed | Merges the additive v1.2 OpenAPI/capability/bundle implementation and bundle tests. | Parent should treat it as review-pending v1.2 contract context, not an implemented AG-FE-ID-001 frontend shell/client. |
| AG-DES-SW-PRIV review followup refreshed | `dffa0ee5..a9347b7a` updates AG-DES-SW-PRIV sidecar review support text only. | No AG-FE-ID-001 BFF/frontend handoff implication in the checked pathset. |
| Management upload-path refresh landed | `a9347b7a..fe6136c8` adds `scripts/test_release_gate_current_run.py`. | No AG-FE-ID-001 BFF/frontend handoff implication in the checked pathset. |
| Management `nl/ask` async refresh landed | `fe6136c8..994dce7d` changes `services/control-plane/bff/main.py` inside `bff_management_nl_ask` context/audit handling. | BFF runtime changed, but outside the `/bff/agora/*` identity/servant/session handoff route family. |
| v1.2 servant-session create shape checked | `ServantSessionCreateRequest` in v1.2 still lacks a public session type field, matching v1.1. | AG-BE-ID-003 type-contract blocker remains. |
| AG-BE-ID-003 followup-12 closed | Closeout PR `#1980` merged at `ff92e8cb`; task archive says parent AG-BE-ID-003 remains blocked. | Reinforces that frontend session controls must stay disabled. |
| AG-DES-SW-PRIV support material landed | Private-content design/task/support updates landed in task briefs. | No direct AG-FE-ID-001 shell/client implementation impact. |
| No AG-FE-ID-001 source/support delta | The checked pathset from `a93a26b9..origin/dev` has no new `support/sidecars/AG-FE-ID-001/*` changes after followup-22. | This packet is a freshness refresh, not a changed parent implementation baseline. |
| Execute-plans remote state unchanged for targets | Checked remotes still lack `AgoraApp.tsx`, `identity.ts`, and `servant.ts`. | Parent still needs to add these files or open a blocker before claiming acceptance. |

## 5. BFF Query Ledger For Parent

| Route or surface | Runtime BFF status | Contract/generated status | Frontend handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover identity scope and servant policy context. | Runtime route, not a generated OpenAPI v1.1/v1.2 operation in the checked specs. | Parent may use it as narrow identity readiness. Keep the client strict and document runtime-only route status. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover filtered capabilities. | Runtime route, not a generated OpenAPI v1.1/v1.2 operation in the checked specs. | Parent may use it for readiness/capability display; do not infer generated operation support. |
| `POST /bff/agora/servant/ensure` | Implemented in `services/control-plane/bff/agora/servant/router.py`; runtime requires `Idempotency-Key` and `X-Request-Id`; tests cover current 200 provisioning/reconcile, missing-header 422, and unauth 401. | Present in v1.1 and v1.2 OpenAPI as `ensureAgoraServant`; runtime remains current-200/no-body while OpenAPI route expectations are not exactly the same. | `servant.ts` should send both headers, parse current 200 `ServantProfile`, and map 401/403/422/503 explicitly. |
| `GET /bff/agora/servant` | No current servant sub-router handler was identified in checked runtime paths. | Present in v1.1 and v1.2 OpenAPI. | Do not make the shell depend on this route until runtime support lands or reviewer records a disposition. |
| `POST /bff/agora/servant/reconcile` | No current servant sub-router handler was identified in checked runtime paths. | Present in v1.1 and v1.2 OpenAPI. | Keep out of the parent UI until runtime support exists or reviewer records a disposition. |
| `POST /bff/agora/servant/sessions*` | Still no accepted BFF runtime implementation in checked paths; parent `AG-BE-ID-003` is blocked. | Present in v1.1 and v1.2 OpenAPI, but `ServantSessionCreateRequest` lacks a public session type field and rejects undeclared top-level fields. | Do not call these routes from the parent frontend until AG-BE-ID-003 lands and the type decision is approved. |
| `GET/POST /bff/agora/sessions*` | Legacy routes live in `main.py`; create accepts `mode` or `sessionType` and defaults to `quick_ask`. | Not the servant-session facade. | Do not treat these routes as proof of `interactive`, `trainer`, or `research_task` readiness. |
| `GET/POST /bff/agora/ask/sessions*` | Existing quick-ask surface in `main.py`; close/stream semantics are ask-channel oriented. | Ownership remains separate from the servant-session facade unless explicitly reassigned. | Do not use for parent `interactive`, `trainer`, or `research_task` controls. |
| Strategy Workshop v1.2 routes/storage | New v1.2 contract/storage/capability material is workshop/private-content oriented. | AG-XR-OPENAPI-002 is in `review` after PR `#1983`; separate review-support sidecar is active. | Do not absorb it into the AG-FE-ID-001 identity/servant status shell unless parent scope is explicitly expanded and reviewed. |

Safe parent-shell facts are unchanged: user-private identity scope, filtered
capability readiness, successful servant profile ensure/reconcile through
`/ensure`, and no validated servant-session facade.

## 6. Frontend Surface To Hand Off

Remote probe source: `/home/lupin/code/execute-plans` after
`git fetch origin --prune`.

| Surface | Current remote-tree state | Required parent decision |
|---|---|---|
| `src/agora/AgoraApp.tsx` | Missing from both `origin/main` and `origin/dev`. | Parent must add the shell or block for missing design/spec authority. |
| `src/lib/bff-v1/agora/identity.ts` | Missing from both `origin/main` and `origin/dev`. | Parent should add strict clients for `/me` and `/capabilities`; these are runtime routes, not generated OpenAPI operations. |
| `src/lib/bff-v1/agora/servant.ts` | Missing from both `origin/main` and `origin/dev`. | Parent should add a strict ensure client for `/servant/ensure`, including idempotency/request headers and typed failure mapping. |
| `src/lib/bff-v1/agora/types.ts` | Missing from `origin/main`; present on `origin/dev`. | Parent must confirm the actual frontend delivery branch before relying on generated Agora types. |
| `src/entries/agora-main.tsx` | Missing from both checked remote trees. | Parent must resolve frontend entry/build delivery-base truth before attaching a shell. |
| `vite.agora.config.ts` | Missing from both checked remote trees. | Parent must not assume an Agora-specific Vite entry is visible on checked remotes. |
| `agora.html` | Missing from both checked remote trees. | Parent must verify delivery base or a clean task worktree before depending on this entry. |
| `src/agora/pages/AskPersonas.tsx` | Present on both checked remote trees. | Ask/session UI must remain gated behind identity/servant readiness and the AG-BE-ID-003 session decision. |
| `src/lib/bff/agora.ts` | Present on both checked remote trees. | Not sufficient for parent acceptance; strict clients under `src/lib/bff-v1/agora/*` are still needed. |
| `/home/lupin/code/execute-plans` worktree | `main...origin/main [ahead 2, behind 467]`. | Use remote tree checks or a clean frontend task worktree, not this stale local checkout, for implementation truth. |

Parent shell and clients must not import or expose Management, capital pool,
broker order, live order, or RuntimeBinding controls.

## 7. Minimal Status-Shell Contract

If parent `AG-FE-ID-001` proceeds before `AG-BE-ID-003` clears, the safe
frontend shape remains:

```text
approved Agora entry
  -> AgoraApp.tsx or approved shell
     -> identity.getAgoraMe()
     -> identity.getAgoraCapabilities()
     -> servant.ensureAgoraServant({ idempotencyKey, requestId })
     -> current runtime 200 maps to servant_profile_ready
     -> Ask/session/command surfaces remain disabled or read-only
        while AG-BE-ID-003 is blocked
```

Required shell states:

| State | Trigger | UI/runtime rule |
|---|---|---|
| Auth blocked | Missing auth or `401` from readiness/ensure call. | Render blocked auth state; no servant/session controls. |
| Scope/audience blocked | `403`, wrong tenant, wrong audience, or missing Agora capability. | Render blocked scope state; no seed/mock retry. |
| Identity ready | `/me` and `/capabilities` succeed. | Show tenant/user predicate, granted capabilities, and servant policy facts. |
| Servant profile ready | `/servant/ensure` returns current runtime `200` with `ServantProfile`. | Show servant persona/status/policy; no broker, capital, RuntimeBinding, or order authority. |
| Servant ensure validation failed | Missing `Idempotency-Key` or `X-Request-Id`. | Treat as a client implementation defect; no mock retry. |
| OpenClaw sync degraded | Runtime returns `503` dependency unavailable during servant agent sync. | Show provisioning/reconcile failed state with no session controls. |
| Session facade unavailable | `AG-BE-ID-003` remains blocked. | Keep Ask/session/command surfaces disabled or read-only. |
| BFF unavailable in strict mode | Network error or 5xx while live strict is configured. | Render unavailable state; no silent mock fallback. |
| Compatibility follow-through pending | execute-plans PR `#63` remains open/unstable or frontend runtime commit is placeholder. | Do not claim deployment readiness from local shell behavior alone. |

`servant_policy.execution_authority = "none"` and prohibited authority facts
may be displayed as safety context. They must not become operator controls.

## 8. Operator Journey

### Current honest journey

```text
Operator opens the approved Agora entry
  -> frontend verifies Agora-scoped auth/audience
  -> frontend calls GET /bff/agora/me through a strict identity client
  -> BFF returns tenant_id, user_id, fail-closed read_predicate,
     Agora capabilities, and servant_policy
  -> frontend calls GET /bff/agora/capabilities through a strict identity client
  -> BFF returns filtered capability manifest and backend scope
  -> frontend calls POST /bff/agora/servant/ensure with Idempotency-Key
     and X-Request-Id
  -> BFF creates or reconciles one user-private agora_servant persona,
     syncs OpenClaw agent metadata, and returns current runtime 200
     ServantProfile envelope
  -> shell renders servant profile ready and no-authority policy facts
  -> Ask/session/command surfaces remain disabled or read-only because
     AG-BE-ID-003 is blocked
```

### Future session journey, still blocked

```text
AG-BE-ID-003 resolves the BFF session contract
  -> route family is frozen, preferably /bff/agora/servant/sessions
  -> approved public session_type/session_kind or deterministic derivation exists
  -> research_task maps to an approved OpenClaw skill/session kind
  -> runtime implements create/message/terminate/session-scoped stream
  -> message writes carry required audit fields
  -> OPENCLAW_UPSTREAM_DEGRADED or an approved equivalent is reachable and tested
  -> frontend adds strict session clients under src/lib/bff-v1/agora/*
  -> AskPersonas or replacement command UI is enabled only after readiness
     is proven
```

## 9. Parent Absorption Checklist

Claude should not absorb this sidecar into parent implementation unless the
parent evidence answers these checks:

| Check | Required evidence |
|---|---|
| Parent dependency disposition | Parent either waits for `AG-BE-ID-003`, or explicitly narrows completion to an identity plus servant-profile status shell while leaving sessions disabled. |
| Frontend base truth | Parent identifies the exact execute-plans branch/commit it is building from; do not assume local `/home/lupin/code/execute-plans` has current truth. |
| Identity route truth | Parent states `/me` and `/capabilities` are interim runtime routes, not generated OpenAPI operations. |
| Servant ensure truth | Parent proves `/servant/ensure` success and typed 401/403/422/503 failure handling where applicable. |
| Ensure contract/runtime mismatch | Parent explicitly notes current runtime accepts no body and returns 200 for create/reconcile, while OpenAPI expectations differ. |
| Type mirror truth | Parent verifies generated Agora frontend types on the actual delivery branch before reuse. |
| Servant session contract | Parent does not send undeclared `session_type` or `sessionType` to `ServantSessionCreateRequest`; it waits for approved schema or derivation. |
| Route family decision | Parent does not mix `/bff/agora/servant/sessions`, legacy `/bff/agora/sessions`, and `/bff/agora/ask/sessions` without explicit backend disposition. |
| Strategy Workshop separation | v1.2 workshop/private-content routes are not treated as AG-FE-ID-001 shell/session readiness. |
| Strict clients | `identity.ts` and `servant.ts` use strict live semantics, do not fall back to mock/seed data, and keep page components away from direct route fetches. |
| Bundle isolation | Agora shell tests or static checks prove no Management/runtime-binding/capital/broker/order code leaks into the app shell. |
| Compatibility honesty | Parent does not claim dev deployment readiness while PR `#63` is open/unstable or the manifest frontend runtime commit is a placeholder. |
| Tests | Parent adds focused frontend tests for identity success, auth/audience failure, strict no-fallback, servant ensure success/failure mapping, disabled session controls while `AG-BE-ID-003` is blocked, and no forbidden bundle strings. |

## 10. Verification Performed For This Sidecar

Commands and results:

| Command | Result |
|---|---|
| `git status -sb` | On `task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-23`; before edits only the generated task brief was untracked. |
| `git branch --show-current` | `task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-23`. |
| `git remote -v` | `origin` is `https://github.com/ajoe734/pantheon.git`. |
| `./scripts/git/task_start.sh "AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-23"` | Reset the task branch to then-current `origin/dev` at `539e4184`. |
| `git merge --ff-only origin/dev` | Fast-forwarded the task branch to current `origin/dev` at `dffa0ee5` after AG-XR-OPENAPI-002 PR `#1983` merged. |
| `git merge --ff-only origin/dev` after latest fetch | Fast-forwarded the task branch to current `origin/dev` at `a9347b7a`; the new diff only touched AG-DES-SW-PRIV sidecar review support text. |
| `git merge origin/dev --no-edit` after PR `#1986` reported `BEHIND` | Merged current `origin/dev` at `fe6136c8`; the new checked diff only added `scripts/test_release_gate_current_run.py` outside this handoff scope. |
| `git merge origin/dev --no-edit` after PR `#1986` again reported `BEHIND` | Merged current `origin/dev` at `994dce7d`; focused diff shows Management `nl/ask` async context/audit handling only. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-23` | Active `in_progress`, owner `Codex`, reviewer `Claude`, support-only artifact path. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001` | Parent `todo`; depends on `AG-FE-000` and `AG-BE-ID-003`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003` | Dependency remains `blocked`, waiting for `Claude`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12` | Archived `done`; closeout PR `#1980` merged and blocker unchanged. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003` | Archived `done`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14` | Archived `done`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-002` | Active `review`, additive v1.2 OpenAPI/bundle work merged in PR `#1983` and awaiting review. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-002-SIDECAR-REVIEW` | Active `in_progress`, separate review-support sidecar. |
| `git diff --name-status a93a26b980757ca96ebd6d76979f2a8409495c67..origin/dev -- <checked handoff pathset>` | Shows dependency/support and v1.2 workshop/private-content/storage material; no AG-FE-ID-001 shell/client implementation. |
| `python3 -m pytest services/control-plane/bff/tests/test_agora_router.py integrations/openclaw/test_persona_agent_sync.py services/control-plane/bff/tests/test_agora_identity_scope.py` | `35 passed in 14.78s` after the `994dce7d` BFF refresh. |
| `git diff -U0 fe6136c80b20ae57d525191db0120a845b62d2a7..origin/dev -- services/control-plane/bff/main.py \| rg -n "agora\|servant\|session\|management/nl\|nl/ask\|context\|async\|OpenClaw\|assistant"` | Shows only `bff_management_nl_ask` async context/audit handling, outside the Agora servant-session route family. |
| `python3 scripts/agora_schema_bundle.py --verify` | OK for frozen Agora schemas, capability manifest, and `openapi/agora_v1.openapi.yaml`. |
| `python3 -c "import pathlib, yaml; yaml.safe_load(pathlib.Path('services/control-plane/openapi/agora_v1_1.openapi.yaml').read_text()); yaml.safe_load(pathlib.Path('services/control-plane/openapi/agora_v1_2.openapi.yaml').read_text())"` | Passed with no output. |
| `python3 scripts/agora_compat_manifest.py verify --allow-pending --manifest docs/contracts/agora/dev-compatibility-manifest.json` | `ok docs/contracts/agora/dev-compatibility-manifest.json`. |
| `python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Exit `1`, expected fail-closed errors: status must be compatible, frontend runtime commit is placeholder, and blocking reasons must be empty. |
| `python3 -m pytest scripts/test_agora_compat_manifest.py -q` | `4 passed in 3.76s`. |
| `npm --prefix execute-plans run contract:drift` | Passed; 20 bundle digests, 17 schemas, 96 OpenAPI operations. |
| `python3 -m pytest scripts/test_agora_v1_2_bundle.py -q` | `5 passed in 2.38s`. |
| `git diff --check -- .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_23.md support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-23.md` | Passed with no output. |
| `rg` marker scan over the task brief and packet | No unresolved marker matches. |
| `git -C /home/lupin/code/execute-plans fetch origin --prune` | Completed successfully. |
| `git -C /home/lupin/code/execute-plans symbolic-ref refs/remotes/origin/HEAD` | `refs/remotes/origin/main`. |
| `git -C /home/lupin/code/execute-plans rev-parse origin/main` | `7b2f17c4dee8dcafe62c2295504df03aed0ae16e`. |
| `git -C /home/lupin/code/execute-plans rev-parse origin/dev` | `7aa4917272212452fe5e4dc99bf2d76fe48eacfd`. |
| `git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/main -- ...` | Only `package.json`, `src/agora/pages/AskPersonas.tsx`, and `src/lib/bff/agora.ts` were present from the probed list. |
| `git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/dev -- ...` | Only `package.json`, `src/agora/pages/AskPersonas.tsx`, `src/lib/bff-v1/agora/types.ts`, and `src/lib/bff/agora.ts` were present from the probed list. |
| `git -C /home/lupin/code/execute-plans status -sb` | `## main...origin/main [ahead 2, behind 467]`. |
| `gh pr view 63 --repo ajoe734/execute-plans --json state,mergeStateStatus,headRefOid,updatedAt,url,statusCheckRollup` | PR `#63` is `OPEN`, `UNSTABLE`, head `e1cb9125c87d9ace0adf3dd9f17f24ff0542d9c5`; `integration-gate` failed in run `27877483718`. |
| `rg -n "ServantSessionCreateRequest\|servant/sessions\|session_type\|sessionType\|quick_ask\|OPENCLAW_UPSTREAM_DEGRADED\|createServantSession" ...` | Confirms OpenAPI v1.1/v1.2 servant-session paper routes, missing public session type field, and separate legacy ask/session surfaces. |
| `rg -n "@router\.(get\|post)\|/bff/agora/servant\|ensure\|Idempotency-Key\|X-Request-Id\|DEPENDENCY_UNAVAILABLE" ...` | Confirms `/servant/ensure` runtime route, required headers, and current dependency-unavailable mapping. |

## 11. Reviewer Handoff

Claude should review this packet as support-only. The review basis is:

1. Followup-22 is archived `done` through packet PR `#1955` and closeout PR
   `#1977`.
2. Current `origin/dev` is `994dce7d`.
3. New dependency-side support (`AG-BE-ID-003` followup-12) is archived `done`
   and explicitly leaves the parent `AG-BE-ID-003` blocker unchanged.
4. AG-XR-003 is archived `done`, but execute-plans PR `#63` remains
   `OPEN`/`UNSTABLE` and the committed manifest still has a placeholder
   frontend runtime commit.
5. Additive v1.2 Strategy Workshop/private-content/storage/capability material
   from AG-XR-OPENAPI-002 is in review and is not an AG-FE-ID-001 shell/client
   implementation; it also does not solve the servant-session type-contract
   decision.
6. The parent frontend target files remain absent from checked execute-plans
   remote trees.
7. Focused BFF/OpenClaw pytest, schema/OpenAPI checks, manifest verify,
   manifest pytest, and contract drift are green; deployment gate still fails
   closed as expected for pending compatibility.
8. The packet does not change canonical truth, BFF runtime code, OpenAPI
   source-of-truth semantics, capability manifests, governance, database,
   OpenClaw adapter code, compatibility manifest source, or frontend source.

After this artifact PR merges, Codex should hand the task to Claude with:

```bash
AI_NAME=Codex ./scripts/ai-status.sh handoff AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-23 Claude "Support-only followup-23 packet merged; please review the BFF/frontend handoff artifact."
```

*Prepared by Codex for the `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-23`
support slice.*

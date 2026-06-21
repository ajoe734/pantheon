# AG-FE-ID-001 Followup-22 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-22` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex2` / `Claude` |
| Date | `2026-06-21` |
| Status | `in_progress; ready for sidecar review after packet PR merge` |
| Packet observation base | `52a2d5a8cf6eff9e6fda7d98d170d389196cc29c` |
| Previous AG-FE-ID-001 sidecar closeout merge | `7cfa8a4f84c6aced1e4b66c661fd9d7f78779e2c` |
| Previous AG-FE-ID-001 sidecar packet merge | `97cfbdd5037a2cbe20143f24c2775954824e275a` |
| Previous AG-FE-ID-001 sidecar closeout commit | `971b84aefabe9b9d843aa08baafeefd85a89714b` |
| Execute-plans remotes checked | `origin/main` at `7b2f17c4dee8dcafe62c2295504df03aed0ae16e`; `origin/dev` at `7aa4917272212452fe5e4dc99bf2d76fe48eacfd` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance policy, database migrations, OpenClaw adapter code, or
execute-plans source files.

## 1. Purpose

This twenty-second followup refreshes the `AG-FE-ID-001` BFF/frontend handoff
after `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-21` closed through PR #1949.

The material delta from followup-21 is narrow: support closeouts, AG-XR
support packets, and one AG-XR-002A contract/type refresh that improves
compatibility evidence but does not implement the AG-FE-ID-001 shell/client
files.

1. Followup-21 is archived `done`. Its packet PR #1945 merged at
   `97cfbdd5037a2cbe20143f24c2775954824e275a`, and its closeout PR #1949
   merged into `dev` at `7cfa8a4f84c6aced1e4b66c661fd9d7f78779e2c`.
2. Current `origin/dev` resolves to
   `52a2d5a8cf6eff9e6fda7d98d170d389196cc29c`. This task branch merged that
   dev tip after PR #1955 became `BEHIND` again.
3. A focused diff from `52a2d5a8..origin/dev` over BFF/OpenAPI/Agora specs,
   docs/contracts, AG-FE-ID-001 support, AG-BE-ID-003 support, AG-XR-003
   support, and execute-plans mirror paths is empty.
4. A focused diff from followup-21's closeout merge
   `7cfa8a4f84c6aced1e4b66c661fd9d7f78779e2c..origin/dev` contains
   AG-BE-ID-003 followup-11 closeout support material plus AG-XR-002A
   compatibility manifest/type-generation updates plus AG-XR-003 followup-14
   support material. No BFF runtime, OpenAPI route, Agora schema,
   AG-FE-ID-001 support, or AG-FE-ID-001 frontend shell file changed after
   followup-21 closeout.
5. A wider focused diff from followup-21's observation base
   `b3b5b1c3c3cdb37121dd6cc4d3c8f3634cc75c56..origin/dev` contains
   AG-FE-ID-001 followup-21 packet/review, AG-BE-ID-003 followup-11 support,
   AG-XR-003 followup-13/followup-14 support, and the AG-XR-002A contract/type
   refresh. No BFF runtime, OpenAPI route, Agora schema, or AG-FE-ID-001
   frontend shell change supersedes the approved followup-21 handoff.
6. Parent `AG-FE-ID-001` remains `todo`.
7. Parent dependency `AG-BE-ID-003` remains `blocked`, waiting for `Claude` on
   the servant session type-contract decision.
8. `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` is archived `done` through
   closeout PR #1950 at `bfb6b1c640db2a19a3ce025aa8d29982b9164a0b`, but it
   keeps the parent blocker unchanged and is not session runtime readiness.
9. After `bfb6b1c6`, `origin/dev` advanced through unrelated
   `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-12` support material. That path
   is outside this handoff scope and has no BFF/frontend session implication.
10. After `f6b61c6d`, `AG-XR-002A` merged at `e5f20720` and refreshed the
   Pantheon mirror of Agora v1.1 frontend contracts, manifest verifier tests,
   generated type hashing, and `execute-plans/src/lib/bff-v1/agora/types.ts`.
   This improves type/hash sanity, but it does not implement AG-FE-ID-001 shell
   clients or merge execute-plans PR #63.
11. `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-13` is archived `done`, but its
   pre-AG-XR-002A generated-types hash mismatch is partly superseded by the
   new `AG-XR-002A` evidence. Current checks now show manifest
   `verify --allow-pending` OK and `contract:drift` OK, while deployment gate
   still fails closed because compatibility is pending, frontend runtime commit
   is a placeholder, and blocking reasons remain non-empty.
12. `execute-plans` PR #63 is still open and unstable at head `e1cb9125`; its
   latest checked integration-gate run remains `27877483718` with failure.
13. After `e5f20720`, `origin/dev` advanced through unrelated
   `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-12` review support material. That
   path is outside this handoff scope and has no BFF/frontend session
   implication.
14. After `60e3e18c`, `origin/dev` advanced through
   `AG-XR-002A-SIDECAR-BFF-HANDOFF` support material at `285a6d60`. That
   packet is useful AG-XR context, but it is not an AG-FE-ID-001 runtime or
   frontend shell implementation and does not unblock `AG-BE-ID-003`.
15. After `285a6d60`, `origin/dev` advanced through
   `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14` support material at
   `e7d75a11`. That packet refreshes AG-XR-003 acceptance evidence and still
   keeps execute-plans PR #63/runtime pin/deployment gate disposition as the
   compatibility blocker.
16. After `e7d75a11`, `origin/dev` advanced through `AG-XR-002A` closeout
   review artifacts at `f8a8dd73`; `AG-XR-002A` is now archived `done`, but
   that does not merge execute-plans PR #63 or implement the AG-FE-ID-001
   shell/client files.
17. After `f8a8dd73`, `origin/dev` advanced through an unrelated
   `AG-FE-DB-002` sidecar closeout and the `AG-XR-002A-SIDECAR-BFF-HANDOFF`
   closeout at `4588fe17`; the AG-XR-002A sidecar is now archived `done`, but
   no AG-FE-ID-001 runtime or frontend shell file changed.
18. After `4588fe17`, `origin/dev` advanced through the `AG-BE-SW-001` deep
   design closure at `52a2d5a8`. That document is v1.2 additive design input
   and explicitly keeps v1/v1.1 bundles immutable; it does not change this
   v1.1 BFF/frontend handoff conclusion.
19. `execute-plans` remote refs were refreshed. `origin/HEAD` still points to
   `origin/main`, `origin/dev` still exists, and the three parent target files
   remain absent from both checked remote trees: `AgoraApp.tsx`, `identity.ts`,
   and `servant.ts`.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex2`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-22` | active `in_progress`; owner `Codex2`, reviewer `Claude` | This packet prepares the support-only handoff to Claude. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-21` | archived `done`; packet PR #1945 / merge `97cfbdd5`; closeout PR #1949 / merge `7cfa8a4f` | Previous support packet, review, and owner closeout are durable on `dev`. |
| `AG-FE-ID-001` | `todo`; owner `Claude`, reviewer `Codex`; depends on `AG-FE-000` and `AG-BE-ID-003` | Parent implementation has not started in durable task state. |
| `AG-FE-000` | archived `done` | Separate Agora/Management entry/build/audience work is accepted, but remote-tree ambiguity remains relevant because the checked frontend remotes still lack several expected entry files. |
| `AG-BE-ID-002` | archived `done`; implementation PRs merged | `/bff/agora/servant/ensure` is the accepted servant ensure/provision/reconcile surface. |
| `AG-BE-ID-003` | `blocked`; owner `Codex2`, reviewer `Claude`, waiting for `Claude` | Servant session facade remains unavailable pending the session type contract decision. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` | archived `done`; reviewed packet PR #1948 / merge `9880c815`; closeout PR #1950 / merge `bfb6b1c6` | Support packet is durable on `dev`; it does not unblock session routes. |
| `AG-XR-OPENAPI-001` | archived `done` | v1.1 OpenAPI and capability manifest remain present on `dev`. |
| `AG-XR-002A` | archived `done`; implementation PR #1952 merged at `e5f20720`; closeout artifact PR #1957 merged at `f8a8dd73` | Pantheon mirror v1.1 frontend contract/type refresh is accepted, but execute-plans PR #63 remains open/unstable and deployment gate disposition remains unresolved. |
| `AG-XR-002A-SIDECAR-BFF-HANDOFF` | archived `done`; original packet PR #1954 merged at `285a6d60`; closeout PR #1959 merged at `4588fe17` | Support-only AG-XR handoff context is accepted; it does not implement AG-FE-ID-001 shell/client files. |
| `AG-XR-003` | active row is not `done`; current `next` still keeps compatibility closeout gated | Do not claim strict v1.1 cross-repo compatibility or deployment readiness. |
| `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-13` | archived `done`; PR #1946 / merge `13f864d5` | Previous compatibility support packet kept parent AG-XR-003 blocked before AG-XR-002A refreshed manifest/type evidence. |
| `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14` | active `review`; PR #1956 / merge `e7d75a11` | Latest compatibility support packet says local pending-state sanity is green, but execute-plans PR #63/runtime pin/deployment gate disposition still blocks parent closeout. |

Dependency honesty rule: parent `AG-FE-ID-001` still depends on
`AG-BE-ID-003`. The frontend may show identity and servant-profile readiness,
but it must not claim interactive, trainer, or research-task session readiness.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_22.md` | This sidecar's support-only assignment. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-22` | Confirms active task state, owner, reviewer, artifact, and support-only acceptance. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-21` | Confirms predecessor archived `done` through PR #1949 / merge `7cfa8a4f`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent remains `todo` and still depends on `AG-BE-ID-003`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-000` | Confirms upstream entry/build/audience task is archived `done`, with historical delivery-base ambiguity still relevant. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002` | Confirms servant ensure/provision/reconcile is archived `done`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms session facade remains `blocked` on the missing session type contract decision. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` | Confirms dependency-side support packet is archived `done` but keeps the parent blocker unchanged. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-OPENAPI-001` | Confirms v1.1 OpenAPI and capability manifest work is archived `done`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-002A` | Confirms archived `done` state, PR #1952 implementation merge, PR #1957 closeout artifact merge, and accepted v1.1 frontend contract refresh. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-002A-SIDECAR-BFF-HANDOFF` | Confirms archived `done` state, original packet PR #1954 merge, closeout PR #1959 merge, and support-only scope. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003` | Confirms AG-XR-003 is not closed and remains gated by frontend/type/deployment disposition. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-13` | Confirms the previous AG-XR support packet is archived `done` through PR #1946 and preserved the parent blocker before AG-XR-002A refreshed type/hash evidence. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14` | Confirms active `review` sidecar state, PR #1956 merge at `e7d75a11`, and unchanged PR #63/runtime pin/deployment gate blocker. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-21.md` | Previous approved AG-FE-ID-001 support baseline and closeout notes. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-21-REVIEW.md` | Claude's review record for followup-21. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-11.md` | Latest dependency-side session-gate support packet. |
| `support/sidecars/AG-XR-003/AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-13.md` | Previous compatibility-gate support packet. |
| `support/sidecars/AG-XR-003/AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14.md` | Latest compatibility-gate support packet. |
| `docs/reviews/2026-06-21-ag-xr-002a-claude-review.md` | Materialized AG-XR-002A Claude review approval; confirms PR #1952 merge and frozen v1 unchanged. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md` | v1.2 additive Strategy Workshop design closure candidate; explicitly does not rewrite prior v1/v1.1 bundles. |
| `git diff --name-only 52a2d5a8cf6eff9e6fda7d98d170d389196cc29c..origin/dev -- ...` | Empty output: no checked handoff-path delta after merging current `origin/dev`. |
| `git diff --name-only 4588fe174e8305bf37ebb2ee78b9fa578a4d31ea..origin/dev -- docs/04/pantheon_agora_cross_repo_2026-06-20` | Shows the `AG-BE-SW-001` deep design closure doc. |
| `git diff --name-only f8a8dd73021f62af64b3964e947139cfc3b90317..origin/dev -- support/sidecars/AG-XR-002A docs/04/pantheon_agora_cross_repo_2026-06-20` | Shows `support/sidecars/AG-XR-002A/AG-XR-002A-SIDECAR-BFF-HANDOFF.md` and the `AG-BE-SW-001` deep design closure doc. |
| `git diff --name-only e7d75a1161545aa0c2f696882e45fc13ff4bdf35..origin/dev -- docs/reviews .orchestrator/task-briefs/ag_xr_002a.md` | Shows `docs/reviews/2026-06-21-ag-xr-002a-claude-review.md` and `.orchestrator/task-briefs/ag_xr_002a.md`. |
| `git diff --name-only 285a6d6002da982f029d0b8b7447f95f10efc09b..origin/dev -- ...` | Shows `support/sidecars/AG-XR-003/AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14.md`. |
| `git diff --name-only 60e3e18c466a0b3b4d28d8a128f28156e42743cd..origin/dev -- support/sidecars/AG-XR-002A ...` | Shows `support/sidecars/AG-XR-002A/AG-XR-002A-SIDECAR-BFF-HANDOFF.md`. |
| `git diff --name-only e5f20720bc5c0fa7eb1e03972db838eb8098b241..origin/dev -- ...` | Empty output for this checked handoff pathset; the intervening dev change is outside AG-FE-ID-001/BFF/OpenAPI/execute-plans scope. |
| `git diff --name-only f6b61c6d2046926819adf8bd750865397c8a8f7f..origin/dev -- ...` | Shows the `AG-XR-002A` contract/type refresh: dev compatibility manifest, execute-plans type generation/drift scripts, generated Agora types, and manifest tests. |
| `git diff --name-only bfb6b1c640db2a19a3ce025aa8d29982b9164a0b..origin/dev -- ...` | Shows the AG-XR-002A contract/type refresh in this checked handoff pathset; the AG-FE-DB-002 support packet is outside this pathset. |
| `git diff --name-only 7cfa8a4f84c6aced1e4b66c661fd9d7f78779e2c..origin/dev -- ...` | Shows AG-BE-ID-003 followup-11 closeout support plus AG-XR-002A manifest/type-generation/test updates. |
| `git diff --name-only b3b5b1c3c3cdb37121dd6cc4d3c8f3634cc75c56..origin/dev -- ...` | Shows AG-FE-ID-001 followup-21 packet/review, AG-BE-ID-003 followup-11, AG-XR-003 followup-13/followup-14, and AG-XR-002A manifest/type-generation/test updates. |
| Target file probes against `/home/lupin/code/execute-plans` `origin/main` and `origin/dev` | Confirms the parent frontend target files are still absent from refreshed frontend remote trees. |
| Focused BFF/OpenClaw pytest, schema bundle verify, OpenAPI YAML load, and route spot checks | Confirms current BFF identity/servant evidence and frozen bundle remain green. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-21

| Change | What changed | Parent implication |
|---|---|---|
| FOLLOWUP-21 closed | Archived `done`; packet PR #1945 merged at `97cfbdd5`; closeout PR #1949 merged at `7cfa8a4f`. | Treat FOLLOWUP-21 as accepted support evidence on `dev`. |
| Current dev base | `origin/dev` is `52a2d5a8`; this branch merged that dev tip after PR #1955 became behind again. | The packet is refreshed against the latest visible `dev` tip. |
| Post-followup-21 closeout pathset | `7cfa8a4f..origin/dev` over the checked handoff pathset shows AG-BE-ID-003 followup-11 closeout support, AG-XR-002A manifest/type-generation/test updates, and AG-XR-003 followup-14 support. | No post-followup-21 BFF runtime, OpenAPI route, Agora schema, AG-FE-ID-001 support, or AG-FE-ID-001 shell/client file changed. |
| Current-tip pathset | `52a2d5a8..origin/dev` over the checked handoff pathset is empty. | No additional visible delta after the branch refresh. |
| Unrelated dev advancement | `bfb6b1c6..f6b61c6d` added `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-12.md` outside this checked handoff pathset. | No AG-FE-ID-001 BFF/frontend implication. |
| Additional unrelated review | `e5f20720..60e3e18c` added `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-12-REVIEW.md` outside this checked handoff pathset. | No AG-FE-ID-001 BFF/frontend implication. |
| AG-XR-002A sidecar handoff | `60e3e18c..285a6d60` added `support/sidecars/AG-XR-002A/AG-XR-002A-SIDECAR-BFF-HANDOFF.md`. | Useful AG-XR support context, but no AG-FE-ID-001 runtime/frontend shell implementation. |
| AG-XR-003 followup-14 | `285a6d60..e7d75a11` added `support/sidecars/AG-XR-003/AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14.md`. | Latest AG-XR support keeps deployment readiness gated by execute-plans PR #63/runtime pin/deployment gate disposition. |
| AG-XR-002A closeout | `e7d75a11..f8a8dd73` added `.orchestrator/task-briefs/ag_xr_002a.md` and `docs/reviews/2026-06-21-ag-xr-002a-claude-review.md`. | AG-XR-002A is now archived `done`; parent still cannot claim deployment readiness because execute-plans PR #63/runtime pin/deployment gate disposition remains unresolved. |
| AG-XR-002A sidecar closeout | `f8a8dd73..4588fe17` updated `support/sidecars/AG-XR-002A/AG-XR-002A-SIDECAR-BFF-HANDOFF.md`; the same interval also contains unrelated AG-FE-DB-002 closeout material. | AG-XR-002A sidecar is now archived `done`; no AG-FE-ID-001 runtime/frontend shell implication. |
| AG-BE-SW-001 design closure | `4588fe17..52a2d5a8` added the v1.2 additive Strategy Workshop design closure candidate. | It keeps prior v1/v1.1 bundles immutable and does not change AG-FE-ID-001 shell/client readiness. |
| AG-XR-002A landed | `f6b61c6d..origin/dev` updated `docs/contracts/agora/dev-compatibility-manifest.json`, execute-plans contract generation/drift scripts, generated Agora `types.ts`, and manifest tests. | The previous generated-types hash mismatch is no longer the current local blocker; parent still cannot claim deployment readiness because execute-plans PR #63 and deployment gate disposition remain unresolved. |
| Wider pathset from followup-21 observation base | `b3b5b1c3..origin/dev` shows AG-FE-ID-001 followup-21 packet/review, AG-BE-ID-003 followup-11, AG-XR-003 followup-13/followup-14 support, and AG-XR-002A manifest/type-generation/test updates. | The new AG-XR evidence improves local type/hash sanity but preserves the AG-FE-ID-001 session and deployment gates. |
| AG-BE-ID-003 followup-11 closed | The support artifact is archived `done` through PR #1950 at `bfb6b1c6`. | Supersedes followup-21's temporal note that no artifact existed, but does not unblock sessions. |
| AG-XR followup-13 closed | Archived `done`; its generated-types hash mismatch note is partly superseded by AG-XR-002A, but PR #63 remains open/unstable. | Compatibility/deployment readiness remains gated. |
| Execute-plans remote probe | `origin/main` at `7b2f17c4` and `origin/dev` at `7aa49172` still lack `src/agora/AgoraApp.tsx`, `src/lib/bff-v1/agora/identity.ts`, and `src/lib/bff-v1/agora/servant.ts`. | Parent still needs to add the requested shell/client files or open a blocker. |
| Execute-plans branch ambiguity | `origin/HEAD -> origin/main`, but `origin/dev` also exists and AG-FE-000 archive records dev/default-branch closeout complexity. | Parent must confirm the actual frontend delivery base before coding or reviewing. |
| Execute-plans local checkout | `/home/lupin/code/execute-plans` worktree is `main...origin/main [ahead 2, behind 467]`. | Do not rely on that local checkout as latest frontend truth; use remote tree checks or a clean task worktree. |
| AG-BE-ID-003 | Still `blocked`, waiting for Claude. | Session UI and session clients remain gated. |

## 5. BFF Query Ledger For Parent

| Route or surface | Runtime BFF status | Contract/generated status | Frontend handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover envelope, tenant/user predicate, capabilities, and servant policy. | Not generated as an OpenAPI v1.1 operation. | Parent may use it as interim runtime route truth for identity readiness. Keep the client narrow and document runtime-only status. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover filtered capability manifest and backend scope. | Same as `/me`: runtime route, not generated operation coverage. | Parent may use it for readiness/capability display. Do not claim generated operation support. |
| `POST /bff/agora/servant/ensure` | Implemented and archived through `AG-BE-ID-002`; runtime requires `Idempotency-Key` and `X-Request-Id`, derives user scope server-side, creates or reconciles one user-private `agora_servant`, syncs OpenClaw metadata, and returns current `200` profile envelopes in tests. | Present in v1.1 OpenAPI as `ensureAgoraServant`; previously observed mismatch remains: OpenAPI declares request/response expectations that do not exactly match current runtime no-body/current-200 behavior. | `servant.ts` should send both headers, parse current `200` `ServantProfile`, handle 401/403/422/503 explicitly, and record the body/status mismatch. |
| `GET /bff/agora/servant` | No current servant sub-router handler was identified in the checked runtime paths. | Present in v1.1 OpenAPI as `getAgoraServant`. | Do not make the parent shell depend on this read route until runtime support lands. |
| `POST /bff/agora/servant/reconcile` | No current servant sub-router handler was identified in the checked runtime paths. | Present in v1.1 OpenAPI. | Keep out of the parent UI until runtime support exists or reviewer records a disposition. |
| `POST /bff/agora/servant/sessions*` | Still no accepted BFF runtime implementation; parent `AG-BE-ID-003` is blocked before coding. | Present in v1.1 OpenAPI, but `ServantSessionCreateRequest` still lacks `session_type` and rejects undeclared top-level fields. | Do not call these routes from the parent frontend until `AG-BE-ID-003` lands and the contract decision is approved. |
| `GET/POST /bff/agora/sessions*` | Legacy routes live in `main.py`; create accepts `mode` or `sessionType` and defaults to `quick_ask`. | Not the v1.1 servant-session facade. | Do not treat these routes as proof of `interactive`, `trainer`, or `research_task` readiness. |
| `GET/POST /bff/agora/ask/sessions*` | Existing quick-ask surface in `main.py`; close/stream semantics are ask-channel oriented. | Ownership remains separate from the v1.1 servant-session facade unless explicitly reassigned. | Do not use for parent `interactive`, `trainer`, or `research_task` controls. |
| Dashboard recipe/widget routes | Runtime/dashboard work has advanced separately. | Present in v1.1 OpenAPI and generated mirrors. | Dashboard readiness remains separate from identity/servant/session shell readiness. |

Safe parent-shell facts are unchanged: user-private identity scope, filtered
capability readiness, successful servant profile ensure/reconcile through
`/ensure`, and no validated servant-session facade.

Attention item: `services/control-plane/bff/tests/test_agora_router.py` still
has a top-level comment saying `/servant/ensure` returns HTTP 501. The concrete
tests now assert provisioning, reconcile, required-header `422`, and unauth
`401` behavior. Reviewers should rely on the concrete tests and route code, not
that stale header comment.

## 6. Session And Compatibility Gate Status

`AG-BE-ID-003` remains blocked on the same contract decision recorded in
followup-21 and AG-BE-ID-003 followup-11.

| Gate | Current blocker | Frontend rule |
|---|---|---|
| Session type field | `ServantSessionCreateRequest` allows only `intent`, `strategy_ref`, and `metadata`; `additionalProperties: false`. | Strict FE clients must not send undeclared top-level fields. |
| Public derivation rule | No reviewer-approved rule says how BFF derives `interactive`, `trainer`, or `research_task` from route/context. | FE must wait for explicit schema or derivation authority. |
| Research task mapping | Checked evidence names `interactive` and `trainer`; `research_task` skill/session ownership remains unresolved. | Research-task controls stay disabled. |
| Runtime route family | v1.1 OpenAPI lists `/bff/agora/servant/sessions*`, but BFF runtime implementation is not accepted. | Do not wire live create/message/terminate/stream clients. |
| Degraded error | `OPENCLAW_UPSTREAM_DEGRADED` was not found in checked BFF runtime paths for this session facade. | Do not display a tested session degradation state yet. |
| Cross-repo compatibility | After AG-XR-002A, local manifest `verify --allow-pending` and `contract:drift` pass, but deployment gate still fails closed and execute-plans PR #63 remains open/unstable. | Strict v1.1 live release claims stay gated. |

If the parent later approves an explicit public type field, the expected FE
client shape remains:

```ts
type ServantSessionType = "interactive" | "trainer" | "research_task";

createServantSession(input: {
  sessionType: ServantSessionType;
  intent: string;
  strategyRef?: string;
  metadata?: Record<string, unknown>;
}): Promise<ServantSessionEnvelope>;
```

The actual wire field must match the reviewer-approved OpenAPI/schema field.
This is a handoff expectation, not a claim that the current schema is ready.

## 7. Frontend Surface To Hand Off

Remote probe source: `/home/lupin/code/execute-plans` after
`git fetch origin --prune`, checking `origin/main` at `7b2f17c4` and
`origin/dev` at `7aa49172`.

| Surface | Current remote-tree state | Required parent decision |
|---|---|---|
| `src/agora/AgoraApp.tsx` | Missing from both `origin/main` and `origin/dev`. | Parent must add the shell or block for missing design/spec authority. |
| `src/lib/bff-v1/agora/identity.ts` | Missing from both `origin/main` and `origin/dev`. | Parent should add strict clients for `/me` and `/capabilities`; these are runtime routes, not generated OpenAPI operations. |
| `src/lib/bff-v1/agora/servant.ts` | Missing from both `origin/main` and `origin/dev`. | Parent should add a strict ensure client for `/servant/ensure`, including idempotency/request headers and typed failure mapping. |
| `src/lib/bff-v1/agora/types.ts` | Missing from `origin/main`; present on `origin/dev`. Pantheon mirror `execute-plans/src/lib/bff-v1/agora/types.ts` was refreshed by AG-XR-002A on `dev`. | Parent must confirm the delivery branch and AG-XR compatibility disposition before relying on generated Agora types in the actual frontend repo. |
| `src/entries/agora-main.tsx` | Missing from both checked remote trees, despite AG-FE-000 archive saying entry/build work landed in its task branch. | Parent must resolve frontend delivery-base truth before claiming the shell can attach to an existing Agora entry. |
| `vite.agora.config.ts` | Missing from both checked remote trees. | Parent must not assume AG-FE-000 entry/build artifacts are visible on checked remotes. |
| `agora.html` | Missing from both checked remote trees. | Parent must verify the delivery base or a clean task branch before depending on a separate Agora HTML entry. |
| `src/agora/pages/AskPersonas.tsx` | Present on both checked remote trees. | Ask/session UI must remain gated behind identity/servant readiness and the AG-BE-ID-003 session decision. |
| `src/lib/bff/agora.ts` | Present on both checked remote trees. | Not sufficient for parent acceptance; strict clients under `src/lib/bff-v1/agora/*` are still needed. |
| `package.json` | Present on both checked remote trees. | Parent should inspect scripts on the exact delivery branch before claiming build/test command availability. |
| `/home/lupin/code/execute-plans` worktree | Local branch `main` is ahead 2 and behind 467. | Use a clean frontend task worktree or remote tree checks for implementation/review. |

Parent shell and clients must not import or expose Management, capital pool,
broker order, live order, or RuntimeBinding controls.

## 8. Minimal Status-Shell Contract

If parent `AG-FE-ID-001` proceeds before `AG-BE-ID-003` clears, the safe
frontend shape remains:

```text
agora-main.tsx or approved Agora entry
  -> AgoraApp.tsx or approved shell
     -> identity.getAgoraMe()
     -> identity.getAgoraCapabilities()
     -> servant.ensureAgoraServant({ idempotencyKey, requestId })
     -> current 200 maps to servant_profile_ready
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
| Compatibility gate blocked | AG-XR compatibility or manifest status remains pending. | Do not claim release/deployment readiness from local shell behavior alone. |

`servant_policy.execution_authority = "none"` and
`prohibited_authority = ["runtime_binding", "broker_order", "capital_binding"]`
may be displayed as safety facts. They must not become operator controls.

## 9. Operator Journey

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

## 10. Parent Absorption Checklist

Claude should not absorb this sidecar into parent implementation unless the
parent evidence answers these checks:

| Check | Required evidence |
|---|---|
| Parent dependency disposition | Parent either waits for `AG-BE-ID-003`, or explicitly narrows completion to an identity plus servant-profile status shell while leaving sessions disabled. |
| Frontend base truth | Parent identifies the exact execute-plans branch/commit it is building from; do not assume local `/home/lupin/code/execute-plans` `main`, `origin/main`, or `origin/dev` has all AG-FE-000 artifacts without checking. |
| Identity route truth | Parent states `/me` and `/capabilities` are interim runtime routes, not generated OpenAPI operations. |
| Servant ensure truth | Parent proves `/servant/ensure` success and typed 401/403/422/503 failure handling where applicable. |
| Ensure contract/runtime mismatch | Parent explicitly notes current runtime accepts no body and returns 200 for create/reconcile, while OpenAPI contract expectations differ. |
| Type mirror truth | Parent verifies generated Agora frontend types before reuse; current remote probe finds `types.ts` on `origin/dev` but not `origin/main`. |
| Servant session contract | Parent does not send undeclared `session_type` or `sessionType` to `ServantSessionCreateRequest`; it waits for approved schema or derivation. |
| Route family decision | Parent does not mix `/bff/agora/servant/sessions`, legacy `/bff/agora/sessions`, and `/bff/agora/ask/sessions` without explicit backend disposition. |
| Research task mapping | Parent does not show or call research-task sessions until the OpenClaw skill/session mapping is frozen. |
| Legacy session gap | Parent does not treat `main.py` `/bff/agora/sessions*` as canonical servant-session readiness while it defaults to `quick_ask`. |
| Ask session split | Parent does not use `/bff/agora/ask/sessions*` for `interactive`, `trainer`, or `research_task` controls without explicit backend ownership disposition. |
| Strict clients | `identity.ts` and `servant.ts` use strict live semantics, do not fall back to mock/seed data, and keep page components away from direct route fetches. |
| No broad path import | Agora shell does not import Management, capital pool, broker, order, RuntimeBinding, or dashboard-only control surfaces. |
| Dashboard separation | Dashboard recipe/widget routes remain outside the minimal identity/servant status shell unless the parent scope is explicitly expanded and reviewed. |
| Bundle isolation | Parent tests or static checks prove the Agora bundle does not pull Management/runtime-binding code into the app shell. |
| Compatibility gate honesty | Parent does not claim strict v1.1 dev deployment compatibility while execute-plans PR #63 is open/unstable and the deployment gate still fails closed. |
| Tests | Parent adds focused frontend tests for identity success, auth/audience failure, strict no-fallback, servant ensure success/failure mapping, disabled session controls while `AG-BE-ID-003` is blocked, and no forbidden bundle strings. |

## 11. Suggested Verification For Parent

Backend readiness checks:

```bash
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py integrations/openclaw/test_persona_agent_sync.py services/control-plane/bff/tests/test_agora_identity_scope.py
python3 scripts/agora_schema_bundle.py --verify
python3 -c "import pathlib, yaml; yaml.safe_load(pathlib.Path('services/control-plane/openapi/agora_v1_1.openapi.yaml').read_text())"
```

Frontend remote probes:

```bash
git -C /home/lupin/code/execute-plans fetch origin --prune
git -C /home/lupin/code/execute-plans symbolic-ref refs/remotes/origin/HEAD
git -C /home/lupin/code/execute-plans rev-parse origin/main
git -C /home/lupin/code/execute-plans rev-parse origin/dev
git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/main -- src/agora/AgoraApp.tsx src/lib/bff-v1/agora/identity.ts src/lib/bff-v1/agora/servant.ts src/lib/bff-v1/agora/types.ts src/entries/agora-main.tsx vite.agora.config.ts agora.html src/agora/pages/AskPersonas.tsx src/lib/bff/agora.ts package.json
git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/dev -- src/agora/AgoraApp.tsx src/lib/bff-v1/agora/identity.ts src/lib/bff-v1/agora/servant.ts src/lib/bff-v1/agora/types.ts src/entries/agora-main.tsx vite.agora.config.ts agora.html src/agora/pages/AskPersonas.tsx src/lib/bff/agora.ts package.json
```

Static review spot checks for the parent implementation:

```bash
rg -n "fetch\(|/bff/agora" /path/to/execute-plans/src/agora /path/to/execute-plans/src/lib/bff-v1/agora
rg -n "management|RuntimeBinding|capital|broker|order" /path/to/execute-plans/src/agora /path/to/execute-plans/src/lib/bff-v1/agora
```

## 12. Verification Performed For This Sidecar

Commands and results:

| Command | Result |
|---|---|
| `git status -sb` | On `task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-22`; after merging current `origin/dev`, only this packet was dirty. |
| `git branch --show-current` | `task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-22`. |
| `git remote -v` | `origin` is `https://github.com/ajoe734/pantheon.git`. |
| `git fetch origin --prune` | Completed successfully. |
| `git ls-remote origin refs/heads/dev` | First returned `285a6d6002da982f029d0b8b7447f95f10efc09b`; after PR #1956 merged, returned `e7d75a1161545aa0c2f696882e45fc13ff4bdf35`; after PR #1957 merged, returned `f8a8dd73021f62af64b3964e947139cfc3b90317`; after PR #1961 merged, returned `52a2d5a8cf6eff9e6fda7d98d170d389196cc29c`. |
| `git merge origin/dev --no-edit` | Merged current `origin/dev` after PR #1955 became behind; first added support-only `AG-XR-002A-SIDECAR-BFF-HANDOFF.md` from PR #1954, then `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14.md` from PR #1956, then AG-XR-002A closeout review artifacts from PR #1957, then AG-XR-002A sidecar closeout plus AG-BE-SW-001 design closure material through PR #1961. |
| `git rev-parse HEAD origin/dev` | After the latest merge, `HEAD` was merge commit `8712929f64f8af1d5c8e647efd1a73c59fe7f408`; `origin/dev` was `52a2d5a8cf6eff9e6fda7d98d170d389196cc29c`. |
| `git diff --name-only 52a2d5a8cf6eff9e6fda7d98d170d389196cc29c..origin/dev -- services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora docs/04/pantheon_agora_cross_repo_2026-06-20 support/sidecars/AG-FE-ID-001 support/sidecars/AG-BE-ID-003 support/sidecars/AG-XR-003 support/sidecars/AG-XR-002A execute-plans scripts/test_agora_compat_manifest.py docs/reviews` | Empty output. |
| `git diff --name-only 4588fe174e8305bf37ebb2ee78b9fa578a4d31ea..origin/dev -- services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora docs/04/pantheon_agora_cross_repo_2026-06-20 support/sidecars/AG-FE-ID-001 support/sidecars/AG-BE-ID-003 support/sidecars/AG-XR-003 support/sidecars/AG-XR-002A execute-plans scripts/test_agora_compat_manifest.py docs/reviews` | `docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md`. |
| `git diff --name-only f8a8dd73021f62af64b3964e947139cfc3b90317..origin/dev -- services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora docs/04/pantheon_agora_cross_repo_2026-06-20 support/sidecars/AG-FE-ID-001 support/sidecars/AG-BE-ID-003 support/sidecars/AG-XR-003 support/sidecars/AG-XR-002A execute-plans scripts/test_agora_compat_manifest.py docs/reviews` | `docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md`; `support/sidecars/AG-XR-002A/AG-XR-002A-SIDECAR-BFF-HANDOFF.md`. |
| `git diff --name-only e7d75a1161545aa0c2f696882e45fc13ff4bdf35..origin/dev -- services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora support/sidecars/AG-FE-ID-001 support/sidecars/AG-BE-ID-003 support/sidecars/AG-XR-003 execute-plans scripts/test_agora_compat_manifest.py docs/reviews` | `docs/reviews/2026-06-21-ag-xr-002a-claude-review.md`. |
| `git diff --name-only 285a6d6002da982f029d0b8b7447f95f10efc09b..origin/dev -- services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora support/sidecars/AG-FE-ID-001 support/sidecars/AG-BE-ID-003 support/sidecars/AG-XR-003 execute-plans scripts/test_agora_compat_manifest.py` | `support/sidecars/AG-XR-003/AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14.md`. |
| `git diff --name-only 60e3e18c466a0b3b4d28d8a128f28156e42743cd..origin/dev -- support/sidecars/AG-XR-002A` | `support/sidecars/AG-XR-002A/AG-XR-002A-SIDECAR-BFF-HANDOFF.md`. |
| `git diff --name-only e5f20720bc5c0fa7eb1e03972db838eb8098b241..origin/dev -- services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora support/sidecars/AG-FE-ID-001 support/sidecars/AG-BE-ID-003 support/sidecars/AG-XR-003 execute-plans scripts/test_agora_compat_manifest.py` | Empty output for this checked handoff pathset. |
| `git diff --name-only f6b61c6d2046926819adf8bd750865397c8a8f7f..origin/dev -- services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora support/sidecars/AG-FE-ID-001 support/sidecars/AG-BE-ID-003 support/sidecars/AG-XR-003 execute-plans scripts/test_agora_compat_manifest.py` | `docs/contracts/agora/dev-compatibility-manifest.json`; `execute-plans/scripts/contract-drift-check.mjs`; `execute-plans/scripts/generate-agora-types.mjs`; `execute-plans/src/lib/bff-v1/agora/types.ts`; `scripts/test_agora_compat_manifest.py`. |
| `git diff --name-only 7cfa8a4f84c6aced1e4b66c661fd9d7f78779e2c..origin/dev -- services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora support/sidecars/AG-FE-ID-001 support/sidecars/AG-BE-ID-003 support/sidecars/AG-XR-003 execute-plans scripts/test_agora_compat_manifest.py` | `docs/contracts/agora/dev-compatibility-manifest.json`; `execute-plans/scripts/contract-drift-check.mjs`; `execute-plans/scripts/generate-agora-types.mjs`; `execute-plans/src/lib/bff-v1/agora/types.ts`; `scripts/test_agora_compat_manifest.py`; `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-11.md`. |
| `git diff --name-only b3b5b1c3c3cdb37121dd6cc4d3c8f3634cc75c56..origin/dev -- services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora support/sidecars/AG-FE-ID-001 support/sidecars/AG-BE-ID-003 support/sidecars/AG-XR-003 execute-plans scripts/test_agora_compat_manifest.py` | `docs/contracts/agora/dev-compatibility-manifest.json`; `execute-plans/scripts/contract-drift-check.mjs`; `execute-plans/scripts/generate-agora-types.mjs`; `execute-plans/src/lib/bff-v1/agora/types.ts`; `scripts/test_agora_compat_manifest.py`; `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-11.md`; `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-21-REVIEW.md`; `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-21.md`; `support/sidecars/AG-XR-003/AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-13.md`; `support/sidecars/AG-XR-003/AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14.md`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-22` | Active `in_progress`, owner `Codex2`, reviewer `Claude`, support-only artifact. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-21` | Archived `done`; closeout PR #1949 merged at `7cfa8a4f`; support-only scope remained unchanged. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001` | Parent `todo`, owner `Claude`, reviewer `Codex`, depends on `AG-FE-000` and `AG-BE-ID-003`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003` | Parent dependency still `blocked`, waiting for `Claude`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` | Archived `done`; closeout PR #1950 merged at `bfb6b1c6`; review notes keep parent AG-BE-ID-003 blocked. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-13` | Archived `done`; pre-AG-XR-002A support packet kept parent compatibility gated. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14` | Active `review`; support-only followup-14 packet merged in PR #1956 at `e7d75a1161545aa0c2f696882e45fc13ff4bdf35`; parent remains gated on execute-plans PR #63/runtime pin/deployment gate disposition. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-002A` | Archived `done`; PR #1952 implementation merge and PR #1957 closeout artifact merge recorded. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-002A-SIDECAR-BFF-HANDOFF` | Archived `done`; PR #1959 closeout merge recorded at `4588fe174e8305bf37ebb2ee78b9fa578a4d31ea`. |
| `python3 scripts/agora_compat_manifest.py verify --allow-pending --manifest docs/contracts/agora/dev-compatibility-manifest.json` | `ok docs/contracts/agora/dev-compatibility-manifest.json`. |
| `python3 scripts/agora_compat_manifest.py deployment-gate --manifest docs/contracts/agora/dev-compatibility-manifest.json` | Exit 1; expected fail-closed errors: compatibility status must be compatible, frontend runtime commit is a placeholder, and blocking reasons must be empty. |
| `npm --prefix execute-plans run contract:drift` | Passed; 20 bundle digests, 17 schemas, 96 OpenAPI operations. |
| `python3 -m pytest scripts/test_agora_compat_manifest.py -v` | `4 passed in 1.45s`. |
| `gh pr view 63 --repo ajoe734/execute-plans --json ...` | PR #63 remains `OPEN`, `UNSTABLE`, head `e1cb9125c87d9ace0adf3dd9f17f24ff0542d9c5`; integration-gate check run `27877483718` is still `FAILURE`. |
| `git -C /home/lupin/code/execute-plans fetch origin --prune` | Completed successfully. |
| `git -C /home/lupin/code/execute-plans symbolic-ref refs/remotes/origin/HEAD` | `refs/remotes/origin/main`. |
| `git -C /home/lupin/code/execute-plans rev-parse origin/main` | `7b2f17c4dee8dcafe62c2295504df03aed0ae16e`. |
| `git -C /home/lupin/code/execute-plans rev-parse origin/dev` | `7aa4917272212452fe5e4dc99bf2d76fe48eacfd`. |
| `git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/main -- ...` | Only `package.json`, `src/agora/pages/AskPersonas.tsx`, and `src/lib/bff/agora.ts` were present from the probed list. |
| `git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/dev -- ...` | Only `package.json`, `src/agora/pages/AskPersonas.tsx`, `src/lib/bff-v1/agora/types.ts`, and `src/lib/bff/agora.ts` were present from the probed list. |
| `git -C /home/lupin/code/execute-plans status -sb` | `## main...origin/main [ahead 2, behind 467]`. |
| `python3 -m pytest services/control-plane/bff/tests/test_agora_router.py integrations/openclaw/test_persona_agent_sync.py services/control-plane/bff/tests/test_agora_identity_scope.py` | `35 passed in 15.58s`. |
| `python3 scripts/agora_schema_bundle.py --verify` | OK for frozen Agora schemas, capability manifest, and `openapi/agora_v1.openapi.yaml`. |
| `python3 -c "import pathlib, yaml; yaml.safe_load(pathlib.Path('services/control-plane/openapi/agora_v1_1.openapi.yaml').read_text())"` | Passed with no output. |
| `rg -n "ServantSessionCreateRequest\|servant/sessions\|session_type\|sessionType\|quick_ask\|OPENCLAW_UPSTREAM_DEGRADED\|createServantSession" ...` | Found servant session paths in OpenAPI v1.1 and legacy/main.py session terms; no accepted servant-session BFF runtime implementation or session-level `OPENCLAW_UPSTREAM_DEGRADED` runtime hit was identified in checked paths. |
| `rg -n "@router\.(get\|post)\|/bff/agora/servant\|ensure\|Idempotency-Key\|X-Request-Id\|DEPENDENCY_UNAVAILABLE" ...` | Confirmed concrete `/servant/ensure` route/tests, required headers, and current `DEPENDENCY_UNAVAILABLE` mapping; also confirmed the stale 501 test header comment remains. |

## 13. Reviewer Handoff

Claude should review this packet as support-only. The review basis is:

1. Followup-21 closed through PR #1949 at `7cfa8a4f`; its packet PR #1945
   merged at `97cfbdd5`.
2. Current dev base is `52a2d5a8`, after this task branch merged current
   `origin/dev` into PR #1955 again.
3. The checked pathset delta after followup-21 closeout is AG-BE-ID-003
   followup-11 closeout support plus AG-XR-002A manifest/type-generation/test
   updates plus AG-XR-003 followup-14 support, AG-XR-002A closeout review and
   sidecar closeout material, and AG-BE-SW-001 v1.2 design closure input.
4. The checked pathset delta from followup-21's observation base `b3b5b1c3`
   is AG-FE-ID-001 followup-21, AG-BE-ID-003 followup-11, AG-XR-003 followup-13
   and followup-14 support, plus AG-XR-002A manifest/type-generation/test
   updates and closeout review material.
5. Parent `AG-FE-ID-001` remains `todo`.
6. `AG-BE-ID-003` remains blocked; `AG-BE-ID-003` followup-11 closeout
   does not unblock session runtime.
7. `bfb6b1c6..f6b61c6d` is unrelated AG-FE-DB-002 support material and has no
   AG-FE-ID-001 BFF/frontend implication.
8. `f6b61c6d..e5f20720` is AG-XR-002A contract/type refresh material:
   manifest verify now passes and contract drift passes, but deployment gate
   still fails closed.
9. `e5f20720..60e3e18c` is unrelated AG-FE-DB-002 review support material and
   has no AG-FE-ID-001 BFF/frontend implication.
10. `60e3e18c..285a6d60` is AG-XR-002A sidecar support handoff material and
    has no AG-FE-ID-001 runtime/frontend shell implication.
11. `285a6d60..e7d75a11` is AG-XR-003 followup-14 support material; it keeps
    compatibility closeout gated on execute-plans PR #63/runtime pin/deployment
    gate disposition.
12. `e7d75a11..f8a8dd73` is AG-XR-002A closeout review material; it archives
    AG-XR-002A as `done` but does not merge execute-plans PR #63 or pass the
    deployment gate.
13. `f8a8dd73..4588fe17` includes AG-XR-002A sidecar closeout and unrelated
    AG-FE-DB-002 closeout material; no AG-FE-ID-001 shell/client implication.
14. `4588fe17..52a2d5a8` is AG-BE-SW-001 v1.2 additive design closure input;
    it explicitly keeps v1/v1.1 bundles immutable and does not alter current
    AG-FE-ID-001 v1.1 readiness.
15. AG-XR compatibility/deployment readiness remains gated by execute-plans PR
   #63, which is still open/unstable with integration-gate failure.
16. Execute-plans target files `AgoraApp.tsx`, `identity.ts`, and `servant.ts`
   remain absent from both checked frontend remote trees.
17. Focused BFF/OpenClaw pytest, schema/OpenAPI checks, manifest verify,
    contract drift, and manifest pytest are green; deployment gate still fails
    closed for pending compatibility.
18. The packet does not change canonical truth, BFF runtime code, OpenAPI,
   capability manifests, governance, or frontend source.

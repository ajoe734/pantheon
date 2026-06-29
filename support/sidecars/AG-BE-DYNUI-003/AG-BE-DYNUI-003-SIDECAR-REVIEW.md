# AG-BE-DYNUI-003 Sidecar Review Packet

| Field | Value |
|---|---|
| Task ID | `AG-BE-DYNUI-003-SIDECAR-REVIEW` |
| Helper kind | `review_packet` |
| Parent task | `AG-BE-DYNUI-003` - Servant workspace generator and safe widget validator |
| Parent owner / reviewer | `Codex2` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Date | `2026-06-29` |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

## Purpose

This packet supports review of `AG-BE-DYNUI-003` by consolidating the merged
implementation evidence, focused validation results, and reviewer attention
points for the V11 Trading Room servant workspace generator slice.

It is support-only. It does not modify L1 canonical truth, core contract truth,
schema semantics, OpenAPI/generated types, BFF runtime behavior, widget registry
truth, governance logic, frontend runtime, broker authority, RuntimeBinding, or
any capital-affecting surface.

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Sources Used

| Source | Relevance |
|---|---|
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DYNUI-003-SIDECAR-REVIEW` | Sidecar active state, owner/reviewer, helper kind, parent link, support-only artifact path. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DYNUI-003` | Parent active `review` state, merged PR evidence, owner/reviewer, dependency list. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DYNUI-001` | Upstream workspace proposal/workspace contract is archived `done`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DYNUI-002` | Upstream widget revision/version/rollback contract is archived `done`. |
| `.orchestrator/task-briefs/ag_be_dynui_003.md` | Parent scope, design-pack constraints, non-static UI requirement, no-code/no-order safety boundary. |
| `support/sidecars/AG-BE-DYNUI-003/AG-BE-DYNUI-003-SIDECAR-ACCEPTANCE.md` | Acceptance checklist and dependency map for the parent generator task. |
| `support/sidecars/AG-BE-DYNUI-003/AG-BE-DYNUI-003-IMPLEMENTATION-EVIDENCE.md` | Owner evidence: sources read, delivered scope, non-goals, and validation commands. |
| GitHub PR `#2585` | Parent implementation merge facts and required check results. |
| Commit `b72678e8` | Parent task commit and commit-message scope boundary. |
| Merge commit `ef246b2d` | Parent PR merge commit on `dev`. |
| `integrations/openclaw/skills/agora/trading_room_workspace/skill.py` | Generator boundary, registry validation, fallback/component-task metadata, personalization filtering, data availability assembly. |
| `integrations/openclaw/skills/agora/trading_room_workspace/test_skill.py` | Skill-level regression coverage. |
| `services/control-plane/bff/agora/trading_room/router.py` | Proposal route wiring, route-level `_validate_view` / `_validate_widget` gate, scoped proposal readback, accept route composition. |
| `services/control-plane/bff/agora/trading_room/store.py` | Proposal generation metadata persistence/readback. |
| `services/control-plane/bff/agora/trading_room/test_trading_room.py` | Focused BFF coverage for proposal generation, metadata, accept, scope, registry validation, revision boundary, and safety guards. |

## Parent Delivery Facts

| Item | Evidence |
|---|---|
| Parent implementation PR | `https://github.com/ajoe734/pantheon/pull/2585` |
| Parent PR state | `MERGED` into `dev` at `2026-06-29T01:35:24Z` |
| Parent merge commit | `ef246b2da4d6d48f2fd47ca55dc2465415c71efd` |
| Parent task commit | `b72678e87fd85fa594dec3e54d7517ab6cfe4a53` - `AG-BE-DYNUI-003: add servant workspace generator` |
| Parent changed files | 8 files: parent task brief, new generator package/tests, Trading Room router/store/tests, implementation evidence. |
| Parent commit owned layer | Backend servant generator integration, proposal metadata persistence, focused generator/BFF validation. |
| Parent commit non-goals | V11 schema field semantics, OpenAPI/generated frontend types, frontend runtime, visual parity, broker/runtime/capital authority. |
| Required checks observed on PR `#2585` | Commit trailers, Runtime mirror guard, Smoke acceptance, and Forward to orchestrator all concluded `SUCCESS`. |
| Parent active state | `review`; next says implementation merged in PR `#2585` and reviewer approval is needed before owner closeout. |

## Review Matrix

| Area | Evidence observed | Sidecar assessment |
|---|---|---|
| Generator boundary | `generate_trading_room_workspace_proposal` accepts `WorkspaceGenerationInput`, a controlled `view_factory`, registry data, and a widget validator. It returns only proposal-shaped data plus metadata. | Supports the servant-generator boundary without introducing frontend code generation. |
| Complete V11 view set | `WINNER_BRANCH_VIEW_IDS` contains the seven required views; the generator checks exact view id order; route tests assert the seven V11 ids are returned. | Parent covers the full Winner Branch workspace shape expected by the acceptance packet. |
| Registry and route validation | The skill checks widget type, active registry status, renderer, chart kind, data source, interactions, and forbidden executable content; the BFF route also re-runs `_validate_view`. | Two validation layers exist before a preview proposal is stored or returned. |
| Unsupported renderers | The skill can apply `renderer_fallbacks` or return `componentTaskRequests`; tests cover both paths. | Capability fallback behavior is proven at skill level. The current BFF route uses only the controlled supported Winner Branch view factory and does not pass route-level renderer fallback config. |
| Evidence and freshness | `evidenceRefs` and `dataFreshness` are accepted by the route, passed to the generator, reflected in proposal `dataAvailability`, persisted as generator meta, and read back by `GET`. | Evidence/freshness preservation is covered in route tests. |
| Personalization safety | The skill drops unsafe keys/content patterns and records a warning; route tests show `javascript` is removed while safe `density` remains. | Safe personalization behavior is covered. |
| Readiness gate | The route accepts `tradingRoomReady` and the skill returns `blocked` when it is false. | Code has a blocking path, but readiness is currently request-body driven with default `true`; this packet did not find store-backed StrategySpec readiness lookup evidence. |
| Proposal persistence and scoping | `upsert_workspace_proposal` stores tenant/user scope and generation meta; proposal loading checks scope before returning. | Scoped persistence exists. Focused tests cover cross-user workspace read; proposal cross-user read is enforced in code but not separately asserted by a named test. |
| Accept-to-workspace | Route tests create a proposal, read it back, accept it, and assert active workspace materialization with seven views and schema validation. | Parent composes with `AG-BE-DYNUI-001` accept/workspace behavior. |
| Revision boundary | Tests reject servant direct widget patch with `servant_direct_widget_patch_not_allowed`; widget revision proposal tests preserve before/proposed specs and version records. | Parent preserves the `AG-BE-DYNUI-002` revision/version boundary. |
| No order/capital/runtime/code authority | Implementation evidence and sidecar grep found only guard/test strings for forbidden code patterns; no `place_order`, live enablement, RuntimeBinding write, management route, iframe, or arbitrary executable UI path was introduced in reviewed paths. | Safety boundary is preserved for this backend slice. |
| Local validation | Re-ran focused tests in this worktree: generator skill, Trading Room BFF tests, and Agora context bundle tests. | `65 passed in 35.56s`. |

## Reviewer Attention Points

1. **Readiness source is still shallow.** The parent brief says generation starts
   from a ready StrategySpec version. The implementation carries a
   `tradingRoomReady` boolean through the request and generator, and blocks when
   false, but this packet did not find a store-backed StrategySpec readiness
   lookup or stale-version lookup in the reviewed diff. If parent acceptance
   requires readiness to be proven from persisted Strategy Workshop state rather
   than a caller-provided flag, request a follow-up or mark it as residual risk.

2. **Unsupported renderer handling is skill-proven, not route-configured.** The
   skill has supported fallback and component-task-request behavior with tests.
   The current route passes the controlled Winner Branch view factory and no
   explicit `renderer_fallbacks`; therefore route-level evidence proves the
   supported-widget path and metadata persistence, while unsupported renderer
   fallback remains a generator-level capability.

3. **OpenAPI/generated types remain downstream.** The parent did not change
   OpenAPI/generated frontend types, matching the commit non-goals and the
   `AG-XR-DYNUI-001` boundary.

4. **Frontend preview, grid editing, visual parity, and E2E remain downstream.**
   This backend packet should not be used as proof for `AG-FE-DYNUI-002`,
   `AG-FE-DYNUI-003`, `AG-FE-DYNUI-004`, or `AG-E2E-DYNUI-001`.

## Verification Performed

Commands used while preparing this packet:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DYNUI-003-SIDECAR-REVIEW
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DYNUI-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DYNUI-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DYNUI-002
gh pr view 2585 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit,url,title,headRefName,baseRefName,statusCheckRollup
git show --stat --oneline --find-renames --find-copies b72678e87fd85fa594dec3e54d7517ab6cfe4a53
git show --stat --oneline --find-renames --find-copies ef246b2da4d6d48f2fd47ca55dc2465415c71efd
python3 -m pytest integrations/openclaw/skills/agora/trading_room_workspace/test_skill.py services/control-plane/bff/agora/trading_room/test_trading_room.py integrations/openclaw/adapter/test_agora_context_bundle.py -q
rg -n "dangerouslySetInnerHTML|eval\(|new Function|place_order|enable_live|change_capital_binding|write_runtime_binding|open_management_route|rawHtml|javascript:|<script|iframe" integrations/openclaw/skills/agora/trading_room_workspace services/control-plane/bff/agora/trading_room
```

Observed results:

- Branch was `task/AG-BE-DYNUI-003-SIDECAR-REVIEW`; remote was
  `origin https://github.com/ajoe734/pantheon.git`.
- The only pre-existing dirty file was the task-scoped untracked brief
  `.orchestrator/task-briefs/ag_be_dynui_003_sidecar_review.md`.
- PR `#2585` was merged to `dev`; visible Commit trailers, Runtime mirror
  guard, Smoke acceptance, and Forward to orchestrator checks all reported
  `SUCCESS`.
- Focused pytest passed: `65 passed in 35.56s`.
- Forbidden-surface grep returned only guard/test strings:
  forbidden-key/pattern lists in `skill.py`, an older personalization filter in
  `router.py`, and test payloads asserting `<script>` rejection. No live
  order/capital/runtime/Management path was introduced in reviewed paths.

## Reviewer Handoff

To `Codex2`, sidecar reviewer:

Please review this support-only packet for:

1. Accuracy of parent PR `#2585` merge facts, changed-file scope, and check
   summary.
2. Accuracy of the evidence matrix against the current merged generator,
   router, store, and tests.
3. Whether the readiness-source and route-level fallback caveats are framed
   strongly enough for parent review of `AG-BE-DYNUI-003`.
4. Whether the packet stays support-only and avoids changing canonical truth or
   parent status.

If accurate, approve `AG-BE-DYNUI-003-SIDECAR-REVIEW` and return it to `Codex`
for owner closeout. Parent `AG-BE-DYNUI-003` remains a separate review decision:
this sidecar does not replace the parent reviewer approval.

Suggested approval command:

```bash
AI_NAME=Codex2 REVIEW_FILE=support/sidecars/AG-BE-DYNUI-003/AG-BE-DYNUI-003-SIDECAR-REVIEW.md \
  ./scripts/ai-status.sh approve AG-BE-DYNUI-003-SIDECAR-REVIEW \
  "Review packet accepted; support artifact accurately summarizes AG-BE-DYNUI-003 generator evidence, validation, safety boundary, and residual readiness/fallback caveats without changing canonical truth."
```

If changes are required:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh reopen AG-BE-DYNUI-003-SIDECAR-REVIEW \
  "Describe the exact review packet corrections needed."
```

## Scope Boundary

This sidecar changes only support material:

```text
support/sidecars/AG-BE-DYNUI-003/AG-BE-DYNUI-003-SIDECAR-REVIEW.md
```

It intentionally does not:

- approve, reopen, or finalize the parent task;
- modify L1/L2 canonical documents;
- modify BFF route behavior, schema, store, widget registry, OpenClaw runtime,
  governance, frontend runtime, or generated types;
- create broker order, live enablement, capital-binding, RuntimeBinding, or
  Management-plane authority.

Prepared by `Codex` for the `AG-BE-DYNUI-003-SIDECAR-REVIEW` support slice.

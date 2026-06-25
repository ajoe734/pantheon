# AG-FE-SW-003 - Acceptance Packet and Dependency Map

**Sidecar kind:** acceptance_packet
**Sidecar task:** AG-FE-SW-003-SIDECAR-ACCEPTANCE
**Parent task:** AG-FE-SW-003 - Version comparison and readiness UI
**Parent owner:** Claude2
**Parent reviewer:** Codex
**Prepared by:** Codex
**Sidecar reviewer:** Claude2
**Date:** 2026-06-22

## Purpose

This packet gives the parent owner and reviewer a support-only acceptance
checklist for AG-FE-SW-003. It does not approve the parent task, mutate
canonical truth, change schemas, alter BFF/runtime behavior, or edit the
frontend implementation. The parent owner still owns refresh/merge/closeout,
and the parent reviewer still owns the final AG-FE-SW-003 review decision.

## Sidecar Scope Boundary

This sidecar may create support material only:

- `support/sidecars/AG-FE-SW-003/AG-FE-SW-003-SIDECAR-ACCEPTANCE.md`

It must not change:

- L1 canonical architecture or policy docs.
- `services/control-plane/specs/agora/**` schema truth.
- `services/control-plane/openapi/**` contract truth.
- `execute-plans/**` implementation or tests.
- runtime, registry, governance, order, RuntimeBinding, or capital-binding code.

## Current Parent State

State checked on 2026-06-22 from `ai-status.sh`, `git`, and GitHub PR metadata.

| Item | State | Acceptance consequence |
|---|---|---|
| Parent task `AG-FE-SW-003` | Active `review` in `ai-status.json`; local generated brief on the parent branch says owner added tests and handed back for review. | Treat task as not closed. Parent still needs reviewer decision and owner closeout after the final PR state is merged. |
| PR #2257 | `MERGED` into `dev` at 2026-06-22T12:01:37Z; merge commit `77feafe74ad1b0db37fbe39fca5dea8a10d04821`. | Main implementation is already on `dev`. Review should inspect this merge for behavior and scope. |
| PR #2265 | `OPEN`; head `41f5431f160f49004a2bbc9fe424000e5f8b3ba5`; checks green; auto-merge enabled; `mergeStateStatus: BEHIND`; no merge commit yet. | VersionCompareCard test coverage is not yet on `dev`. Parent owner must refresh/merge or provide equivalent merged test coverage before claiming UI-test acceptance closed. |
| Local `origin/dev` delta after #2257 | No changes to AG-FE-SW-003 parent implementation files or `support/sidecars/AG-FE-SW-003` after merge `77feafe7`. | The implementation-review baseline remains PR #2257 plus pending test PR #2265. |

## Parent Implementation Surface

PR #2257 added or changed:

| File | Role |
|---|---|
| `.orchestrator/task-briefs/ag_fe_sw_003.md` | Task brief record. |
| `execute-plans/src/agora/components/VersionCompareCard.tsx` | New multi-version comparison card. |
| `execute-plans/src/agora/components/WorkshopCardRenderer.tsx` | Routes `version_compare` cards to `VersionCompareCard` and enhances `readiness_gate` rendering. |
| `execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx` | Reads readiness, refreshes on readiness SSE events, and gates the "Add to Trading Room" CTA. |

PR #2265 adds:

| File | Role |
|---|---|
| `execute-plans/src/agora/components/VersionCompareCard.test.tsx` | 26 UI tests for VersionCompareCard rendering, predicted/observed separation, decision authority, empty sections, multi-candidate grouping, and continue-discussion callback. |
| `.orchestrator/task-briefs/ag_fe_sw_003.md` | Parent task brief status update. |

## Dependency Map

| Dependency | Current state | Relevant delivered surface | How AG-FE-SW-003 depends on it |
|---|---|---|---|
| `AG-FE-SW-002` | Archived `done`; PR #2252 merged. | Conversation/result cards, `StrategyCompletenessRail`, workshop card renderer foundation, SSE helper usage, 41 FE tests reported. | AG-FE-SW-003 extends the existing workshop card/rail surface instead of inventing a new page or renderer architecture. |
| `AG-XR-OPENAPI-004` | Archived `done`; PR #2072 merged. | Additive Agora v1.3 OpenAPI, v4 schemas, capability manifest, bundle index hash chain; frozen v1-v1.2 untouched. | Version comparison/readiness UI must consume the v4 schema/route family without new fields, routes, enums, or allowlist expansion. |
| `AG-BE-SW-002` | Archived `done`; PR #2080 merged. | StrategySpec patch/version linkage and version comparison builder; 65 unit tests; `decision_authority=trader`; no Registry duplication. | Parent UI data source for version diff semantics. UI must preserve evidence class and decision-authority semantics from this backend slice. |
| PR #2265 | Open, checks green, behind `dev`. | Dedicated `VersionCompareCard.test.tsx` coverage. | Required dependency for closing the VersionCompareCard UI-test gap identified by the prior sidecar review. |

## Authority and Contract References

Review against these repository sources, not this packet as canonical truth:

| Reference | Acceptance use |
|---|---|
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/01_strategy_versioning_patch_readiness.md` | A5 version comparison and A6 readiness state machine. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/05_workshop_card_contracts.md` | E12 `version_compare`, E13 `readiness_gate`, and card source rules. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/08_openapi_v1_3_delta.yaml` | `compareWorkshopVersions`, `getWorkshopReadiness`, and `reassessWorkshopReadiness` route contracts. |
| `services/control-plane/specs/agora/v4/version_compare.schema.json` | VersionCompare schema field set and evidence classes. |
| `services/control-plane/specs/agora/v4/strategy_readiness.schema.json` | StrategyReadinessAssessment gate/status field set. |
| `services/control-plane/specs/agora/v4/workshop_card.schema.json` | Typed `version_compare` and `readiness_gate` card payload references. |

## Acceptance Checklist for Parent Review

### Gate 1 - Scope and contract discipline

- [ ] Parent changed only the implementation/test/task-brief surfaces listed above.
- [ ] No L1 canonical docs, OpenAPI files, v4 schemas, bundle indexes, capability manifests, BFF routes, Registry truth, governance code, RuntimeBinding code, or order/capital paths changed.
- [ ] No route, enum, payload field, widget type, score, readiness gate, or evidence class was invented in the frontend.
- [ ] Frozen Agora v1/v1.1/v1.2 OpenAPI and bundle files remain untouched.

### Gate 2 - Version comparison behavior

- [ ] `VersionCompareCard` renders one base version and one to four candidate versions without assuming only one candidate.
- [ ] Field, metric, risk, and readiness diffs are rendered from the payload shape declared in the v4 schemas.
- [ ] Metric rows preserve evidence class: `predicted`, `backtested_in_sample`, `backtested_oos`, and `paper_observed`.
- [ ] Predicted effects are visually separated from observed/backtested metrics and rendered with distinct treatment.
- [ ] Servant recommendations are displayed as recommendations only, with visible trader decision authority.
- [ ] Recommendation limitations remain visible when present.

### Gate 3 - Readiness gate UI

- [ ] The UI renders all three gates: `preliminary_research`, `full_validation`, and `trading_room`.
- [ ] Gate states cover `not_assessed`, `blocked`, `conditional`, `ready`, and `stale`.
- [ ] Missing and partial requirements are visible with hard/soft distinction.
- [ ] `hard_blockers`, `staleness_reasons`, and `highest_ready_gate` are rendered when provided.
- [ ] Conditional readiness is not treated as Trading Room readiness.
- [ ] Readiness payload comes from the workshop readiness route/card projection, not arbitrary LLM markdown parsing.

### Gate 4 - Trading Room CTA safety

- [ ] The "Add to Trading Room" button is disabled when readiness is absent.
- [ ] The button is disabled when `highest_ready_gate` is lower than `trading_room`.
- [ ] The button remains disabled when no `onAddToTradingRoom` handler is provided, even if the readiness value says `trading_room`.
- [ ] Disabled state uses `disabled`, `aria-disabled`, and an operator-visible reason.
- [ ] Enabling the button must not imply order routing, RuntimeBinding creation, broker placement, canary/live execution, or capital binding. Agora remains request-only for governed handoff.

### Gate 5 - Stream and refresh behavior

- [ ] `workshop.readiness.updated` refreshes readiness.
- [ ] Version/card-related workshop events refresh cards without requiring a full page reload.
- [ ] The implementation composes with the `AG-FE-SW-002` card renderer/rail pattern instead of duplicating a parallel workshop UI path.

### Gate 6 - UI test coverage

- [ ] PR #2265, or an equivalent merged commit, lands `VersionCompareCard.test.tsx` on `dev`.
- [ ] VersionCompareCard tests cover predicted-vs-observed visual separation, decision authority, diff sections, empty optional sections, multi-candidate grouping, and callback behavior.
- [ ] Reviewer should explicitly decide whether CTA readiness gating has sufficient UI test coverage. The current local `StrategyWorkshopPage.test.tsx` does not exercise the disabled/enabled CTA gate states; require tests or record a narrow follow-up before parent closeout.
- [ ] Focused Agora UI tests pass after the final parent branch refresh.

## Review Decision Hints

Use these outcomes when reviewing the parent task:

| Finding | Recommended action |
|---|---|
| #2265 is still open or behind `dev`. | Do not mark parent UI-test acceptance complete. Ask owner to refresh/merge #2265 or equivalent. |
| VersionCompareCard visual separation or decision authority tests are absent from merged code. | Reopen or block parent review on Gate 6. |
| CTA readiness gating remains untested and reviewer judges this explicit acceptance item must have automated coverage. | Reopen or require a small parent test follow-up before closeout. |
| Frontend adds any new schema field, route, enum, readiness gate, or order/capital path. | Reopen; this violates parent and sidecar boundary rules. |
| All gates pass, #2265 or equivalent tests are merged, and focused Agora UI validation passes. | Parent reviewer can approve AG-FE-SW-003 and return to owner for closeout. |

## Evidence Commands Run

Commands used while preparing this packet:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-003-SIDECAR-ACCEPTANCE
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-004
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-002
git fetch origin dev --quiet
gh pr view 2257 --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,title,url,commits,statusCheckRollup
gh pr view 2265 --json number,state,mergeStateStatus,reviewDecision,autoMergeRequest,mergedAt,mergeCommit,url,headRefOid,headRefName,baseRefName,statusCheckRollup
git fetch origin task/AG-FE-SW-003 --quiet
git show --stat --oneline --summary 77feafe74ad1b0db37fbe39fca5dea8a10d04821
git show --stat --oneline --summary 41f5431f160f49004a2bbc9fe424000e5f8b3ba5
git diff --name-only 77feafe74ad1b0db37fbe39fca5dea8a10d04821..origin/dev -- execute-plans/src/agora/components/VersionCompareCard.tsx execute-plans/src/agora/components/WorkshopCardRenderer.tsx execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.test.tsx execute-plans/src/agora/components/VersionCompareCard.test.tsx .orchestrator/task-briefs/ag_fe_sw_003.md support/sidecars/AG-FE-SW-003
git diff --name-only 77feafe74ad1b0db37fbe39fca5dea8a10d04821..origin/task/AG-FE-SW-003 -- execute-plans/src/agora/components/VersionCompareCard.test.tsx .orchestrator/task-briefs/ag_fe_sw_003.md
```

Parent implementation tests were not rerun by this sidecar because this task is
support-only and does not change `execute-plans` code. The packet relies on
GitHub check metadata and source inspection for dependency state; the parent
owner/reviewer should run the focused Agora UI validation after refreshing the
parent branch.

## Handoff

Claude2 should review this packet for sidecar accuracy and scope discipline.
If approved, use it as a parent-owner checklist for refreshing/merging PR
#2265 and returning AG-FE-SW-003 to Codex for the final parent review.

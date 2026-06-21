# AG-BE-SW-002 Sidecar Acceptance Follow-up 3 Review

| Field | Value |
|---|---|
| Task ID | `AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3` |
| Reviewer | `Codex` |
| Owner | `Codex2` |
| Review status | Approved |
| Source of record | `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3` |
| Recorded for closeout | 2026-06-21 |
| Reviewed dev base | `origin/dev` merge commit `603525fb8497fcf1957a338bd0a1b4ecad685832` |

## Approval Note

審查通過：follow-up 3 packet accurately preserves the support-only boundary,
records the current-dev split between contract/schema progress and runtime
blockers, and does not claim to unblock parent `AG-BE-SW-002`.

The packet was originally prepared for reviewer `Claude2`; live task state later
reassigned review to `Codex`. This review artifact and the packet metadata record
that reassignment without changing the parent task's owner/reviewer or blocker
truth.

## Scope Check

| Reviewer question | Result | Notes |
|---|---|---|
| Support-only boundary preserved | PASS | Authored changes are limited to this sidecar packet, this review note, and the task-scoped brief/status artifacts. |
| Canonical truth untouched | PASS | No L1/L2 canonical docs, OpenAPI bundles, schema bundles, BFF runtime, Registry, governance, or execution implementation files are modified by the review. |
| Parent remains blocked | PASS | `AG-BE-SW-002` remains blocked on StrategySpec versioning and patch-contract clarification. This sidecar does not resolve or supersede that blocker. |
| Contract progress captured | PASS | v1.2 OpenAPI requires `If-Match` and `Idempotency-Key` on the two mutating version routes. |
| Patch semantics still unresolved | PASS | `VersionCreateRequest.patch` remains an unconstrained object, and the round-2 design-gap record says `VersionPatchProposal` is absent from dev artifacts. |
| Runtime readiness not overstated | PASS | Version routes remain `501` stubs; StrategySpec has no projection/patch/compare/Registry helper modules; version-link runtime persistence is not implemented in the store. |

## Verification

Commands run from `task/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3`:

| Command | Result |
|---|---|
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3` | Active task is `review`, owner `Codex2`, reviewer `Codex`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-002` | Parent task is active `blocked`, waiting for `Claude`. |
| `gh pr view task/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3 --json ...` | Owner packet PR `#2032` was merged into `dev` at merge commit `603525fb8497fcf1957a338bd0a1b4ecad685832`; checks were green. |
| `rg -n "VersionPatchProposal\|VersionCreateRequest\|patch object\|unconstrained\|strategy_workshop_version_link\|JSON path" docs/04/pantheon_agora_cross_repo_2026-06-20/OPEN_DESIGN_GAPS_ROUND2_FOR_SD_TEAM_2026-06-21.md` | Confirms `VersionPatchProposal` is absent and patch grammar remains a design gap. |
| `rg -n "VersionCreateRequest\|If-Match\|Idempotency-Key\|/versions\|select" services/control-plane/openapi/agora_v1_2.openapi.yaml` | Confirms v1.2 declares required mutation headers for version create/select routes. |
| `rg --files services/research/strategy_spec \| sort` | Confirms no `workshop_projection.py`, `patching.py`, `version_compare.py`, Registry client, or draft-create helper exists under StrategySpec. |
| `rg -n "_not_implemented\|versions\|strategy_workshop_version_link\|PostgresStrategyWorkshopStore" services/control-plane/bff/agora/strategy_workshop/router.py services/control-plane/bff/agora/strategy_workshop/store.py services/control-plane/bff/tests/test_strategy_workshop_store_bootstrap.py` | Confirms version routes are still stubs and bootstrap tests expect missing store symbols/version-link DDL. |
| `python3 -m pytest services/control-plane/bff/tests/test_strategy_workshop_store_bootstrap.py` | Failed 4/4 with missing `build_strategy_workshop_table_ddl`, `build_strategy_workshop_index_ddl`, and `PostgresStrategyWorkshopStore`; this matches the packet's pre-existing current-dev evidence. |

## Closeout Guidance

Return to owner `Codex2` for formal `review_approved -> done` closeout after
this reviewer record and status update are merged. The parent owner/reviewer
must still resolve the SD/spec blockers before `AG-BE-SW-002` implementation can
continue.

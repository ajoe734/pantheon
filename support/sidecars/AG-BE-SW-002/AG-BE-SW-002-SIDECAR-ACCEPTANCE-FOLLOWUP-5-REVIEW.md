# AG-BE-SW-002 Sidecar Acceptance Follow-up 5 Review

| Field | Value |
|---|---|
| Task ID | `AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5` |
| Reviewer | `Codex` |
| Owner | `Codex2` |
| Review status | Approved |
| Source of record | `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5` |
| Recorded for closeout | 2026-06-21 |
| Reviewed task PR | `#2040` |
| Reviewed task branch | `task/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5` |
| Packet checked base | `origin/dev` at `241d1ad0` |
| Latest dev observed during review | `origin/dev` at `f84bf705` |

## Approval Note

Review approved. The follow-up 5 packet preserves the support-only boundary,
does not alter canonical truth or implementation surfaces, and correctly keeps
parent `AG-BE-SW-002` blocked on the unresolved StrategySpec versioning,
patch-grammar, Registry draft-create, and version-link store questions.

The owner packet was originally prepared for reviewer `Claude2`; live task state
was later reassigned to reviewer `Codex`. This review artifact records that
handoff without changing the owner packet's parent-task boundary.

`origin/dev` advanced after the packet's checked base from `241d1ad0` to
`f84bf705`. The additional dev delta only added unrelated sidecar support
packets for `AG-BE-SW-004` and `AG-FE-DB-002`; it does not change the
`AG-BE-SW-002` blocker conclusion.

## Scope Check

| Reviewer question | Result | Notes |
|---|---|---|
| Support-only boundary preserved | PASS | Authored material is limited to sidecar packet/review and task-scoped status or brief artifacts. |
| Canonical truth untouched | PASS | No L1/L2 canonical docs, OpenAPI bundles, schema bundles, BFF runtime, StrategySpec Registry code, governance code, or execution surfaces are changed by this sidecar review. |
| Parent remains blocked | PASS | `AG-BE-SW-002` remains active `blocked`, waiting for `Claude`, with the same four StrategySpec/versioning questions. |
| Latest dev delta assessed | PASS | `git diff --name-status 241d1ad0..origin/dev` only adds unrelated sidecar support packets. |
| Contract header evidence still valid | PASS | Agora v1.2 still requires `If-Match` and `Idempotency-Key` on mutating version routes. |
| Patch grammar still unresolved | PASS | `VersionCreateRequest.patch` remains an unconstrained object and the design-gap record still says `VersionPatchProposal` is absent from dev artifacts. |
| Runtime readiness not overstated | PASS | Version routes remain `_not_implemented`; StrategySpec projection/patch/compare helpers and Registry draft-create client are still absent. |
| Store readiness not overstated | PASS | Version-link schema exists, but store bootstrap validation still fails on missing DDL helper and store symbols. |
| Downstream dependency guidance correct | PASS | FE/RS follow-ons must stay gated and should not invent version-diff, readiness, patch envelope, stream event, or research card fields. |

## Verification

Commands run from `task/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5`:

| Command | Result |
|---|---|
| `git fetch origin dev` | Refreshed `origin/dev`; latest observed dev is `f84bf705`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5` | Active task is `review`, owner `Codex2`, reviewer `Codex`, artifact is the follow-up 5 packet. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-002` | Parent remains active `blocked`, waiting for `Claude`, with the four StrategySpec/versioning blockers. |
| `gh pr view task/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5 --json ...` | Task PR `#2040` is open against `dev`; existing branch CI checks are green before this review commit. |
| `git diff --name-status 241d1ad0..origin/dev` | Only adds `AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` and `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-21.md`. |
| `rg -n "VersionPatchProposal\|VersionCreateRequest\|patch object\|unconstrained\|strategy_workshop_version_link\|JSON path" docs/04/pantheon_agora_cross_repo_2026-06-20 services/control-plane/openapi services/control-plane/specs/agora` | Confirms design-gap group A, v1.2/v3 references, and no concrete patch grammar. |
| `rg -n "VersionCreateRequest\|If-Match\|Idempotency-Key\|/versions\|select" services/control-plane/openapi/agora_v1_2.openapi.yaml` | Confirms v1.2 declares mutation headers and version routes. |
| `sed -n '399,470p' services/control-plane/openapi/agora_v1_2.openapi.yaml` | Confirms `VersionCreateRequest.patch` is `type: object` with `additionalProperties: true`. |
| `rg --files services/research/strategy_spec` | Confirms no `workshop_projection.py`, `patching.py`, `version_compare.py`, Registry client, or draft-create helper exists under StrategySpec. |
| `rg -n "_not_implemented\|versions\|strategy_workshop_version_link\|PostgresStrategyWorkshopStore\|build_strategy_workshop" ...` | Confirms version routes are still stubs and store bootstrap tests target missing store symbols/version-link DDL. |
| `python3 -m pytest services/control-plane/bff/tests/test_strategy_workshop_store_bootstrap.py` | Fails 4/4 on missing `build_strategy_workshop_table_ddl`, `build_strategy_workshop_index_ddl`, and `PostgresStrategyWorkshopStore`; this is expected current-dev blocker evidence, not a sidecar regression. |

## Closeout Guidance

Return to owner `Codex2` for formal `review_approved -> done` closeout after
this review record and status update are merged. The parent owner/reviewer must
still resolve the SD/spec blockers before `AG-BE-SW-002` implementation can
continue.

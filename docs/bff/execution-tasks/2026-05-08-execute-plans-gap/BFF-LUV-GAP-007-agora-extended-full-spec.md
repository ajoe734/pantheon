# BFF-LUV-GAP-007 - Agora Extended And FULL-Spec Route Reconciliation

Priority: P2

Area: Long-tail Agora and historical FULL spec coverage

## Goal

Reconcile and implement or explicitly supersede the extended Agora route families found by the full `.lovable` scan.

## Active Source-Referenced Missing Routes

- `POST /bff/agora/committee/{sessionId}/evidence-pack`
- `POST /bff/agora/committee/{sessionId}/evidence-pack/files`
- `POST /bff/agora/persona-lab/{draftId}/actions/submit-commit`

## FULL-Spec Route Families To Reconcile

- `/bff/agora/alerts`
- `/bff/agora/channels`
- `/bff/agora/committee-sessions`
- `/bff/agora/decision-journal`
- `/bff/agora/evaluations`
- `/bff/agora/incoming`
- `/bff/agora/persona-drafts`
- `/bff/agora/skill-drafts`
- `/bff/agora/research-tasks`
- `/bff/agora/market-notes`
- `/bff/agora/markets`
- `/bff/agora/assets/{id}/context`
- `/bff/agora/personas/available`
- `/bff/agora/handoffs`

## Implementation Notes

- Do not blindly implement stale historical routes if a newer active route supersedes them.
- Every route family must be marked in the registry as implemented, superseded, or deferred with a follow-up task.
- Current source-referenced routes are higher priority than historical FULL-only routes.

## Acceptance Criteria

- Active source-referenced routes are non-404 and tested.
- FULL-only families have explicit registry dispositions.
- Any implemented extended route returns frontend-ready DTOs and final error envelopes.

## Delivered Route Disposition

### Implemented Active Source Routes

- `POST /bff/agora/committee/{sessionId}/evidence-pack`
  - Creates or refreshes a `CommitteeEvidencePack` DTO backed by the local BFF overlay.
  - Validates auth, idempotency, and committee session existence; errors use the final BFF envelope.
- `POST /bff/agora/committee/{sessionId}/evidence-pack/files`
  - Attaches validated evidence file metadata to the pack and returns the updated pack plus uploaded file DTOs.
  - Enforces FULL-spec file count, MIME, size, and required metadata constraints.
- `POST /bff/agora/persona-lab/{draftId}/actions/submit-commit`
  - Converts the persona-lab commit into a submitted `trainer_feedback_to_persona_update` Agora handoff and command metadata.
  - Requires at least one evaluation run id and a change summary before management review.

### FULL-Only Historical Families

| Family | Registry disposition |
|---|---|
| `/bff/agora/alerts` | `implemented_by_alias` via `GET /bff/risk/alerts` |
| `/bff/agora/channels` | `superseded_with_reason`; historical channel admin is replaced by explicit tools/MCP/skills and session surfaces |
| `/bff/agora/committee-sessions` | `implemented_by_alias` via `GET /bff/agora/sessions` |
| `/bff/agora/decision-journal` | `implemented_by_alias` via `GET /bff/agora/journal` |
| `/bff/agora/evaluations` | `superseded_with_reason`; global evaluations moved to persona-scoped evaluation and persona-lab gate surfaces |
| `/bff/agora/incoming` | `implemented_by_alias` via `GET /bff/agora/handoffs` |
| `/bff/agora/persona-drafts` | `superseded_with_reason`; draft browsing is replaced by persona-lab submit-commit and management review |
| `/bff/agora/skill-drafts` | `superseded_with_reason`; skill draft work is routed through BFF-LUV-GAP-008 tools/MCP/skills surfaces |
| `/bff/agora/research-tasks` | `implemented_by_alias` via `GET /bff/research/tasks` |
| `/bff/agora/market-notes` | `implemented_by_alias` via `GET /bff/agora/notes` |
| `/bff/agora/markets` | `implemented_by_alias` via `GET /bff/agora/watchlist` |
| `/bff/agora/assets/{id}/context` | `superseded_with_reason`; asset context is covered by evidence refs, research artifacts, and committee evidence packs |
| `/bff/agora/personas/available` | `implemented_by_alias` via `GET /bff/personas` |
| `/bff/agora/handoffs` | `implemented` |

## Verification

- `python3 -m pytest services/control-plane/bff/test_bff_agora_extended_contract.py services/control-plane/bff/test_bff_agora_core_contract.py services/control-plane/bff/test_execute_plans_contract_registry.py -q` -> 14 passed, 2 pre-existing `datetime.utcnow()` warnings in `read_store.py`.
- `python3 services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py` -> `agora-extended` reports 4 implemented, 8 alias, 0 missing, 0 deferred, 5 superseded.

# AG-BE-SW-002 Sidecar Acceptance Follow-up 5

| Field | Value |
|---|---|
| Task ID | `AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5` |
| Helper kind | `acceptance_packet` |
| Parent task | `AG-BE-SW-002` - StrategySpec draft patch/version linkage |
| Parent owner / reviewer | Claude2 / Claude |
| Sidecar owner / reviewer | Codex2 / Claude2 |
| Prepared by | Codex2 |
| Date | 2026-06-21 |
| Checked base | `origin/dev` at `241d1ad0` |
| Mutates canonical truth | false |
| Status | Prepared for reviewer handoff |

## Purpose

This packet is a support-only refresh after follow-up 4 merged into `dev`.
It does not replace the approved base packet or the follow-up 2, follow-up 3,
or follow-up 4 records:

- `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE.md`
- `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md`
- `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3.md`
- `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-4.md`

The goal is to keep the parent owner/reviewer aligned on the current `dev`
state: follow-up 4 is now merged support evidence, but no new authoritative
runtime, patch-contract, Registry draft-create, or store bootstrap material has
landed to unblock `AG-BE-SW-002`.

This sidecar does not edit L1/L2 canonical truth, OpenAPI bundles, schema
bundles, BFF runtime code, StrategySpec Registry code, governance code, or
execution surfaces.

## Current Dev Delta Since Follow-up 4

`origin/dev` is at merge commit `241d1ad0`. The only delta from the follow-up 4
pre-closeout comparison point `6ed60000` is the follow-up 4 task brief and
packet. No new runtime or contract artifact observed in this slice changes the
follow-up 4 conclusion.

| Surface | Current observation | Impact for `AG-BE-SW-002` |
|---|---|---|
| Follow-up 4 support | Follow-up 4 packet and closeout are merged on `dev`. | Treat it as support evidence, not implementation permission. |
| Parent task state | `AG-BE-SW-002` remains `blocked`, waiting for Claude, with the same four StrategySpec/versioning blockers. | Parent implementation remains stopped pending clarification. |
| OpenAPI v1.2 | `POST /versions` and `POST /versions/{version_id}/select` still require `If-Match` and `Idempotency-Key`. | Header requirement remains usable review evidence. |
| Patch payload | `VersionCreateRequest.patch` remains `type: object` with `additionalProperties: true`. | Still not a concrete JSON-path `from` / `to` grammar. |
| Design gap record | Gap group A still says `VersionPatchProposal` appears in no dev artifact and the patch grammar is missing. | Confirms this is a spec gap, not a local implementation detail to infer. |
| StrategySpec code | `services/research/strategy_spec/` still has no `workshop_projection.py`, `patching.py`, `version_compare.py`, Registry client, or draft-create helper. | Parent cannot safely implement projection, patch, compare, or draft persistence without inventing interfaces. |
| BFF runtime routes | The three version routes in `strategy_workshop/router.py` still call `_not_implemented(...)`. | Runtime versioning is not available. |
| Runtime persistence | v3 persistence schema defines `strategy_workshop_version_link`, but `store.py` still lacks the expected DDL helpers and `PostgresStrategyWorkshopStore` symbol tested by the bootstrap test. | Version-link readiness remains schema-only until runtime store code catches up. |
| Store bootstrap validation | `test_strategy_workshop_store_bootstrap.py` still fails 4/4 on missing store symbols. | This remains current-dev evidence that persistence is not ready. |

## Updated Dependency Map

| Task | Current status | Dependency / impact note |
|---|---:|---|
| `AG-BE-ID-001` | `done` | Identity foundation remains archived done. |
| `AG-BE-SW-001` | `done` | Workshop session/event/completeness persistence remains archived done. |
| `AG-BE-SW-002` | `blocked` | Parent is waiting for Claude on StrategySpec versioning and patch-contract clarification. |
| `AG-BE-SW-003` | `done` | Completeness / next-best-question skill is archived done with 26 gold+hard-rule tests recorded. |
| `AG-BE-SW-004` | `blocked` | Streaming aggregate remains separately blocked on typed stream event semantics, missing upstream version/research events, and missing degraded error contract. |
| `AG-FE-SW-002` | `todo` | Conversation/result cards depend on stream data from `AG-BE-SW-004`; should not infer missing version/research event payloads. |
| `AG-FE-SW-003` | `todo` | Version comparison/readiness UI data path comes from `AG-BE-SW-002`; should remain gated by the parent blocker. |
| `AG-BE-RS-004` | `todo` | Result synthesis mentions `VersionPatchProposal`; should not define its own patch envelope while gap group A is unresolved. |
| `AG-FE-RS-001` | `todo` | Research/backtest cards cite research and backtest fields that remain tied to unresolved upstream projection/stream data. |

## Acceptance Checklist Delta

Use the approved base packet for the full checklist. Apply these follow-up 5
deltas during parent review:

- Treat follow-up 4 as merged support evidence on `dev`, not as an unblock of
  the parent implementation.
- Keep `AG-BE-SW-002` blocked until the four recorded blocker questions are
  answered or explicitly reassigned into implementable subtasks.
- Accept OpenAPI v1.2 only for the mutating-route header requirement; do not
  accept the unconstrained `patch` object as an implementation-ready grammar.
- Keep `VersionPatchProposal`, patch grammar, `version_compare` semantics, and
  readiness gates assigned to the SD/spec clarification path.
- Keep the Registry-single-source-of-truth rule. The workshop may link to
  StrategySpec Registry versions; it must not become a StrategySpec JSON store.
- Do not let schema-only `strategy_workshop_version_link` definitions stand in
  for runtime persistence. Parent review must see store creation/use or a
  reviewed assignment of that work.
- Downstream FE/RS tasks should not invent version-diff, readiness, patch
  envelope, stream event payload, or research card fields while the upstream
  blockers remain open.

## Evidence Checked

Commands run from `task/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5`:

| Command | Result |
|---|---|
| `git fetch origin dev` | Refreshed `origin/dev`; branch HEAD is aligned with `origin/dev` at `241d1ad0`. |
| `git log --oneline 6ed60000..origin/dev` | Only follow-up 4 commits are new: `425fd854`, `2bdf803f`, and merge `241d1ad0`. |
| `git diff --name-status 6ed60000..origin/dev` | Only adds `.orchestrator/task-briefs/ag_be_sw_002_sidecar_acceptance_followup_4.md` and follow-up 4 packet. |
| `git status --short` before packet creation | Only the task-scoped follow-up 5 brief was dirty. |
| `git rev-parse --short HEAD` | `241d1ad0`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-002` | Parent is active `blocked`, waiting for Claude, with the four StrategySpec/versioning blockers. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5` | This sidecar is active `in_progress`, owner Codex2, reviewer Claude2, artifact path is this packet. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-003` | Archived `done`; closeout records 26 gold+hard-rule tests and merged PR evidence. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-004` | Active `blocked` on typed stream event schema, missing upstream version/research events, and missing degraded error contract. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-SW-002` | Active `todo`; conversation/result cards depend on `AG-BE-SW-004` stream data. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-SW-003` | Active `todo`; version/readiness UI data path comes from `AG-BE-SW-002`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-RS-004` | Active `todo`; result synthesis mentions `VersionPatchProposal` and must stay evidence-grounded. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-RS-001` | Active `todo`; depends on FE workshop/research data and upstream research projection. |
| `rg -n "VersionPatchProposal\|VersionCreateRequest\|patch object\|unconstrained\|strategy_workshop_version_link\|JSON path" docs/04/pantheon_agora_cross_repo_2026-06-20 services/control-plane/openapi services/control-plane/specs/agora` | Found v1.2/v3 references and design-gap group A; found no concrete patch grammar. |
| `rg -n "VersionCreateRequest\|If-Match\|Idempotency-Key\|/versions\|select" services/control-plane/openapi/agora_v1_2.openapi.yaml` | Confirms v1.2 declares required mutation headers for version create/select routes. |
| `sed -n '399,470p' services/control-plane/openapi/agora_v1_2.openapi.yaml` | Confirms `VersionCreateRequest.patch` is still an unconstrained object. |
| `rg --files services/research/strategy_spec` | Confirms no `workshop_projection.py`, `patching.py`, `version_compare.py`, Registry client, or draft-create helper exists. |
| `rg -n "_not_implemented\|versions\|strategy_workshop_version_link\|PostgresStrategyWorkshopStore\|build_strategy_workshop" ...` | Confirms version routes are still stubs and store bootstrap tests target missing store symbols/version-link DDL. |
| `python3 -m pytest services/control-plane/bff/tests/test_strategy_workshop_store_bootstrap.py` | Failed 4/4 on missing `build_strategy_workshop_table_ddl`, `build_strategy_workshop_index_ddl`, and `PostgresStrategyWorkshopStore`; this is pre-existing current-dev evidence, not a sidecar change. |

## Reviewer Handoff

Requested review by `Claude2`:

1. Confirm this packet preserves the support-only boundary and does not change
   canonical truth or implementation surfaces.
2. Confirm the `origin/dev@241d1ad0` delta is accurately described as follow-up
   4 support material only.
3. Confirm the acceptance delta should keep `AG-BE-SW-002` blocked until the
   StrategySpec versioning, patch grammar, Registry draft-create, and
   version-link store questions are resolved.

## Non-goals

This sidecar does not:

- define `VersionPatchProposal`;
- choose RFC 6902, RFC 7386, or a custom JSON-path `from` / `to` grammar;
- implement `workshop_projection.py`, `patching.py`, or `version_compare.py`;
- create a StrategySpec Registry API or draft-create path;
- add runtime `strategy_workshop_version_link` persistence;
- replace the three version route stubs;
- fix current-dev bootstrap tests;
- alter canonical L1/L2 truth.

Prepared by `Codex2` for the
`AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5` support-only review loop.

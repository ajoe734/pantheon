# AG-BE-SW-002 Sidecar Acceptance Follow-up 3

| Field | Value |
|---|---|
| Task ID | `AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3` |
| Helper kind | `acceptance_packet` |
| Parent task | `AG-BE-SW-002` - StrategySpec draft patch/version linkage |
| Parent owner / reviewer | Claude2 / Claude |
| Sidecar owner / reviewer | Codex2 / Codex (review reassigned from Claude2) |
| Prepared by | Codex2 |
| Date | 2026-06-21 |
| Checked base | `origin/dev` at `8049242d` |
| Mutates canonical truth | false |
| Status | Review approved; owner closeout pending |
| Review record | `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3-REVIEW.md` |

## Purpose

This packet is a support-only current-dev refresh for `AG-BE-SW-002`. It builds
on the approved base packet and the archived follow-up 2 packet:

- `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE.md`
- `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE-REVIEW.md`
- `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md`
- `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2-REVIEW.md`

The goal is to distinguish contract/design progress on current `dev` from
runtime readiness. This sidecar does not resolve, supersede, or implement the
parent task. It does not modify L1/L2 canonical truth, OpenAPI bundles, schema
contracts, BFF runtime code, Registry code, governance code, or execution
surfaces.

## Current Dev Delta Since Follow-up 2

`dev` has advanced beyond the follow-up 2 baseline. The relevant new state is
mixed:

| Surface | Current-dev observation | Impact for `AG-BE-SW-002` |
|---|---|---|
| OpenAPI v1.2 | `services/control-plane/openapi/agora_v1_2.openapi.yaml` exists and declares `If-Match` plus `Idempotency-Key` on `POST /versions` and `POST /versions/{version_id}/select`. | Confirms the mutating-route header requirement at the contract surface. |
| Version link schemas | `services/control-plane/specs/agora/v3/workshop_version_link.schema.json` and `workshop_persistence.schema.json` define `strategy_workshop_version_link` as a pointer to Strategy Registry truth. | Confirms the support packet's "workshop stores links, not StrategySpec JSON" interpretation. |
| Patch payload contract | v1.2 `VersionCreateRequest.patch` remains `type: object` with `additionalProperties: true`; v1.1 has the same unconstrained shape. | Does not define the requested JSON-path `from` / `to` grammar. Parent remains blocked on patch semantics. |
| Design gap record | `OPEN_DESIGN_GAPS_ROUND2_FOR_SD_TEAM_2026-06-21.md` gap group A says `VersionPatchProposal` appears in no dev artifact and the patch object is unconstrained. | Confirms this is an SD/spec gap, not a local implementation detail to guess around. |
| StrategySpec code | `services/research/strategy_spec/` still has no `workshop_projection.py`, `patching.py`, `version_compare.py`, Registry client, or draft-create helper. | Parent cannot safely implement projection, patch, compare, or draft persistence without inventing interfaces. |
| BFF runtime routes | `strategy_workshop/router.py` still leaves the three version routes as `_not_implemented(...)` stubs. | Parent route implementation has not landed. |
| Runtime persistence | `strategy_workshop/store.py` includes session pointer fields such as `active_strategy_spec_registry_id` and `selected_version_id`, but bootstrap still creates session/event/completeness/idempotency tables only; no version-link table is created by runtime store code. | Contract schema exists, but runtime persistence for version links is not implemented. |
| Store bootstrap tests | `services/control-plane/bff/tests/test_strategy_workshop_store_bootstrap.py` expects DDL helpers and `PostgresStrategyWorkshopStore` symbols that do not exist in `store.py`. | Current-dev validation shows the persistence layer is still inconsistent and should not be treated as ready. |

## Updated Dependency Map

| Task | Current status | Dependency / impact note |
|---|---:|---|
| `AG-BE-ID-001` | `done` | Identity foundation is archived done. |
| `AG-BE-SW-001` | `done` | Workshop session/event/completeness persistence is archived done. |
| `AG-BE-SW-002` | `blocked` | Parent is waiting for Claude on StrategySpec versioning and patch contract clarification. |
| `AG-BE-SW-003` | `done` | Completeness / next-best-question skill is archived done and no longer held by SW-002. |
| `AG-BE-SW-004` | `blocked` | Streaming aggregate is separately blocked on missing typed stream event semantics and unavailable upstream version/research events. |
| `AG-FE-SW-002` | `todo` | Conversation/result cards depend on stream data from `AG-BE-SW-004`; they should not infer missing version/research event contracts. |
| `AG-FE-SW-003` | `todo` | Version comparison/readiness UI still says data comes from `AG-BE-SW-002`; it should remain gated by the parent blocker. |
| `AG-BE-RS-004` | `todo` | Result-synthesis mentions `VersionPatchProposal`; it should not define its own patch envelope while group A is unresolved. |
| `AG-FE-RS-001` | `todo` | Research/backtest cards cite §7.4 fields that remain part of the unresolved design gap group. |

## Parent Blocker Refresh

The four follow-up 2 blockers remain materially valid, with current-dev nuance:

| Blocker | Follow-up 3 disposition |
|---|---|
| `VersionPatchProposal` design source missing. | Still unresolved. The round-2 design-gap file explicitly records that no dev artifact defines `VersionPatchProposal`. |
| JSON-path `from` / `to` patch grammar missing. | Still unresolved. v1.1 and v1.2 OpenAPI both keep `VersionCreateRequest.patch` as an unconstrained object. |
| StrategySpec Registry draft-create interface missing. | Still unresolved in code. The v3 contracts say to use the existing draft-create path, but `services/research/strategy_spec/` does not expose one. |
| `strategy_workshop_version_link` not bootstrapped in runtime store. | Partially clarified at schema level, still unresolved at runtime. v3 schemas define the table, but `store.py` does not create it and the new bootstrap test file fails against current symbols. |

Additional current-dev blocker:

- The three version BFF routes remain `501 NOT_IMPLEMENTED` stubs. This is
  expected for a blocked parent, but downstream tasks must not treat v1.2
  schemas as evidence that runtime versioning is available.

## Acceptance Checklist Delta

Use the approved base packet for the full checklist. Apply these current-dev
deltas during parent review:

- Keep the Registry-single-source-of-truth rule. Current v3 schemas now
  reinforce this by defining workshop versions as immutable links to
  `strategy_spec_registry_id`.
- Accept v1.2 as evidence that `If-Match` and `Idempotency-Key` are required
  for the two mutating version routes.
- Do not accept `VersionCreateRequest.patch` as sufficiently specified. A
  parent implementation still needs a concrete patch schema before
  `patching.py` can be written without invention.
- Do not let the parent create a workshop-local StrategySpec store to compensate
  for the missing Registry draft-create interface.
- Do not treat schema-only `strategy_workshop_version_link` definitions as
  runtime readiness. The BFF store must create and use the table, or the parent
  owner must record a reviewed decision assigning that work.
- Keep downstream FE/RS tasks gated from inventing version-diff, readiness, or
  patch-envelope semantics while `AG-BE-SW-002` is blocked.

## Evidence Checked

Commands run from `task/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3`:

| Command | Result |
|---|---|
| `git status -sb` | Branch started with only the task-scoped brief untracked. |
| `git fetch origin dev` then `git merge --ff-only origin/dev` | Task branch fast-forwarded to current `origin/dev`; branch/head parity is `0 0`, HEAD `8049242d`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-002` | Parent is active `blocked`, waiting for Claude, with the four StrategySpec/versioning blockers. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3` | This sidecar is active `in_progress`, owner Codex2, reviewer Claude2, artifact path is this packet. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-003` | Archived `done`; 26 tests passed in closeout snapshot. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-004` | Active `blocked` on typed stream event schema and unavailable upstream version/research events. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-SW-002` | Active `todo`; cards depend on stream data from `AG-BE-SW-004`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-SW-003` | Active `todo`; data path comes from `AG-BE-SW-002`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-RS-004` | Active `todo`; mentions `VersionPatchProposal` and must stay evidence-grounded. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-RS-001` | Active `todo`; depends on FE workshop/research data and §7.4 fields. |
| `rg -n "VersionPatchProposal\|VersionCreateRequest\|strategy_workshop_version_link\|draft-create\|StrategySpecRegistry\|strategy_spec_registry" ...` | Found v1.2/v3 contract references and the design-gap record; found no concrete patch grammar or StrategySpec Registry client. |
| `rg --files services/research/strategy_spec \| sort` | No `workshop_projection.py`, `patching.py`, `version_compare.py`, `registry.py`, or `store.py` exists under StrategySpec. |
| `rg -n "_not_implemented\|versions\|strategy_workshop_version_link" strategy_workshop/{router.py,store.py}` | Version routes are still stubs; runtime store does not define/create the version-link table. |
| `python3 -m pytest services/control-plane/bff/tests/test_strategy_workshop_store_bootstrap.py` | Failed 4/4 on current dev because expected DDL helpers and `PostgresStrategyWorkshopStore` are absent. This is pre-existing current-dev evidence, not a sidecar change. |

## Reviewer Guidance

Codex should approve this sidecar only if the packet accurately captures the
current-dev split between contract progress and runtime blockers. The reviewer
was reassigned from Claude2 after packet preparation; this does not change the
sidecar's support-only scope.

Recommended checks:

1. Confirm this packet and the task-scoped brief are the only authored files.
2. Confirm no canonical truth, OpenAPI/schema bundle, BFF runtime, Registry, or
   governance implementation is modified by this sidecar.
3. Confirm the packet does not claim to unblock or approve parent
   `AG-BE-SW-002`.
4. If approved, return to Codex2 for owner closeout. The parent owner/reviewer
   still need SD/spec clarification before implementation can continue.

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

## Owner Closeout Note

Reviewer `Codex` approved this support-only packet. Owner closeout preserves the
same boundary: the parent `AG-BE-SW-002` remains blocked on StrategySpec
versioning and patch-contract clarification, and this sidecar does not promote
any packet content into canonical truth or runtime implementation.

Prepared by `Codex2` for the
`AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3` support-only review loop.

# AG-BE-SW-002 Sidecar Acceptance Follow-up 4

| Field | Value |
|---|---|
| Task ID | `AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-4` |
| Helper kind | `acceptance_packet` |
| Parent task | `AG-BE-SW-002` - StrategySpec draft patch/version linkage |
| Parent owner / reviewer | Claude2 / Claude |
| Sidecar owner / reviewer | Codex2 / Claude (reassigned from Claude2) |
| Prepared by | Codex2 |
| Date | 2026-06-21 |
| Checked base | `origin/dev` at `6ed60000` |
| Mutates canonical truth | false |
| Status | Review approved; owner closeout prepared |

## Purpose

This packet is a support-only refresh after follow-up 3 merged into `dev`.
It does not replace the approved base packet or the follow-up 2 / follow-up 3
records:

- `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE.md`
- `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md`
- `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3.md`

The goal is to keep the parent owner/reviewer and downstream tasks aligned on
one point: current `dev` contains useful support evidence, v1.2 route headers,
and v3 version-link schemas, but it still does not contain enough authoritative
runtime or patch-contract material to unblock `AG-BE-SW-002` implementation.

This sidecar does not edit L1/L2 canonical truth, OpenAPI bundles, schema
bundles, BFF runtime code, StrategySpec Registry code, governance code, or
execution surfaces.

## Current Dev Delta Since Follow-up 3

`origin/dev` has advanced to merge commit `6ed60000`, which includes
follow-up 3 packet, review, and closeout material plus unrelated
`AG-FE-DB-002` sidecar packets and an `AG-BE-SW-004` BFF stream handoff packet.
No new runtime or contract artifact observed in this slice changes the
follow-up 3 conclusion.

| Surface | Current observation | Impact for `AG-BE-SW-002` |
|---|---|---|
| Parent task state | `AG-BE-SW-002` remains `blocked`, waiting for Claude, with the same four StrategySpec/versioning blockers. | Parent implementation remains stopped by design/contract clarification. |
| OpenAPI v1.2 | `POST /versions` and `POST /versions/{version_id}/select` require `If-Match` and `Idempotency-Key`. | Header requirement is usable review evidence. |
| Patch payload | `VersionCreateRequest.patch` remains `type: object` with `additionalProperties: true`. | Still not a concrete JSON-path `from` / `to` grammar. |
| Design gap record | Gap group A still says `VersionPatchProposal` appears in no dev artifact and the patch grammar is missing. | Confirms this is a spec gap, not a local implementation detail to infer. |
| StrategySpec code | `services/research/strategy_spec/` still has no `workshop_projection.py`, `patching.py`, `version_compare.py`, Registry client, or draft-create helper. | Parent cannot safely implement projection, patch, compare, or draft persistence without inventing interfaces. |
| BFF runtime routes | The three version routes in `strategy_workshop/router.py` still call `_not_implemented(...)`. | Runtime versioning is not available. |
| Runtime persistence | v3 persistence schema defines `strategy_workshop_version_link`, but `store.py` still lacks the expected DDL helpers and `PostgresStrategyWorkshopStore` symbol tested by the bootstrap test. | Version-link readiness is schema-only until runtime store code catches up. |
| Store bootstrap validation | `test_strategy_workshop_store_bootstrap.py` still fails 4/4 on missing store symbols. | This remains pre-existing evidence that persistence is not ready. |

## Updated Dependency Map

| Task | Current status | Dependency / impact note |
|---|---:|---|
| `AG-BE-ID-001` | `done` | Identity foundation remains archived done. |
| `AG-BE-SW-001` | `done` | Workshop session/event/completeness persistence remains archived done. |
| `AG-BE-SW-002` | `blocked` | Parent is waiting for Claude on StrategySpec versioning and patch-contract clarification. |
| `AG-BE-SW-003` | `done` | Completeness / next-best-question skill is archived done with 26 gold+hard-rule tests recorded. |
| `AG-BE-SW-004` | `blocked` | Streaming aggregate remains separately blocked on typed stream event semantics and unavailable upstream version/research events. |
| `AG-FE-SW-002` | `todo` | Conversation/result cards depend on stream data from `AG-BE-SW-004`; should not infer missing version/research event payloads. |
| `AG-FE-SW-003` | `todo` | Version comparison/readiness UI data path comes from `AG-BE-SW-002`; should remain gated by the parent blocker. |
| `AG-BE-RS-004` | `todo` | Result synthesis mentions `VersionPatchProposal`; should not define its own patch envelope while gap group A is unresolved. |
| `AG-FE-RS-001` | `todo` | Research/backtest cards cite §7.4 fields that remain part of unresolved design gap group A/B/E. |

## Acceptance Checklist Delta

Use the approved base packet for the full checklist. Apply these follow-up 4
deltas during parent review:

- Treat follow-up 3 as merged support evidence on `dev`, not as an unblock of
  the parent implementation.
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
  envelope, or stream event payload fields while `AG-BE-SW-002` and
  `AG-BE-SW-004` remain blocked.

## Evidence Checked

Commands run from `task/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-4`:

| Command | Result |
|---|---|
| `git fetch origin dev` then `git merge --ff-only origin/dev` during packet preparation | Refreshed and fast-forwarded to `origin/dev` at `068eb9c4`; the intervening dev commit touched only an unrelated `AG-FE-DB-002` sidecar packet. |
| `git fetch origin dev`; `git log --oneline HEAD..origin/dev`; `git diff --name-status HEAD..origin/dev` during owner closeout | Refreshed view of `origin/dev` at `6ed60000`; new dev commits add unrelated `AG-BE-SW-004` and `AG-FE-DB-002` sidecar support packets and do not modify AG-BE-SW-002 canonical, runtime, schema, or contract surfaces. |
| `git status -sb` | Branch is `task/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-4`; only the task-scoped brief was dirty before packet creation. |
| `git status --short` during owner closeout | Only the task-scoped brief and this packet are modified. |
| `git diff --check` during owner closeout | Passed. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-002` | Parent is active `blocked`, waiting for Claude, with the four StrategySpec/versioning blockers. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-4` | This sidecar is active `review_approved`, owner Codex2, reviewer Claude, artifact path is this packet, and review notes approve owner closeout. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-003` | Archived `done`; closeout records 26 gold+hard-rule tests and merged PR evidence. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-004` | Active `blocked` on typed stream event schema, missing upstream version/research events, and missing degraded error contract. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-SW-002` | Active `todo`; conversation/result cards depend on `AG-BE-SW-004` stream data. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-SW-003` | Active `todo`; version/readiness UI data path comes from `AG-BE-SW-002`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-RS-004` | Active `todo`; result synthesis mentions `VersionPatchProposal` and must stay evidence-grounded. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-RS-001` | Active `todo`; depends on FE workshop/research data and §7.4 fields. |
| `rg -n "VersionPatchProposal\|VersionCreateRequest\|strategy_workshop_version_link\|draft-create\|StrategySpec Registry" ...` | Found v1.2/v3 references and design-gap group A; found no concrete patch grammar or draft-create implementation. |
| `rg --files services/research/strategy_spec` | Confirms no `workshop_projection.py`, `patching.py`, `version_compare.py`, Registry client, or draft-create helper exists. |
| `rg -n "_not_implemented\|versions\|strategy_workshop_version_link\|PostgresStrategyWorkshopStore\|build_strategy_workshop" ...` | Confirms version routes are still stubs and store bootstrap tests target missing store symbols/version-link DDL. |
| `python3 -m pytest services/control-plane/bff/tests/test_strategy_workshop_store_bootstrap.py` | Failed 4/4 on missing `build_strategy_workshop_table_ddl`, `build_strategy_workshop_index_ddl`, and `PostgresStrategyWorkshopStore`; this is pre-existing current-dev evidence, not a sidecar change. |

## Review And Closeout Outcome

Claude approved this sidecar on 2026-06-21 after reassignment from Claude2.
The review confirmed that the packet accurately captures the current dev state:
merged follow-up 3 material is support evidence, not implementation permission
for `AG-BE-SW-002`.

Closeout refresh checks:

1. This packet and the task-scoped brief remain the only authored files.
2. No canonical truth, OpenAPI/schema bundle, BFF runtime, Registry,
   governance, or execution implementation file is modified by this sidecar.
3. The packet keeps `AG-BE-SW-002` blocked until the SD/spec gaps are
   clarified or explicitly assigned.
4. `origin/dev` commits after the reviewed base touch unrelated sidecar
   support artifacts and do not change this acceptance conclusion.
5. The parent owner/reviewer can decide whether to absorb this packet into the
   parent implementation plan after review.

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
`AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-4` support-only review loop.

# AG-BE-SW-002 Sidecar Acceptance Follow-up 2

| Field | Value |
|---|---|
| Task ID | `AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2` |
| Helper kind | `acceptance_packet` |
| Parent task | `AG-BE-SW-002` - StrategySpec draft patch/version linkage |
| Parent owner / reviewer | Claude2 / Claude |
| Sidecar owner / reviewer | Codex / Claude2 |
| Prepared by | Codex |
| Date | 2026-06-21 |
| Checked base | `origin/dev` at `66293cab` |
| Mutates canonical truth | false |
| Status | Ready for sidecar review |

## Purpose

This packet is a support-only follow-up to the approved
`AG-BE-SW-002-SIDECAR-ACCEPTANCE` packet. It does not replace that packet and
does not edit canonical architecture, OpenAPI bundles, runtime code, Registry
contracts, or governance implementation.

The follow-up records what changed after the first packet was approved:

- parent `AG-BE-SW-002` is now blocked before implementation on missing or
  insufficient StrategySpec versioning design;
- `AG-BE-SW-003` has since completed and should not remain described as held;
- `AG-BE-SW-004` is separately blocked on typed workshop stream semantics;
- `AG-FE-SW-003`, `AG-BE-RS-004`, and `AG-FE-RS-001` remain downstream users of
  the unresolved versioning / patch / readiness contract.

Approving this follow-up should only accept the support packet. It must not be
treated as approval to implement `AG-BE-SW-002` without resolving the parent
blocker.

## Baseline From Approved Packet

The first sidecar packet remains the acceptance baseline:

- `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE.md`
- `support/sidecars/AG-BE-SW-002/AG-BE-SW-002-SIDECAR-ACCEPTANCE-REVIEW.md`
- archived task snapshot:
  `ai-task-archive/tasks/AG-BE-SW-002-SIDECAR-ACCEPTANCE.json`

Claude2 approved that packet with two implementation annotations:

1. verify the StrategySpec Registry draft-create path before wiring
   `POST /bff/agora/workshops/{id}/versions`;
2. use the workshop session row ETag for both `POST /versions` and
   `POST /versions/{ver}/select`.

Those annotations are still valid. The current parent blocker shows they are
not yet resolved enough for implementation.

## Current Task Graph Snapshot

Source: `AI_NAME=Codex ./scripts/ai-status.sh show <task-id>` on 2026-06-21.

| Task | Current status | Dependency / impact note |
|---|---:|---|
| `AG-BE-ID-001` | `done` | Identity foundation is archived done. |
| `AG-BE-SW-001` | `done` | Workshop persistence and the initial route stubs are archived done. |
| `AG-BE-SW-002` | `blocked` | Parent is waiting for Claude on StrategySpec versioning and patch contract clarification. |
| `AG-BE-SW-003` | `done` | Completeness / next-best-question skill completed after the first packet; it formally depends on `AG-BE-SW-001`, not `AG-BE-SW-002`. |
| `AG-BE-SW-004` | `blocked` | Separate stream aggregate task is blocked on missing typed stream event schema and upstream version/research events. |
| `AG-FE-SW-002` | `todo` | Depends on `AG-FE-SW-001`; its data path is expected to come from the workshop stream. |
| `AG-FE-SW-003` | `todo` | Version comparison and readiness UI says data comes from `AG-BE-SW-002`; should remain gated by parent blocker. |
| `AG-BE-RS-004` | `todo` | Result synthesis mentions `VersionPatchProposal`; should not invent a patch envelope while `AG-BE-SW-002` is blocked. |
| `AG-FE-RS-001` | `todo` | Research cards cite version/backtest fields that remain part of the open SD gap group. |

## Parent Blocker Map

`AG-BE-SW-002` is currently `blocked`, waiting for Claude, with four concrete
issues recorded in the task status:

| Blocker | Follow-up disposition |
|---|---|
| Cited SD sections for `VersionPatchProposal` cannot be found in the checked design sources. | Confirmed by `OPEN_DESIGN_GAPS_ROUND2_FOR_SD_TEAM_2026-06-21.md` gap group A. |
| `VersionCreateRequest.patch` is an unconstrained object and no concrete JSON-path `from` / `to` schema exists. | Still unresolved. The v1.1 OpenAPI extension defines `patch: {type: object, additionalProperties: true}` only. |
| No usable StrategySpec Registry draft-create client/interface exists under `services/research/strategy_spec/`. | Still unresolved in checked source. The directory contains models/conversion/normalizer/completeness/lineage, but no registry/store client. |
| `strategy_workshop_version_link` is specified in support/design docs but not bootstrapped by the current BFF store. | Still unresolved in runtime code. `store.py` bootstraps session, event, completeness snapshot, and idempotency tables only. |

This follow-up does not resolve those blockers. It preserves them as
acceptance gates for the parent owner and reviewer.

## Evidence Checked

Commands run from the task branch:

| Command | Result |
|---|---|
| `git status -sb` | Branch is `task/AG-BE-SW-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2`; only the generated task brief was dirty before this packet. |
| `git fetch origin dev` | Refreshed `origin/dev`; branch base remained `66293cab` and was 0 ahead / 0 behind before edits. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-002` | Parent status is `blocked`, waiting for Claude, with the four blockers mapped above. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-003` | Archived `done`; 26 tests passed per closeout snapshot. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-004` | Active `blocked` on missing typed stream event schema and unavailable upstream version/research events. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-003` | Active `todo`; summary says version/readiness UI data comes from `AG-BE-SW-002`. |
| `rg -n "VersionPatchProposal|VersionCreateRequest|strategy_workshop_version_link|draft-create" ...` | `VersionPatchProposal` is not defined; v1.1 `VersionCreateRequest.patch` is unconstrained; version-link schemas exist, but runtime store bootstrap lacks the table. |
| `rg --files services/research/strategy_spec \| sort` | No `registry.py`, `store.py`, `patching.py`, `version_compare.py`, or `workshop_projection.py` exists under `services/research/strategy_spec/`. |
| `rg -n "strategy_workshop_version_link|_not_implemented|versions" services/control-plane/bff/agora/strategy_workshop/{store.py,router.py}` | The three version routes still call `_not_implemented`; store bootstrap does not create `strategy_workshop_version_link`. |

## Acceptance Checklist Delta

Use the approved first packet for the full checklist. Apply these deltas during
parent review:

- Treat `AG-BE-SW-003` as completed background context, not a held downstream
  task under `AG-BE-SW-002`.
- Keep `AG-BE-SW-002` blocked until a concrete patch grammar and
  `VersionPatchProposal` envelope exist in an authoritative design or additive
  schema.
- Keep `POST /versions` blocked until the parent owner has a real StrategySpec
  Registry draft-create interface to call. Do not implement a workshop-local
  StrategySpec JSON store or bypass write path.
- Keep version route implementation blocked until runtime persistence includes
  the workshop-version link table or a reviewed decision assigns that work to
  `AG-BE-SW-002`.
- Do not let downstream FE/RS tasks invent readiness gates, version diff
  semantics, or patch payload fields while the parent contract remains blocked.

## Reviewer Guidance

Claude2 should approve this sidecar only if the packet accurately captures the
post-approval state of `AG-BE-SW-002` and its immediate dependents.

Recommended reviewer checks:

1. Confirm this file is the only new support packet for the follow-up.
2. Confirm no L1 canonical truth, OpenAPI bundle, schema, BFF runtime, Registry,
   or governance file is modified by this sidecar.
3. Confirm the blocker framing does not claim to resolve or supersede
   `AG-BE-SW-002`.
4. If approved, return the task to Codex for normal owner closeout; the parent
   owner/reviewer still need a design clarification before implementation can
   continue.

## Non-goals

This sidecar does not:

- define `VersionPatchProposal`;
- choose RFC 6902 vs merge-patch vs custom JSON-path `from` / `to` grammar;
- create a StrategySpec Registry API;
- add `strategy_workshop_version_link` runtime persistence;
- replace the three version route stubs;
- approve `AG-BE-SW-002` implementation;
- alter canonical L1/L2 truth.

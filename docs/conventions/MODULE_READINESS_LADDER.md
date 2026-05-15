# Module Readiness Ladder

Status: draft-canonical
Last updated: 2026-04-22
Source of truth inputs:
- `docs/reviews/Pantheon_Response_to_System_Design_Open_Questions.md`
- `docs/reviews/Pantheon_Response_to_System_Design_Followup_Questions.md`
- `docs/reviews/Pantheon_Response_to_Architecture_Blockers_Decision_Package.md`
Tier: L1 Platform Architecture & Policy
Scope: canonical module readiness states, promotion rules, and legacy-status mapping
Conflict rule: ratification records and explicit module gate decisions may specialize readiness for one module, but they must still map back to this shared ladder

## Purpose

This document defines the canonical readiness ladder for Pantheon modules and
the mapping from older repo vocabulary.

## Canonical ladder

Pantheon uses one shared readiness ladder:

- `blocked`
- `contract_ready`
- `screen_ready`
- `handoff_ready`
- `implementation_ready`
- `production_ui_ready`

## Ladder definitions

### 1. `blocked`

Meaning:

- canonical contract is not fully locked
- owner / authority / lifecycle / degradation semantics still have open
  architecture questions

Implementation lane may proceed:

- no, except shell or non-authoritative scaffolding

### 2. `contract_ready`

Meaning:

- BFF-facing canonical contract is locked enough for implementation planning
- read model exists
- lifecycle / authority / `allowedActions` / degradation semantics are defined
- example payload exists or can be generated without inventing net-new truth

Does not mean:

- route is live
- tests are done
- frontend can already integrate

### 3. `screen_ready`

Meaning:

- screen spec is honest and aligned with the contract
- the frontend can describe the real page behavior without inventing truth

### 4. `handoff_ready`

Meaning:

- frontend handoff bundle exists
- the UI lane has enough packet truth to begin once runtime gates are satisfied

### 5. `implementation_ready`

Meaning:

- backend / BFF lane may implement immediately
- no remaining architecture clarification is required for execution
- route path, envelope, commands, and acceptance criteria are defined enough to
  open a concrete implementation slice

### 6. `production_ui_ready`

Meaning:

- live route truth is available
- readiness / authority / degradation semantics are aligned enough that the
  frontend can build the real production page without synthesizing truth

## Promotion from `contract_ready` to `implementation_ready`

A module may move from `contract_ready` to `implementation_ready` only when:

1. BFF route path is defined
2. response envelope is defined
3. example payload exists
4. state / lifecycle is defined
5. authority owner is defined
6. `allowedActions` is defined
7. degradation semantics are defined
8. pagination / filtering is defined for list routes where applicable
9. write command vocabulary is defined when mutation exists
10. affected files are listed
11. acceptance criteria are written

## Legacy vocabulary mapping

| Existing vocabulary | Canonical readiness | Notes |
|---|---|---|
| `not_ready` | `blocked` | no sufficient canonical contract or route/read model |
| `blocked` | `blocked` | keep as-is |
| `shell-only` | `blocked` or `screen_ready` | shell without canonical truth remains `blocked`; honest UI shell plus screen spec may be `screen_ready` |
| `contract-published` | `contract_ready` | only if the contract is actually ratified |
| `pending-bff` | `contract_ready` | delivery label, not its own ladder rung |
| `screen-ready` | `screen_ready` | keep mapped directly |
| `handoff-ready` | `handoff_ready` | keep mapped directly |
| `route-live` | `implementation_ready` | may later promote to `production_ui_ready` when handoff and acceptance truth are aligned |
| `ready` | deprecated | too vague; rewrite to canonical ladder |
| `implemented` | `implementation_ready` or `production_ui_ready` | backend-only implementation is lower than full production UI readiness |

## Partial activation rule

`partial activation` is not a separate global ladder rung.

It is a promotion modifier used when:

- some live sub-surface is production-usable
- but the full module is not yet eligible for full production handoff

Current explicit case:

- `CW-03` may partial-activate before `CW-02` is fully live
- `CW-03` must not be promoted as full module-ready until transcript-dependent
  surfaces are backed by authoritative `CW-02` truth

## Canonical readiness truth precedence

1. `MODULE_READINESS_RATIFICATION_*.md`
2. `WORKBENCH_DELIVERY_BACKLOG.md` plus code truth
3. BFF overviews, SA docs, packet family docs, lovable-ui-task docs

Derived documents must not override higher-priority readiness truth.

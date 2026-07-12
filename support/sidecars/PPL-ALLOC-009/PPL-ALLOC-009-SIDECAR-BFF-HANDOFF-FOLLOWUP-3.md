# PPL-ALLOC-009 Closeout Decision Handoff

- **Sidecar Task**: `PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`
- **Parent Task**: `PPL-ALLOC-009`
- **Owner / Reviewer**: `Codex` / `Antigravity`
- **Helper Kind**: `bff_handoff_packet`
- **Scope**: support-only; no canonical, BFF/runtime, registry/governance,
  frontend, deployment, or capital-state mutation
- **Date**: 2026-07-12

## Purpose

This packet gives the parent owner a review-ready stop/go matrix for the BFF
queries, operator journey, and frontend evidence required by
`PPL-ALLOC-009`. It refines the earlier handoff packets; it does not certify a
deployment or add a new contract. Every `pass` below must be backed by actual
responses from the named deployment and by response-derived identifiers.

## Closeout Stop/Go Matrix

| Journey segment | Minimum go evidence | Stop condition | Do not mistake for proof |
| --- | --- | --- | --- |
| Paper persona creation | Creation response and follow-up Fleet/Capital reads agree on `persona_id`, isolated `paper_ledger_id`, runtime binding, `paper_running`, and no live-capital side effect. | Any identity is absent or disagrees; a real pool/sleeve is implied; setup is partial while UI reports running. | Form submission, success toast, local fixture, or persona row without binding/ledger readback. |
| Paper promotion review | Recommendation submission joins deterministically to one promotion review and authorized human decision, retaining target stage, evidence, correlation, approval, and audit references. | The review is found only by label/time; target stage or evidence is missing; submission/decision claims direct full-live allocation. | Recommendation accepted, inbox badge, or decision receipt without linked review query. |
| Real allocation proposal | One ranking snapshot yields the displayed eligible/excluded universe, advisory target weights with `applied: false`, and a rebalance proposal carrying current/target/delta, caps, simulation, rollback, and snapshot identity. | UI invents eligibility/weights; snapshot linkage is missing; excluded rows silently disappear; advisory output is presented as applied. | Rendered chart, locally calculated totals, or proposal creation alone. |
| Approved apply | Unapproved live increase fails; approved apply uses a stable idempotency key; the owning command query reaches terminal success; authoritative allocation/binding readback matches the intended identities and weights. | Negative probe is admitted; command is non-terminal/failed/unknown; readback is absent or disagrees. | HTTP `202`, approval presence, apply receipt, elapsed time, optimistic UI, or success toast. |
| Emergency containment | A safe dev fixture admits a risk-decreasing reduce/freeze/suspend action, while promotion and allocation-increase variants fail closed; refreshed reads show no increase/promotion side effect. | Any unsafe variant is admitted or post-command reads cannot rule out an increase/promotion. | Client-disabled controls or policy unit tests without hosted/API negative probes. |
| Hosted operator surface | Exact Pantheon and Execute Plans deployed SHAs, authenticated desktop and mobile traces, live BFF mode, strict fallback, and no required-request fixture/mock substitution. | Deployment identity is unknown; either viewport fails; auth/network/stale failure becomes empty/success; fallback data enters evidence. | Local tests/build, one viewport, cached seed data, or route existence. |

The parent verdict is `blocked` if any stop condition remains. A residual-risk
entry does not convert a missing acceptance proof into a pass; it records who
must repair or rerun it.

## Minimal BFF Query Manifest

The smoke ledger should retain the raw sanitized response artifact and salient
fields for each query. Route templates may be instantiated with the actual IDs
returned by the preceding command.

| Phase | Query or command | Required retained linkage |
| --- | --- | --- |
| Create | `POST /bff/management/personas/create-paper-bundle` | Idempotency/correlation IDs, `persona_id`, paper ledger, runtime binding, deployment/stage results, live-side-effect flag. |
| Verify identity | Authoritative Persona Fleet and Capital reads used by the deployed UI | Same persona, ledger/binding, stage, pool/sleeve identities, snapshot timestamp and staleness metadata. |
| Submit promotion | `POST /bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit` | Recommendation, ranking/evidence, target stage, returned review reference, correlation and audit references. |
| Review | `GET /bff/management/promotion-reviews/{review_id}` and `POST /bff/management/promotion-reviews/{review_id}/decisions` | Review, decision, actor/role, approval/audit receipt, and unchanged correlation chain. |
| Evaluate | `POST /bff/management/allocation-policy/evaluate` | Ranking snapshot, complete eligible/excluded lines, caps/reasons, targets, and `applied: false`. |
| Propose | `POST /bff/rebalances`, then `GET /bff/rebalances/{id}` | Rebalance ID, snapshot ID, current/target/delta, constraints, simulation, rollback, approval/audit references. |
| Apply | `POST /bff/rebalances/{id}/apply`, then the owning command/status query | Stable idempotency key, approval ref, command ID, terminal result, audit receipt, and error body for the no-approval negative probe. |
| Read back | Authoritative Capital/Fleet/binding reads used by the deployed UI | Post-command weights and identities, snapshot timestamp, no optimistic or locally merged values. |
| Contain | Governed emergency containment command and refreshed reads | Accepted trigger/evidence, risk-decreasing action receipt, rejected increase/promotion bodies, and post-action state. |

If the deployed surface does not expose a deterministic command-status or
authoritative readback query, record that exact missing owner query as a
blocking BFF gap. Do not replace it with polling a proposal, reading a UI
toast, or constructing a frontend-owned aggregate.

## Frontend Evidence Handoff

The separate `execute-plans` checkout and deployment must:

- use `VITE_BFF_MODE=live`, the Pantheon dev BFF origin, and
  `VITE_BFF_FALLBACK=strict`;
- preserve server IDs and the original idempotency key across an ambiguous
  write retry, querying the prior intent before issuing another mutation;
- render admission, pending command, terminal success/failure, and refreshed
  authoritative state as distinct phases;
- show missing identity, evidence, stage, cap/exclusion reason, approval, or
  snapshot freshness as blocked/unknown rather than synthesizing a value;
- distinguish `401`, `403`, `409`, `422`, network failure, stale/degraded data,
  and genuine empty results; and
- capture authenticated desktop and mobile traces with request failures kept
  visible in the evidence set.

Frontend proof should name its repository PR/merge SHA, deployed bundle SHA,
browser viewport, role, timestamp, BFF origin, and related sanitized trace or
screenshot. Frontend source or test artifacts must not be copied into this
Pantheon repository.

## Evidence Verdict Template

Use one row per positive or negative probe:

| Segment / probe | Pantheon SHA | Frontend SHA | Role + viewport | Correlation + idempotency | Domain IDs | Admission | Terminal state | Readback | Artifact | Verdict / blocker owner / recheck |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `<step>` | `<sha>` | `<sha or n/a>` | `<role, desktop/mobile/API>` | `<ids>` | `<persona/review/snapshot/rebalance/command>` | `<HTTP>` | `<state or n/a>` | `<query + fields>` | `<sanitized path>` | `<pass or blocked; owner; condition>` |

Required negative rows are: real/live intent during paper creation; direct
full-live mutation during promotion submission; live allocation increase
without approval; emergency promotion; emergency allocation increase; and
strict frontend fallback/mock substitution.

## Reviewer Questions

- [ ] Do actual response IDs provide every join, without label/time matching?
- [ ] Are command admission, terminal execution, and authoritative readback
      independently evidenced?
- [ ] Do approval and containment negative probes preserve sanitized failure
      bodies and fail closed?
- [ ] Do ranking/proposal artifacts retain excluded rows, caps, simulation,
      rollback, and snapshot identity?
- [ ] Do desktop and mobile runs use the recorded strict/live deployment and
      expose all required-request failures?
- [ ] Does every blocked row name an owner and objective recheck condition?

An unchecked item returns concrete work to the parent lane. This sidecar must
not be used as review approval, hosted evidence, or a declaration that the
parent task is complete.

## Composition Boundary

Owned layer: task-scoped BFF query manifest, operator stop/go matrix, frontend
evidence handoff, and reviewer checklist.

Not changing: L1 truth, service contracts, BFF/frontend/runtime code,
registry/governance semantics, deployment configuration, or capital state.

Composes with: the original `PPL-ALLOC-009` handoff packet, follow-up-2
readiness gate, the parent closeout archive, and parent-owner evidence from
the deployed Pantheon and Execute Plans commits.

## References

- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-009-closeout-dev-publish.md`

## Finalization Record

- Reviewer `Antigravity` approved implementation commit `d89de9cc9` and
  returned the task to owner `Codex` for closeout.
- Review evidence is recorded in
  `docs/reviews/2026-07-12-ppl-alloc-009-sidecar-bff-handoff-followup-3-antigravity-review.md`.
- Owner closeout verification: `git diff --check`; existence checks for all
  three references above; and `git diff --name-only origin/dev...HEAD` to
  confirm the branch remains confined to this support packet and its review
  record.
- This finalization records publication readiness only. The parent owner still
  decides whether and how to compose the packet into `PPL-ALLOC-009`; it does
  not certify the parent task, a hosted deployment, or any capital mutation.

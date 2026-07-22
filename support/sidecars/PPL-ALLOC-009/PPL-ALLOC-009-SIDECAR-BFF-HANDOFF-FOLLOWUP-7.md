# PPL-ALLOC-009 Executable BFF/Frontend Handoff Run Sheet

- **Sidecar Task**: `PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-7`
- **Parent Task**: `PPL-ALLOC-009`
- **Owner / Reviewer**: `Codex` / `Antigravity`
- **Helper Kind**: `bff_handoff_packet`
- **Scope**: support-only; no canonical, BFF/runtime, registry/governance,
  frontend, deployment, or capital-state mutation
- **Snapshot date**: 2026-07-12

## Purpose

This packet gives the parent owner an executable run sheet for turning the
existing route inventory into closeout evidence. It neither declares the
routes deployed nor changes their contract. Repository route presence, unit
tests, an HTTP admission response, terminal execution, authoritative readback,
and hosted browser behavior remain separate claims.

## Query-Gap Triage

Before hosted smoke, the parent owner must resolve each row against the exact
deployed Pantheon SHA. `Available` means the deployed BFF returns the required
response-derived identifiers; source-code presence alone is `unverified`.

| Required join | Candidate surface | Fields that must survive | Blocking gap when absent |
| --- | --- | --- | --- |
| Created persona to isolated paper runtime | `POST /bff/management/personas/create-paper-bundle`, then Fleet/Capital/binding reads | `persona_id`, `paper_ledger_id`, runtime binding, stage, correlation/trace, failed-step detail | No authoritative query can confirm all created identities and `paper_running` without a live-capital side effect. |
| Recommendation to governed review | Quarterly recommendation submit, promotion-review detail and decision | recommendation, ranking/evidence, review, target stage, actor, decision, approval/audit refs | Review can only be found by label or timestamp, or correlation is lost between submit and decision. |
| Ranking to advisory rebalance | Allocation-policy evaluation, rebalance create/detail | `ranking_snapshot_id`, complete eligible/excluded universe, cap reasons, current/target/delta, simulation, constraints, rollback target, `applied: false` | The proposal cannot be reconciled to one immutable ranking snapshot or excluded rows disappear. |
| Apply admission to execution | Rebalance apply plus owning command/status query | stable idempotency key, approval ref, command ID, audit ref, terminal state and failure detail | Apply returns only admission/receipt and no authoritative terminal-status owner is queryable. |
| Execution to capital truth | Capital/Fleet/binding reads after terminal command | same persona, pool/sleeve/binding identities, applied weights, snapshot time and freshness | No authoritative readback proves the intended allocation, or the read disagrees with the terminal result. |
| Containment to safe post-state | Governed emergency command plus refreshed state reads | trigger/evidence, risk-decreasing action, receipt/audit, unchanged promotion state and no increased allocation | Unsafe intent is admitted or post-state cannot exclude promotion/allocation increase. |

Do not fill a missing join with a frontend aggregate, cached fixture, inferred
timestamp relationship, label match, or locally calculated weight. Record the
exact missing owner query as a blocking residual risk.

## Operator Journey Checkpoints

Run with one safe dev persona and retain sanitized requests/responses. Every
checkpoint is independently pass/fail; a later pass does not repair an earlier
missing proof.

1. **Deployment identity**: record Pantheon and Execute Plans merge/deployed
   SHAs, BFF/frontend origins, UTC timestamp, role, and strict live-mode build
   posture.
2. **Paper bundle**: create with one stable idempotency key; follow returned
   IDs into Fleet, Capital, and binding reads. Reject any request containing
   real/live capital intent.
3. **Promotion review**: submit the response-linked recommendation, query the
   exact review, and make an authorized decision. Confirm it authorizes the
   next governed stage only and does not directly allocate full-live capital.
4. **Allocation proposal**: evaluate one named ranking snapshot and retain
   eligible and excluded rows; create/read the rebalance and reconcile every
   line, cap, simulation, constraint, and rollback reference.
5. **Governed apply**: first prove a live increase without `approval_ref`
   fails. Apply the approved proposal once, reuse the same idempotency key
   after ambiguous transport outcomes, and poll the owning command to a
   terminal state.
6. **Authoritative readback**: refresh Capital/Fleet/binding truth and compare
   response identities and weights with the proposal and terminal command.
   Do not merge optimistic client state into this comparison.
7. **Emergency containment**: admit one safe risk-decreasing action; separately
   reject promotion and allocation-increase variants; refresh state to prove
   no unsafe side effect.
8. **Hosted UI**: repeat required reads/actions in authenticated desktop and
   mobile browsers, preserving request failures, degraded state, and bundle
   identity in the evidence set.

## Frontend Handoff

Implementation belongs in the separate `ajoe734/execute-plans` repository.
The parent evidence must show:

- `VITE_BFF_MODE=live`, the Pantheon dev BFF origin, and
  `VITE_BFF_FALLBACK=strict` in the deployed build;
- browser traffic terminates at the BFF, with no direct internal-service,
  runtime, registry, broker, fixture, seed, or mock fallback;
- admission, pending execution, terminal success/failure, and authoritative
  refreshed state render as distinct phases;
- missing IDs, evidence, stage, cap/exclusion reason, approval, or freshness
  render as blocked/unknown rather than a default or zero value;
- `401`, `403`, `409`, `422`, network failure, stale/degraded data, genuine
  empty results, and terminal command failure remain distinguishable; and
- an ambiguous write retry first queries the original intent and preserves its
  idempotency identity instead of issuing a new mutation.

Frontend source, tests, and build files must not be copied into Pantheon.
Legacy routes redirect to the matching workbench tab; diagnostic/readiness
pages link to the workbench but do not impersonate an action surface.

## Evidence Ledger

Use one row per positive or negative probe:

| Step | Pantheon / FE SHA | Role + viewport | Method/path or UI action | Idempotency + trace | Domain IDs | Admission | Terminal state | Authoritative readback | Required-request/fallback result | Artifact | Verdict / owner / recheck |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `<probe>` | `<deployed SHAs>` | `<role, API/desktop/mobile>` | `<surface>` | `<ids>` | `<persona/review/snapshot/rebalance/command>` | `<HTTP>` | `<state or n/a>` | `<query + fields>` | `<strict result>` | `<sanitized path>` | `<pass or blocked>` |

Mandatory negative rows are: real/live intent during paper creation; direct
paper-to-full-live mutation; live allocation increase without approval;
emergency promotion; emergency allocation increase; and strict-mode
fixture/mock substitution. A missing negative row is a blocking evidence gap.

## Parent Absorption Gate

Antigravity may absorb this packet into `PPL-ALLOC-009` only when:

- every dependency has a reviewed terminal or reviewed-supersession record,
  PR and merge SHA, validation, and required deployment identity;
- each join above is response-derived and no blocking query gap is hidden;
- admission, terminal execution, and authoritative readback have separate
  evidence verdicts;
- approval and containment negative probes fail closed;
- authenticated desktop and mobile strict-mode runs have zero substituted
  required requests; and
- every residual risk names severity, blocking status, owner, and objective
  expiry/recheck condition.

An unchecked condition keeps the parent blocked. This sidecar is not reviewer
approval, deployment evidence, or permission to mutate capital.

## Composition Boundary

Owned layer: task-scoped query-gap triage, executable operator checkpoints,
frontend fail-closed handoff, evidence shape, and parent absorption gate.

Not changing: L1 truth, route/schema contracts, BFF/runtime/frontend code,
registry or governance semantics, task dependency state, deployment, approval,
or capital state.

Composes with: the earlier `PPL-ALLOC-009` sidecar packets, the execution
packet, deployed Pantheon/Execute Plans evidence, and the parent owner's final
closeout archive. Parent owner `Antigravity` decides whether to absorb it;
parent reviewer `Claude` retains canonical closeout review.

## Reviewer Checklist

- [ ] Candidate surfaces are presented as unverified until deployed responses
      preserve the required joins.
- [ ] No missing query is replaced with client inference or synthetic truth.
- [ ] Operator checkpoints separate admission, execution, and readback.
- [ ] Frontend guidance is strict/live, BFF-only, and fail-closed.
- [ ] Negative probes and parent absorption conditions are explicit.
- [ ] The packet makes no canonical, implementation, deployment, approval, or
      capital-state claim.

## References

- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-009-closeout-dev-publish.md`
- `docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md`

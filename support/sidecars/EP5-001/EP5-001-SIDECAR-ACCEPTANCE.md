# EP5-001 Acceptance and Dependency Map (Sidecar)

**Parent Task**: `EP5-001` - Prepare the canary-ready execution path
**Parent Owner**: `Claude`
**Parent Reviewer**: `Codex`
**Parent Status**: `todo`
**Sidecar Task**: `EP5-001-SIDECAR-ACCEPTANCE`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `acceptance_packet`
**Generated**: `2026-04-22`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime / registry / governance implementations. It
> packages the acceptance targets, dependency map, and proof boundary for
> `EP5-001` so the parent owner can execute without accidentally overclaiming
> beyond stable `EP4`.

---

## 1. Executive Summary

`EP5-001` is the prerequisite-only slice between the archived `EP4` governed
paper packet and any future `EP5` canary/live proof. The repo already has a
stable `EP4` anchor, and several canonical records explicitly say that:

1. `EP5-001` should prepare the entry path only: broker/venue boundary, scaled
   capital gate, operator checklist, and rollback drill harness.
2. `EP5-001` must not silently claim real canary/live proof.
3. the first actual canary/live proof packet belongs to later gated work
   (`EP5-002` in planning records), not to this prerequisite slice.

This packet therefore does two things:
1. turns the parent task acceptance criteria into a reviewer-ready checklist
   anchored to current repo truth
2. maps the upstream `EP4` evidence anchors and downstream `EP5` gate that
   depend on this slice

---

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Current execution truth: `EP5-001` is active follow-on work owned by `Claude`; this sidecar is support-only and separate from the parent lifecycle. |
| `docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md` | Current execution-origin packet that keeps `EP5-001` as explicit follow-on preparation work and keeps `EP5-002` deferred. |
| `EXECUTION_PROOF_AND_MATURITY_LEVELS.md` | Canonical proof ladder stating `EP4` is stable and `EP5-001` is the next prerequisite slice. |
| `docs/deployment/ep4-evidence-packet.md` | Current proof boundary that explicitly leaves broker-side acknowledgement and canary/live rollback drill to `EP5-001`. |
| `docs/deployment/evidence/ep4-governed-paper/20260419T003720Z/README.md` | Archived `EP4` packet proving the governed paper baseline that `EP5-001` builds on top of. |
| `docs/02-architecture/consensus/sessions/phase7-2026-04-18-ep4-ep5-execution-proof/planning-session.json` | Planning provenance showing `EP5-001` as the prerequisite-only wave and `EP5-002` as the later proof wave. |
| `docs/reviews/2026-04-18-ep4-ep5-planning-entry-packet.md` | Gap inventory for canary/live readiness: real broker behavior, capital gating, operator approvals, and rollback drill under real execution. |

---

## 3. Repo-Current Truth Snapshot

| Truth item | Repo evidence | Implication for `EP5-001` |
|---|---|---|
| Stable `EP4` proof exists | `docs/deployment/evidence/ep4-governed-paper/20260419T003720Z/README.md` and `EXECUTION_PROOF_AND_MATURITY_LEVELS.md` | `EP5-001` can assume the governed paper baseline exists; it does not need to re-prove `EP4`. |
| `EP5-001` is prerequisite-only work | `docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md` and the phase-7 planning session | Parent work must stop at readiness artifacts and runnable prerequisite materials, not first canary/live proof. |
| Broker acknowledgement and canary/live rollback remain open | `docs/deployment/ep4-evidence-packet.md` scope boundary | Parent closeout must cover these as entry-path artifacts or harnesses; `EP4` evidence does not already satisfy them. |
| No canary/live proof packet exists yet | `EXECUTION_PROOF_AND_MATURITY_LEVELS.md` and `docs/reviews/2026-04-18-ep4-ep5-planning-entry-packet.md` | Any document or packet produced by the parent must preserve the "not yet EP5 proof" boundary. |

Inference note:
the first two parent acceptance items below remain open because the parent task
is still `todo` and no `EP5-001`-specific support artifacts currently exist in
the repo. That inference is based on current task state plus the absence of any
listed parent artifacts beyond the existing EP4 / execution-proof documents.

---

## 4. Parent Acceptance Checklist

Use this table to review `EP5-001` closure without confusing baseline evidence
with new prerequisite work.

| Parent acceptance target | Repo-current baseline | Required closeout evidence from parent owner | Status now |
|---|---|---|---|
| Canary ready prerequisites are documented as executable repo artifacts | Canonical docs already name the four required domains: broker/venue config boundary, scaled capital gate, operator approval checklist, rollback drill harness. | Add concrete repo-local artifact paths for each domain, with operator-usable instructions or scripts rather than planning-only prose. | PENDING |
| Rollback drill harness and operator checklist are runnable | The archived `EP4` paper packet proves rollback command flow and incident handling at paper stage, but planning records still list real canary/live rollback and operator signoff as open gaps. | Provide a runnable checklist and harness or rehearsal procedure, including invocation steps, expected inputs, and output/evidence location. | PENDING |
| Execution proof docs point at the prepared EP5 entry path without claiming EP5 proof | This guardrail is already present in `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`, the 2026-04-22 execution packet, and the `EP4` evidence packet scope boundary. | Preserve the same wording when adding new artifacts; no parent output may claim canary/live proof, broker fills, or operator signoff completion. | BASELINE PASS |

---

## 5. Scope Boundary — What `EP5-001` Must Not Claim

`EP5-001` is allowed to package readiness artifacts. It must not silently absorb
claims that belong to later proof work or to real infrastructure evidence not
yet present in the repo.

| Claim | Belongs to | Why it is out of scope here |
|---|---|---|
| First canary/live proof packet | `EP5-002` planning wave | The planning session and execution packet keep proof execution behind a later explicit gate. |
| Real broker acknowledgement, fills, slippage, and rejects under live/canary conditions | Later `EP5` proof evidence | The planning entry packet still lists these as unresolved real-order gaps. |
| Final operator signoff proving rollback readiness under canary/live conditions | Later `EP5` proof evidence | Current evidence only proves paper-stage rollback and incident flow. |
| Repo-wide declaration that `EP5` is achieved | Out of scope until proof exists | The canonical proof ladder still says the repo has stable `EP4` and no `EP5` proof packet. |

---

## 6. Dependency Map

### 6.1 Upstream Truth Anchors

| Dependency | Where recorded | Status | Relevance |
|---|---|---|---|
| Stable `EP4` governed paper packet (`OSS-004C` evidence) | `docs/deployment/evidence/ep4-governed-paper/20260419T003720Z/README.md` and `EXECUTION_PROOF_AND_MATURITY_LEVELS.md` | COMPLETE | Supplies the governed deployment, telemetry, kill-switch, and rollback baseline that `EP5-001` extends without re-proving. |
| `EP4` evidence scope boundary | `docs/deployment/ep4-evidence-packet.md` | COMPLETE | Explicitly reserves broker acknowledgement and canary/live rollback drill for `EP5-001`. |
| Phase-7 planning provenance | `planning-session.json` and `docs/reviews/2026-04-18-ep4-ep5-planning-entry-packet.md` | COMPLETE | Records the intended sequencing: stable `EP4` first, then prerequisite-only `EP5-001`, then later gated `EP5-002`. |
| Current execution-origin packet | `docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md` | COMPLETE | Keeps `EP5-001` visible as executable follow-on work without silently dispatching `EP5-002`. |

### 6.2 Downstream Consumers

| Task / consumer | Current board presence | Relationship to `EP5-001` |
|---|---|---|
| `EP5-002` | Planning provenance only; not materialized in current `ai-status.json` | The first canary/live proof packet should not start until `EP5-001` closes and a later explicit human gate is granted. |
| Future operator approval / canary run packets | Not yet materialized | They will consume the broker boundary, capital gate, operator checklist, and rollback drill harness produced here. |

### 6.3 Machine vs. Semantic Dependency Note

`ai-status.json` currently shows `EP5-001` with no machine-readable
`depends_on`. The dependency map above is therefore an evidence/provenance map,
not a request to mutate canonical task state. Reviewer guidance should respect
both truths:

1. the live board currently treats `EP5-001` as unblocked follow-on work
2. the planning and proof documents still place it downstream of stable `EP4`
   and upstream of later `EP5` proof

---

## 7. Suggested Parent Closeout Bundle

For the parent owner, a minimal truthful closeout bundle should contain:

1. one artifact that defines the real broker/venue config boundary and names any
   operator-owned prerequisites
2. one artifact that expresses scaled capital gate or ramp constraints
3. one runnable operator approval checklist
4. one runnable rollback drill harness or rehearsal procedure
5. doc links back into the execution-proof ladder that preserve the "prepared
   entry path only, not `EP5` proof" wording

If those outputs are missing, the parent task should not be approved as done,
even if the repo still truthfully claims stable `EP4`.

---

## 8. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | Only `support/sidecars/EP5-001/EP5-001-SIDECAR-ACCEPTANCE.md` is created by this sidecar. |
| No canonical truth edited | PASS | No L0/L1 policy docs, runtime code, registry code, or governance code are modified here. |
| `EP4` vs `EP5` boundary preserved | PASS | Packet keeps `EP5-001` as prerequisite-only work and leaves proof claims to later gated work. |
| Dependency map distinguishes board truth vs semantic provenance | PASS | Section 6 separates live `ai-status.json` state from planning/evidence sequencing. |

---

## 9. Handoff to Reviewer (`Claude`)

This sidecar is ready for review as the acceptance packet for `EP5-001`.

What it gives you:
1. a current-truth acceptance matrix showing which parent acceptance criteria are
   still pending versus already guarded by baseline docs
2. a dependency map that ties `EP5-001` back to stable `EP4` evidence and
   forward to later gated `EP5` proof
3. a scope boundary that prevents the parent task from overclaiming beyond
   prerequisite readiness

Recommended reviewer stance:
1. approve this sidecar if it accurately reflects the repo's current proof
   boundary and the parent task's prerequisite-only scope
2. when reviewing the eventual parent task, require runnable prerequisite
   artifacts and reject any closeout that merely restates existing `EP4`
   evidence without producing new `EP5-001` materials

---
*Generated by Codex as a sidecar `acceptance_packet` helper for `EP5-001`. This
file is a support artifact and does not modify canonical truth.*

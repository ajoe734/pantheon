# CW-03-COMMITTEE-001 Acceptance and Dependency Map (Sidecar)

**Parent Task**: `CW-03-COMMITTEE-001` - Publish Committee Board projection and sponsor decision authority  
**Parent Owner**: `Gemini`  
**Parent Reviewer**: `Claude`  
**Parent Status**: `todo`  
**Sidecar Task**: `CW-03-COMMITTEE-001-SIDECAR-ACCEPTANCE`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Claude`  
**Helper Kind**: `acceptance_packet`  
**Generated**: `2026-04-19`

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth,
> committee runtime behavior, registry state, BFF routes, or governance implementation. It
> packages the acceptance checklist, dependency map, and reviewer handoff for `CW-03` so the
> parent owner can absorb it into the main execution path later.
>
> Current repo state: `ai-status.json` still lists the parent `CW-03-COMMITTEE-001` as `todo`.
> The current source documents describe the contract and the missing backend gaps, but they do not
> claim those routes or projections are already implemented. This sidecar therefore does **not**
> claim the parent is done; it prepares the acceptance instrument for when the parent lands.

---

## 1. Purpose

This packet gives the assigned reviewer and the parent owner one compact support artifact that:

1. translates the parent acceptance wording into concrete route, field-shape, and authority checks
2. maps the direct task dependency from `ai-status.json` plus the workbench-local prerequisites
3. records the non-goal that matters most for this slice: the client must not derive a committee
   verdict from participant votes

---

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | durable truth for parent and sidecar ownership, current status, and the direct dependency on `CW-02-TRANSCRIPT-001` |
| `.orchestrator/task-briefs/cw_03_committee_001_sidecar_acceptance.md` | sidecar scope, reviewer, artifact path, and support-only guardrails |
| `docs/reviews/2026-04-19-architecture-team-input-gap-matrix.md` | architecture-team gap matrix that materialized `CW-03` and lists the minimum contract fields to lock |
| `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md` | canonical workbench packet family definition for `CW-03` surface scope, missing routes, command path, and module ordering |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` | frontend-side guardrail that verdict and write authority cannot be synthesized in the client shell |

---

## 3. Acceptance Checklist for Parent Task (`CW-03-COMMITTEE-001`)

Parent acceptance from `ai-status.json`:

1. `committee board routes are published`
2. `sponsor decision authority is explicit`
3. `synthesis summary is backend composed`

The table below turns those into reviewable checks.

| # | Acceptance criterion | Concrete check for reviewer | Current baseline |
|---|---|---|---|
| 1 | Committee board routes are published | `GET /api/v1/committees` exists and returns the board list surface with `committee_id`, `escalation_reason`, `quorum_state`, `consensus_state`, `linked_request_id`, `started_at`, pagination fields, and `meta.surfaces.committee_board`. | Pending in parent; `PACKET_FAMILY.md` and the gap matrix still mark the route as missing. |
| 2 | Committee board routes are published | `GET /api/v1/committees/:committee_id` exists and returns `committee_ref`, `participant_roster[]`, `escalation_reason`, `quorum_state`, `consensus_state`, `synthesis_summary`, `linked_request_id`, and `allowedActions.canRecordSponsorDecision`. | Pending in parent; the current packet family defines the expected shape but not an implemented route. |
| 3 | Sponsor decision authority is explicit | Sponsor CTA is backend-shaped: `allowedActions.canRecordSponsorDecision` controls visibility, and `POST /api/v1/operator/commands` accepts `RecordSponsorDecision` with `committee_id`, `sponsor_decision` (`approved` / `rejected` / `conditional`), and `rationale_ref`. | Pending in parent; authority and command shape are defined as missing backend work. |
| 4 | Synthesis summary is backend composed | `synthesis_summary` is a BFF object with `outcome`, `rationale_ref`, `evidence_refs[]`, and `dissent_refs[]`; the UI displays it but does not compute a verdict from `participant_roster[]` signals. | Pending in parent; the documents define this as a required object contract and an explicit non-goal for the client. |
| 5 | Committee board projection is canonical | The parent implementation promotes `committee_ref` identity, participant and referral semantics, quorum and consensus state, sponsor selection, and linked evidence from policy-only truth into a BFF-facing projection. | Pending in parent; currently described as a missing contract / projection gap. |

### Acceptance-critical non-goals

These are failure conditions for reviewer sign-off even if routes exist:

- Do not infer committee consensus or verdict from raw participant vote signals.
- Do not show sponsor decision CTA without `allowedActions.canRecordSponsorDecision`.
- Do not treat frontend shell state as canonical for quorum, consensus, or synthesis status.

---

## 4. Dependency Map

### 4.1 Durable task dependency from `ai-status.json`

| Dependency | Status | Why it matters for `CW-03` |
|---|---|---|
| `CW-02-TRANSCRIPT-001` | `done` | `CW-03` cites transcript ordering and actor identity as prerequisite evidence truth for committee state and evidence linkage. |

### 4.2 Workbench-local prerequisite chain from `PACKET_FAMILY.md`

```text
CW-01 Consult Request
  -> CW-02 Debate Transcript
       -> CW-03 Committee Board
```

| Upstream module | Relationship to `CW-03` | Evidence |
|---|---|---|
| `CW-01 Consult Request` | supplies `linked_request_id`, request identity, and request-to-session handoff semantics that the committee board references | `PACKET_FAMILY.md` orders `CW-01` before `CW-03` and says the committee projection requires stable request identity |
| `CW-02 Debate Transcript` | supplies ordered event evidence and actor identity that committee evidence and synthesis links rely on | `PACKET_FAMILY.md` orders `CW-02` before `CW-03`; `ai-status.json` also records it as the direct dependency |

### 4.3 Canonical policy themes the parent must absorb

| Theme | Why it matters for reviewer acceptance |
|---|---|
| `committee_ref` lifecycle and sponsor-selection semantics | the board projection cannot invent committee identity or sponsor flow locally |
| quorum and consensus state | the board list/detail routes must publish these states as backend truth |
| synthesis output and dissent linkage | reviewer should expect BFF-composed `synthesis_summary`, not a client aggregate |
| evidence linkage | committee detail must point back to canonical evidence refs rather than ad hoc UI-only links |

---

## 5. Reviewer Preflight

Before approving the parent, `Claude` should be able to answer "yes" to each item below:

| Check | Expected evidence |
|---|---|
| List route exists and is shaped | repo or review evidence shows `GET /api/v1/committees` with the board fields defined in Section 3 |
| Detail route exists and is shaped | repo or review evidence shows `GET /api/v1/committees/:committee_id` with `participant_roster[]`, `synthesis_summary`, and `allowedActions.canRecordSponsorDecision` |
| Sponsor authority is not implied | the write path is gated by `allowedActions` and uses the canonical operator-command pattern |
| Verdict is not synthesized client-side | frontend contract or implementation explicitly consumes `synthesis_summary` instead of deriving a verdict from participant outcomes |
| Readiness gate stays honest | the module remains blocked for Lovable or shell-only UI until the BFF routes, projection, and authority signals are real |

---

## 6. Artifact Inventory

| Artifact | Path | Role in this sidecar |
|---|---|---|
| This support packet | `support/sidecars/CW-03-COMMITTEE-001/CW-03-COMMITTEE-001-SIDECAR-ACCEPTANCE.md` | acceptance checklist, dependency map, and reviewer handoff |
| Architecture gap matrix | `docs/reviews/2026-04-19-architecture-team-input-gap-matrix.md` | source of the minimum `CW-03` contract fields that must be locked |
| Consultation packet family | `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md` | source of surface scope, backend gaps, workbench ordering, and readiness gate |
| Frontend SA | `docs/lovable/PANTHEON_FRONTEND_SA.md` | source of the client-side non-goal and authority guardrail |

---

## 7. Handoff to Reviewer (`Claude`)

This sidecar is ready for review as a support-only acceptance packet for `CW-03-COMMITTEE-001`.

What this packet gives you:

1. a precise checklist for what "committee board routes published" means in practice
2. a dependency map that distinguishes the durable task dependency (`CW-02`) from the broader
   workbench prerequisite chain (`CW-01` plus `CW-02`)
3. a reviewer guardrail that the frontend must not synthesize committee verdicts or sponsor
   authority

Recommended reviewer stance:

1. approve this sidecar if the packet accurately reflects the current contract gaps and dependency
   story
2. keep the parent task on the main line until those routes, authority signals, and backend-shaped
   synthesis fields are actually delivered

---

*Generated by Codex as a sidecar `acceptance_packet` helper for `CW-03-COMMITTEE-001`. This file is
a support artifact and does not modify canonical truth.*

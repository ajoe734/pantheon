# BP5-WB-008 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `BP5-WB-008-SIDECAR-ACCEPTANCE`
**Helper parent:** `BP5-WB-008` — Packetize the Consultation Workbench family
**Parent owner:** `Claude`
**Parent reviewer:** `Codex2`
**Prepared by:** `Codex`
**Reviewer:** `Claude`
**Date:** `2026-04-16`
**Status:** `done` — review_approved by Claude (2026-04-16); closed by Codex (2026-04-16)

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth, runtime
> implementation, BFF routes, registry state, or governance truth. It packages the current
> acceptance evidence for `BP5-WB-008` so the assigned reviewer can validate the Consultation
> Workbench packet family quickly and the parent owner can decide whether to absorb it into the
> main closeout path.
>
> Current repo state: `ai-status.json` still lists `BP5-WB-008` as `todo` under `Claude` ownership,
> but the current working tree already contains the parent packet family at
> `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md` and an approved review
> report at `docs/reviews/WB-008-review-codex.md`. This sidecar does not reconcile that parent-task
> status drift; it documents the acceptance and dependency evidence now present in the repo.

---

## 1. Purpose

This packet gave `Claude` a compact review surface for
`BP5-WB-008-SIDECAR-ACCEPTANCE` and now remains as the durable acceptance record for the parent
Consultation Workbench packet family:

1. confirm the parent acceptance wording against the current `CW-008` packet family and review
   report
2. show the dependency map behind the Consultation Workbench slice
3. inventory the current repo artifacts relevant to parent closeout
4. record the remaining status-reconciliation note that belongs to the parent owner, not this
   sidecar

---

## 2. Acceptance Criteria Checklist

From `ai-status.json` → `BP5-WB-008` acceptance field:

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Consultation Workbench surfaces have canonical packets, queue semantics, and approval or debate dependencies | **MET** | `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md` defines four canonical modules (`CW-01` through `CW-04`), each with surface scope, backend gaps, module ordering, and cross-module dependency rules. Queue/board semantics are explicit in `CW-01` request list and `CW-03` committee board; debate and evidence ordering are explicit in `CW-02`; downstream governance handoff is explicit in `CW-04`. `docs/reviews/WB-008-review-codex.md` records an approved review on 2026-04-16 after the final write-boundary fix. |
| 2 | no consultation screen is marked Lovable-ready before its BFF routes and decision semantics are explicit | **MET** | The `CW-008` header marks the family `not ready`, every module row remains `not ready`, and each module has an explicit Lovable readiness gate that requires real BFF routes, `allowedActions` authority signals, and locked field shapes before any Lovable handoff can open. The review report explicitly keeps that gate intact. |

### Module-by-module acceptance coverage

| Module | Acceptance-relevant evidence now present |
|---|---|
| `CW-01 Consult Request` | canonical request composer/list/detail packet language; request lifecycle; request-to-session handoff; target taxonomy; explicit missing write/read routes |
| `CW-02 Debate Transcript` | ordered transcript timeline semantics; append-only `TranscriptEvent` contract requirement; actor-labeling rule; inline evidence-link rule; degraded partial-transcript semantics |
| `CW-03 Committee Board` | board or queue surface; quorum and consensus state; sponsor decision CTA gate; synthesis-summary contract; committee projection dependency on request identity plus transcript ordering |
| `CW-04 Red-team Memo` | memo list/detail semantics; L3-anchored `ConsultMemo` lifecycle guardrails; evidence drawer; governance-review handoff gate; explicit session-to-memo mapping gap |

**Overall verdict:** current repo evidence satisfies the parent acceptance wording at the packet
family level. Formal parent-task status advancement still belongs to the parent owner because
`ai-status.json` has not yet been reconciled with the working-tree delivery evidence.

---

## 3. Dependency Map

### Upstream task dependencies

| Dependency | Status in `ai-status.json` | Relevance to `BP5-WB-008` |
|---|---|---|
| `BP5-SVC-003` — Realize the ApprovalDecision governance API and audit flow | `done` | establishes the review and audit vocabulary the Consultation packet family cites for downstream review handoff and backend-shaped authority signals; `CW-04` explicitly uses a governance-review handoff gate instead of UI-local follow-up logic |
| `BP5-SVC-012` — Realize the EvolutionDecision service and governance read path | `done` | provides the canonical governance/evolution read-path pattern that Consultation outputs are expected to feed into; keeps downstream decision evidence and review chains outside the Consultation UI |
| `BP5-SVC-014` — Realize persona platform and consultation read surfaces | `done` | direct foundation for `ConsultPolicy`, `consult` / `committee` / `red_team` session types, consultation read surfaces `CS-01` to `CS-06`, and `SessionPersona.metadata.consultation.*` semantics that `CW-008` builds on |

No unresolved upstream dependency blocker remains for the packet-family slice. The remaining gaps
are the workbench-specific BFF routes, event contracts, and board or memo read models listed inside
`CW-008`.

### Internal workbench dependency chain

```text
CW-01 Consult Request
  -> CW-02 Debate Transcript
       -> CW-03 Committee Board
       -> CW-04 Red-team Memo
```

| Position | Module | Why it must appear in this order |
|---|---|---|
| 1 | `CW-01 Consult Request` | defines the foundational request object, target taxonomy, and request-to-session handoff that every downstream module references |
| 2 | `CW-02 Debate Transcript` | defines the ordered conversation evidence model and actor-ordering semantics needed by both committee and red-team views |
| 3 | `CW-03 Committee Board` | adds policy-driven committee projection, sponsor state, and synthesis output on top of request identity plus transcript evidence ordering |
| 4 | `CW-04 Red-team Memo` | publishes adversarial findings against the same request and evidence chain; depends on stable request identity and session evidence semantics, but not on committee-board ownership |

### Critical backend-gap themes carried by the parent packet

| Theme | Modules affected | Why it matters |
|---|---|---|
| request-write truth and request lifecycle | `CW-01`, downstream identity for all modules | without a canonical `ConsultRequest` write/read model, the workbench cannot honestly claim a first-class initiation surface |
| append-only transcript and actor-resolution semantics | `CW-02`, `CW-03`, `CW-04` | committee and red-team surfaces must not synthesize actor identity, event order, or evidence links client-side |
| committee board projection and sponsor-decision authority | `CW-03` | quorum, consensus, sponsor assignment, and synthesis output must come from the BFF rather than from client aggregation |
| red-team memo read model and governance handoff gate | `CW-04` | memo lifecycle and downstream review CTA must be backend-shaped, not inferred from raw consultation state |

---

## 4. Artifact Inventory

| Artifact | Path | Current role |
|---|---|---|
| Consultation packet family | `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md` | primary parent deliverable; defines all four Consultation Workbench modules, backend gaps, internal order, promotion criteria, and cross-cutting rules |
| Parent review report | `docs/reviews/WB-008-review-codex.md` | records Codex review approval on 2026-04-16 and confirms the remaining write-boundary inconsistency was resolved |
| Consultation surface contract | `services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md` | canonical read-surface baseline (`CS-01` to `CS-06`) that `CW-008` builds on without forking |
| Persona runtime model | `PERSONA_RUNTIME_MODEL.md` | canonical source for `consult`, `committee`, and `red_team` session types plus `ConsultPolicy` and `metadata.consultation.*` vocabulary |
| Multi-persona aggregation policy | `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md` | canonical policy source for committee escalation, sponsor selection, synthesis rules, quorum, consensus, and `committee_ref` semantics |
| Phase-3 workbench backlog | `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` | backlog source for Wave 4 placement, module ordering, and the Consultation Workbench dependency story |
| This support artifact | `support/sidecars/BP5-WB-008/BP5-WB-008-SIDECAR-ACCEPTANCE.md` | acceptance packet for the sidecar review loop |

---

## 5. Closeout Notes For Reviewer And Parent Owner

### 5.1 Parent status drift remains unresolved here

`ai-status.json` still shows `BP5-WB-008` as `todo`, while the working tree already contains both
the packet family and an approved review report. This sidecar does not change parent ownership,
reviewer assignment, or parent status. That reconciliation belongs to the parent owner.

### 5.2 Reviewer identity drift should be acknowledged, not silently normalized

The current parent task in `ai-status.json` names `Codex2` as reviewer, while the existing repo
review report is authored by `Codex`. This packet does not rewrite history or reassign the parent.
It packages the current evidence so `Codex2` can decide whether the support packet is sufficient as
the parent closeout scaffold or whether a fresh reviewer pass is needed upstream.

### 5.3 Lovable readiness remains intentionally false

This is not a defect in the parent packet. It is the core acceptance outcome: the Consultation
Workbench now has canonical packet language, dependency ordering, and explicit backend gaps without
pretending that BFF routes, transcript ordering truth, committee-board projection, or memo read
models already exist.

---

## 6. Sidecar Scope Declaration

- No canonical L1 or L2 document was modified by this sidecar
- No BFF route, service implementation, registry file, or governance implementation was created or modified by this sidecar
- No parent packet-family file or review file was edited by this sidecar
- The only new artifact produced by this slice is this support packet
- Parent absorption and any parent-task status change remain the parent owner's responsibility

---

## 7. Review Outcome and Final Disposition

Final reviewer of record: `Claude`

Review outcome recorded in `ai-status.json`:

- the `CW-008` packet family satisfies both parent acceptance criteria
- the dependency map matches durable task state for `BP5-SVC-003`, `BP5-SVC-012`, and `BP5-SVC-014`
- the artifact inventory is complete for this support-only slice
- the parent status drift is correctly framed as owner follow-up rather than silently normalized by
  the sidecar

Reviewer-path note:

- initial review handoff targeted `Codex2`
- the orchestrator auto-reassigned review through `Qwen`
- final approval was issued by `Claude` on `2026-04-16T17:07:53Z`

Final disposition for this sidecar:

1. The support packet is accepted as accurate and complete.
2. No canonical truth, runtime implementation, or parent artifact needed modification from this
   slice.
3. Parent absorption and any `BP5-WB-008` status reconciliation remain the parent owner's
   responsibility.

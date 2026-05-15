# KW-02-NOTES-001 Acceptance Packet (Sidecar)

**Parent Task**: `KW-02-NOTES-001` — Publish Research Note ownership and attachment contract
**Parent Owner**: `Claude`
**Parent Reviewer**: `Codex2`
**Parent Status**: `in_progress`
**Sidecar Task**: `KW-02-NOTES-001-SIDECAR-ACCEPTANCE`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `acceptance_packet`
**Generated**: `2026-04-19`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime / registry / governance implementations.

---

## 1. Executive Summary

`KW-02-NOTES-001` is the Wave 3 contract-expansion task for the Knowledge
Workbench research-notes surface. The parent task has not reached review yet;
this sidecar therefore prepares the acceptance instrument and dependency map
that the parent owner and reviewer can use once the contract lands.

Based on the task brief, `ai-status.json`, the architecture gap matrix, and the
Knowledge Workbench packet family, `KW-02` must lock four things before the
notes surface can be treated as truthful:

1. Publish `POST /api/v1/knowledge/notes`.
2. Publish `GET /api/v1/knowledge/notes`.
3. Publish `GET /api/v1/knowledge/notes/{note_id}`.
4. Define the research-note ownership and attachment contract so the UI no
   longer invents taxonomy client-side.

---

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Confirms the parent and sidecar task identities, owners, reviewers, acceptance targets, and downstream dependency truth |
| `.orchestrator/task-briefs/kw_02_notes_001_sidecar_acceptance.md` | Confirms this slice is support-only and scoped to an acceptance packet plus dependency map |
| `docs/reviews/2026-04-19-architecture-team-input-gap-matrix.md` | Canonical Wave 3 gap statement for KW-02 and the minimum field-level contract that must be locked |
| `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md` | Defines the KW-02 surface scope, backend gaps, packetization prerequisite, and Lovable readiness gate |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` | Confirms the frontend shell is still blocked and must not invent attachment taxonomy locally |

---

## 3. Parent Acceptance Checklist

The parent task acceptance in `ai-status.json` is:

1. `note create list and detail routes are published`
2. `ownership and attachment semantics are explicit`
3. `notes no longer rely on client invented taxonomy`

Expanded into repo-checkable verification:

| # | Criterion | Verification target | Status |
|---|---|---|---|
| 1 | `note_create_route_published` | `POST /api/v1/knowledge/notes` exists and defines note body shape, attachment target fields, and initial ownership metadata | ⏳ Pending — parent still `in_progress` |
| 2 | `note_list_route_published` | `GET /api/v1/knowledge/notes` exists and supports `owner_ref`, `attachment_type`, `attachment_ref`, `page_token`, `page_size` | ⏳ Pending |
| 3 | `note_detail_route_published` | `GET /api/v1/knowledge/notes/{note_id}` exists and returns note content, owner metadata, attachment target, linked evidence refs, and linked memory anchors where present | ⏳ Pending |
| 4 | `owner_ref_is_canonical` | `owner_ref` is explicitly shaped by backend contract rather than inferred from workspace, path naming, or creator initials | ⏳ Pending |
| 5 | `attachment_taxonomy_locked` | Attachment target taxonomy is explicit and limited to `research_ticket`, `persona`, `strategy_spec`, or `free_standing` | ⏳ Pending |
| 6 | `referential_integrity_rules_defined` | Contract defines how attachment refs must resolve and what qualifies as a valid `free_standing` note | ⏳ Pending |
| 7 | `linked_evidence_refs_shaped` | Note create/detail semantics define how linked evidence refs appear instead of leaving source context implicit | ⏳ Pending |
| 8 | `degradation_surface_shaped` | List/detail responses expose `meta.surfaces.research_note_list` and `meta.surfaces.research_note_detail` so the UI can render degraded or unavailable states truthfully | ⏳ Pending |
| 9 | `frontend_no_longer_invents_taxonomy` | Frontend-facing contract is sufficient for `/knowledge/notes` and `/knowledge/notes/:note_id` to stay blocked-shell only until backend shapes are real | ⏳ Pending |
| 10 | `kw01_anchor_dependency_respected` | Note linkage uses the stable institutional-memory identity model introduced by `KW-01-FOUNDATION-001` rather than client-invented labels | ⏳ Pending |
| 11 | `sidecar_stayed_support_only` | This helper creates support material only and does not mutate canonical truth or runtime implementation | ✅ Verified |

---

## 4. Dependency Map

### 4.1 Upstream Dependency

| Task ID | Status | Relationship |
|---|---|---|
| `KW-01-FOUNDATION-001` | `done` | `KW-02` depends on the institutional-memory identity contract so notes can anchor to stable memory entries instead of client-invented labels |

### 4.2 Direct Downstream Dependency

| Task ID | Status | Why it depends on `KW-02` |
|---|---|---|
| `KW-03-EVIDENCE-001` | `todo` | Evidence refs need note list/detail routes and the note ownership/attachment contract for source-context resolution and linked-note lookup |

### 4.3 Indirect / Fan-Out Impact

| Task ID | Status | Dependency chain |
|---|---|---|
| `KW-04-INSIGHT-001` | `todo` | `KW-04` depends on `KW-03`, and `KW-03` depends on `KW-02`; insight synthesis cannot stay truthful if note and evidence joins are still client-derived |
| `KW-05-STRATEGY-SPEC-001` | `todo` | `KW-05` depends on `KW-03` and `KW-04`; unstable note attachment semantics would propagate into spec citations and compare views |

### 4.4 Readiness Verdict

`KW-02` is the first contract-expansion step that turns Knowledge Workbench
notes from a blocked shell into a real backend-owned surface. The dependency
story is therefore:

`KW-01` identity truth -> `KW-02` note ownership / attachment truth ->
`KW-03` evidence source-context truth -> `KW-04` / `KW-05` synthesis truth.

If `KW-02` slips or publishes a weak contract, the rest of the Knowledge
Workbench family inherits client-side join pressure.

---

## 5. Parent Reviewer Checklist

When the parent owner hands `KW-02-NOTES-001` to `Codex2`, the reviewer should
be able to answer the following without guessing:

| Check | What to look for |
|---|---|
| Route completeness | All three routes exist and use one consistent note object vocabulary |
| Ownership truth | `owner_ref` is backend-shaped and documented, not reconstructed by UI context |
| Attachment truth | Attachment taxonomy is explicit, finite, and coupled to referential-integrity rules |
| Linked context truth | Linked evidence refs and linked memory anchors are part of the contract rather than ad hoc enrichments |
| Degradation honesty | Notes list/detail responses surface degraded and unavailable states instead of collapsing to empty data |
| Frontend boundary | The contract removes the need for client-invented note taxonomy or local joins |

---

## 6. Risks and Failure Modes

| Risk | Impact | Mitigation |
|---|---|---|
| Routes land without the ownership or attachment contract | UI can call endpoints but still has to infer semantics locally | Treat ownership and attachment semantics as acceptance-blocking, not follow-up polish |
| Attachment taxonomy is wider or vaguer than the packet family allows | Downstream note filtering and evidence linkage become unstable | Keep the taxonomy explicit: `research_ticket`, `persona`, `strategy_spec`, `free_standing` |
| `free_standing` is left undefined | Notes may become a dumping ground for unresolvable attachments | Require explicit semantics for when a note may be unattached and how it is still discoverable |
| Degradation metadata is omitted | Blocked or degraded notes surfaces may look like empty states | Require `meta.surfaces.research_note_list` and `meta.surfaces.research_note_detail` on the read routes |
| Parent acceptance is assumed from the sidecar | Reviewers may mistake this packet for parent implementation evidence | This packet is only a support artifact; parent acceptance still depends on real route and contract publication |

---

## 7. Reviewer Handoff (`Claude`)

This sidecar is ready for review as a support-only acceptance packet for
`KW-02-NOTES-001`.

What it gives you:

1. A concrete acceptance checklist derived from the parent task's stated
   acceptance and the Knowledge Workbench packet-family requirements.
2. A dependency map showing `KW-02` as the bridge between `KW-01` identity
   truth and `KW-03` evidence source-context truth.
3. A reviewer checklist and risk table that the parent owner can reuse when
   handing `KW-02` to `Codex2`.

Recommended next step:

1. Approve this sidecar if it accurately reflects the current parent-task scope
   and dependency reality.
2. Keep the parent task in `in_progress` until the routes and ownership /
   attachment contract are actually published.
3. When the parent is ready for formal review, reuse Section 3 and Section 5 as
   the acceptance instrument.

---

*Generated by Codex as a sidecar `acceptance_packet` helper for
`KW-02-NOTES-001`. This file is a support artifact and does not modify
canonical truth.*

# BP5-WB-006 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `BP5-WB-006-SIDECAR-ACCEPTANCE`
**Helper parent:** `BP5-WB-006` — Packetize the Knowledge Workbench family
**Parent owner:** `Qwen`
**Parent reviewer:** `Codex`
**Prepared by:** `Claude`
**Reviewer:** `Codex`
**Date:** `2026-04-16`
**Status:** `done`

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth, runtime
> implementation, BFF routes, or governance truth. It records the formal acceptance criteria,
> dependency map, and module-ordering checklist for BP5-WB-006 so the assigned reviewer can judge
> the parent slice quickly and the parent owner has a compact acceptance scaffold when the packet
> family is delivered.
>
> Current repo state: `BP5-WB-006` is listed as `todo` under Qwen ownership with Codex as the
> assigned reviewer. All three upstream dependencies are `done`:
> - `BP5-SVC-010` (lineage read model and performance service path)
> - `BP5-SVC-011` (incident and postmortem evidence services)
> - `BP5-SVC-014` (persona platform and consultation read surfaces)
>
> The Knowledge Workbench backlog is established in
> `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md`
> as Wave 3, covering five modules: Institutional Memory → Research Notes → Evidence Refs →
> Insight Cards → Strategy Spec. Currently no BFF route backs any Knowledge Workbench screen.

---

## 1. Purpose

This packet gives `Codex` a compact review surface for `BP5-WB-006-SIDECAR-ACCEPTANCE` and serves
as a ready-made acceptance scaffold for the current parent-task review of `BP5-WB-006`:

1. A criterion-by-criterion acceptance checklist for the Knowledge Workbench packet family
2. A concrete file / artifact inventory the parent reviewer should confirm once the parent task delivers the packet family
3. A dependency map showing what this task unblocks and how it relates to the BFF and service layer
4. Module ordering rules from the backlog that the packet plan must preserve

The central structural requirement: **BP5-WB-006 must deliver real canonical packets with explicit
BFF route prerequisites for each of the five Knowledge modules. No Knowledge screen may be handed to
Lovable before its BFF surface and packet family exist. Blueprint-level direction alone is not
sufficient to mark this task done.**

---

## 2. Acceptance Checklist

Formal acceptance criteria from the phase-5 planning session (sourced from `ai-status.json`):

- **AC-1:** `Knowledge Workbench modules have canonical packets and explicit BFF or read-model prerequisites`
- **AC-2:** `module ordering from Institutional Memory to Strategy Spec is preserved in the packet plan`

### AC-1: Knowledge Workbench modules have canonical packets and explicit BFF or read-model prerequisites

| Check | Evidence needed | Status |
|---|---|---|
| Institutional Memory has a canonical packet entry | reviewer should confirm a packet record or handoff file exists for list shell, detail shell, lifecycle state machine, and filter spec | PENDING |
| Institutional Memory lists its BFF prerequisites explicitly | `GET /api/v1/knowledge/memory` and `GET /api/v1/knowledge/memory/{entry_id}` must be named as required routes before Lovable can act on this module | PENDING |
| Research Notes has a canonical packet entry | reviewer should confirm a packet record exists covering note ownership and attachment semantics, list shell, and detail shell | PENDING |
| Research Notes lists its BFF prerequisites and dependency on Institutional Memory identity schema | `POST /api/v1/knowledge/notes` and `GET /api/v1/knowledge/notes/{note_id}` must be named; attachment semantics must call out the dependency on Institutional Memory's identity schema | PENDING |
| Evidence Refs has a canonical packet entry | reviewer should confirm a packet record exists covering evidence reference shape, link type taxonomy, and credibility metadata display | PENDING |
| Evidence Refs lists its BFF prerequisites and both upstream dependencies | `GET /api/v1/knowledge/evidence` and `GET /api/v1/knowledge/evidence/{ref_id}` must be named; the packet must call out dependency on Institutional Memory anchor entity AND Research Notes source-document context | PENDING |
| Insight Cards has a canonical packet entry | reviewer should confirm a packet record exists covering card identity, display contract (header, body, linked sources), and filter semantics | PENDING |
| Insight Cards lists its BFF prerequisites and aggregation input dependencies | an insight aggregation endpoint and a card-surface read model must be named; the packet must call out Institutional Memory and Evidence Refs as aggregation inputs | PENDING |
| Strategy Spec has a canonical packet entry | reviewer should confirm a packet record exists covering spec lifecycle states (draft, approved, deprecated), versioning semantics, citation display, and diff/compare surface | PENDING |
| Strategy Spec lists its BFF prerequisites and both upstream dependencies | strategy-spec list and versioned detail routes must be named; the packet must call out Institutional Memory for lineage AND Evidence Refs for backing citations | PENDING |
| No Knowledge module is marked Lovable-ready without a confirmed BFF route | reviewer should confirm `Lovable readiness: false` or equivalent is preserved for all five modules until BFF routes are confirmed as implemented (not just planned) | PENDING |
| Lovable-readiness gate is explicit in the packet family | the delivery must not silently promote any Knowledge module to Lovable-ready; each module's readiness state must be documented and justified | PENDING |

**AC-1 assessment (pre-implementation):** the backlog in
`pantheon-console-workbench-backlog.md` establishes the canonical BFF route names and
packetization prerequisites for each of the five Knowledge modules. The parent reviewer
must confirm these are encoded as explicit packet entries — not left as backlog-only
narrative or partial cross-references.

### AC-2: Module ordering from Institutional Memory to Strategy Spec is preserved in the packet plan

| Check | Evidence needed | Status |
|---|---|---|
| Institutional Memory is positioned first in the packet plan | it is the foundational identity store; all other modules reference it for anchoring, lineage, and linked-entity resolution | PENDING |
| Research Notes is positioned second | it depends on the Institutional Memory identity schema to define attachment semantics | PENDING |
| Evidence Refs is positioned third | it references both memory entries and research notes; it cannot define link type taxonomy until both inputs are stable | PENDING |
| Insight Cards is positioned fourth | it is the synthesis layer over memory, notes, and evidence; filter and display semantics cannot be defined until input read models are stable | PENDING |
| Strategy Spec is positioned fifth | it is the formal specification viewer that cites evidence and traces lineage through memory; versioning and diff semantics build on the full knowledge graph | PENDING |
| Internal ordering rationale is documented in the packet plan | reviewer should confirm the ordering is not just sequential numbering but carries an explicit statement of why each module depends on the one before it | PENDING |

**AC-2 assessment (pre-implementation):** the ordering is fully specified in the backlog's
internal dependency table and wave ordering table. The reviewer must confirm the packet plan
preserves this sequencing with justification, not just an arbitrary list order.

---

## 3. Expected Artifact Inventory

The following artifacts should be delivered or updated when the parent task is complete. The reviewer
should confirm presence and confirm each artifact's role matches the description.

| Expected artifact | Expected role |
|---|---|
| A packet handoff file or section for `Institutional Memory` in `docs/pantheon-handoffs/` | Canonical screen packet covering list shell, detail shell, lifecycle state machine, filter spec, and explicit BFF route prerequisites |
| A packet handoff file or section for `Research Notes` in `docs/pantheon-handoffs/` | Canonical screen packet covering note ownership, attachment semantics, list shell, detail shell, and BFF prerequisites with Institutional Memory dependency |
| A packet handoff file or section for `Evidence Refs` in `docs/pantheon-handoffs/` | Canonical screen packet covering evidence reference shape, link type taxonomy, credibility metadata, and BFF prerequisites with both upstream dependencies called out |
| A packet handoff file or section for `Insight Cards` in `docs/pantheon-handoffs/` | Canonical screen packet covering card identity, display contract, filter semantics, insight aggregation endpoint name, and aggregation input dependencies |
| A packet handoff file or section for `Strategy Spec` in `docs/pantheon-handoffs/` | Canonical screen packet covering spec lifecycle, versioning semantics, citation display, diff surface, and BFF prerequisites with both upstream dependencies called out |
| Updated summary table in `pantheon-console-workbench-backlog.md` for Knowledge Workbench | The five Knowledge rows should reflect packet status: if packets are now defined, the `Missing screen specs` column should be updated from `all screens` to the specific remaining gaps |
| Internal ordering table or equivalent in the packet plan | An explicit module ordering document or table that preserves the Institutional Memory → Research Notes → Evidence Refs → Insight Cards → Strategy Spec sequence with dependency rationale |

> Note: the parent owner may deliver these as separate files or as sections within a single
> Knowledge Workbench packet family document. The reviewer should evaluate substance, not enforce
> a specific file layout.

---

## 4. Dependency Map

### 4.1 Upstream dependencies already satisfied

| Dependency | Status | Relevance to BP5-WB-006 |
|---|---|---|
| `BP5-SVC-010` | done | The lineage read model is the backbone of evidence tracking and memory-entry lineage that Knowledge Workbench modules will need to cite; Insight Cards and Strategy Spec in particular require the normalized lineage read service to be credible rather than blueprint-only |
| `BP5-SVC-011` | done | The incident and postmortem evidence service establishes canonical evidence reference shapes that Knowledge Workbench's Evidence Refs module must be packet-compatible with; the evidence linkage semantics defined in BP5-SVC-011 constrain how Evidence Refs and Insight Cards can be defined |
| `BP5-SVC-014` | done | The persona platform and consultation read surfaces establish the BFF path pattern that Knowledge Workbench will need to follow for new BFF routes; persona identity is also relevant for note ownership (Research Notes: whose note is attached to which persona) |

### 4.2 Direct downstream unblocked by BP5-WB-006

| Task | How it depends on BP5-WB-006 |
|---|---|
| Future Lovable implementation task for Knowledge Workbench screens | no Knowledge screen may go to Lovable before BP5-WB-006 defines the canonical packet family; this task is the gate |
| Future BFF route definition slices for Knowledge Workbench | explicit BFF route naming in the packet family is the input to any BFF implementation slice; BP5-WB-006 establishes what needs to be built |
| Any analytics or governance surface that needs to cite canonical knowledge records | Institutional Memory, Evidence Refs, and Strategy Spec semantics defined here become the stable reference shape for adjacent query and reporting surfaces |

### 4.3 Adjacent consumers that benefit once packet semantics are accepted

| Consumer | Benefit |
|---|---|
| Research Workbench (BP5-WB-005 family) | Research tickets and experiments often cite evidence and produce research notes; canonical Knowledge Workbench packet semantics make cross-workbench linking explicit |
| Consultation Workbench (BP5-WB-008) | Consultation records and committee outputs may reference institutional memory and strategy specs; the Knowledge Workbench packet family establishes stable reference shapes for these cross-workbench citations |
| Governance Workbench | Evidence citation in approval decisions and evolution records benefits from canonical Evidence Refs packet semantics |
| BFF / operator query layer | Once the Knowledge Workbench BFF routes are named in the packet family, the BFF layer has explicit route targets rather than blueprint-only knowledge endpoints |

### 4.4 Policy and backlog sources the parent reviewer must consult

| Source | Relevant sections | What to confirm |
|---|---|---|
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` | Knowledge Workbench section (§226+): module descriptions, current state, packetization prerequisites, missing screen specs, BFF prerequisites table, internal ordering table | The five module packets match the content and prerequisites documented here; no module has been promoted to Lovable-ready without a confirmed BFF route |
| `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` | Sections on lineage edge shape and governance evidence linkage | Evidence Refs packet semantics should be compatible with canonical lineage evidence shapes; the packet must not define a novel evidence reference shape that diverges from the lineage model |
| `PERSONA_RUNTIME_MODEL.md` | Persona identity and session model | Research Notes ownership model (which persona or entity owns a note) must be compatible with canonical persona identity semantics |
| `OPENCLAW_RUNTIME_CONTRACT.md` | Adapter boundary and data passing contract | If any Knowledge module routes data through OpenClaw adapters, the packet should call out where the OpenClaw boundary sits |

---

## 5. Open Acceptance Questions for the Parent Reviewer

The following items are not blocking this sidecar's review but must be resolved during parent-task
review:

1. **Net-new BFF routes vs. read-model reuse** — the backlog states all five Knowledge Workbench
   modules need net-new BFF routes before packetization can begin. The reviewer should confirm
   whether any Knowledge module can reuse an existing route (e.g., from BP5-SVC-010 lineage reads
   or BP5-SVC-011 evidence service), or whether all five require genuinely new route surfaces.

2. **Evidence Refs and BP5-SVC-011 compatibility** — BP5-SVC-011 delivered canonical incident and
   postmortem evidence services. The reviewer should confirm whether Knowledge Workbench's Evidence
   Refs module can reuse or extend the evidence reference shape from BP5-SVC-011, or whether it
   defines a separate knowledge-domain evidence shape that must be explicitly reconciled.

3. **Strategy Spec versioning model** — the backlog calls for spec lifecycle states (draft, approved,
   deprecated) and versioning semantics. The reviewer should confirm whether the packet defines
   a concrete versioning approach (e.g., append-only version records, pointer model) or defers this
   to a future slice.

4. **Insight Cards aggregation endpoint** — the backlog notes this module requires an insight
   aggregation endpoint that does not currently exist. The reviewer should confirm whether the packet
   family names a specific aggregation route and owner, or whether it marks this as a BFF gap that
   must be resolved before Lovable readiness.

5. **Wave 3 sequencing boundary** — the backlog establishes Knowledge Workbench as Wave 3, meaning
   it should not start packetization until Wave 2 workbench work (Governance, Evolution, Operator
   Wave 2) has stable packet families. The reviewer should confirm whether this sequencing is
   honored or whether the parent task explicitly documents a rationale for advancing in parallel.

---

## 6. Sidecar Scope Declaration

This file is a support artifact only.

- No canonical L1 or L2 document was modified by this sidecar
- No BFF route, service, or runtime implementation file was created or modified by this sidecar
- No Knowledge Workbench screen packet was defined, modified, or superseded by this sidecar
- No workbench backlog entry was promoted to Lovable-ready by this sidecar
- The only artifact created by this slice is this reviewer packet
- Once reviewed and approved by `Codex`, this packet is available to the current parent reviewer
  as a compact acceptance scaffold when the `BP5-WB-006` Knowledge Workbench packet family is
  delivered

---

## 7. Owner Closeout Checkpoint

**Closeout date:** `2026-04-16`
**Closed by:** `Claude` (owner)

Codex review approved with note: "內容完整；僅修正 support packet 內過時的 reviewer/owner/status/handoff 文案以反映現況；acceptance checklist、dependency map 與 backlog/BFF 來源一致，且仍維持 support-only 範圍。"

All acceptance criteria for this sidecar are satisfied:
- AC-1 checklist (12 checks across all 5 Knowledge modules with BFF prerequisite gates) — authored
- AC-2 module ordering checklist (6 checks enforcing IM → Notes → Evidence → Cards → Spec) — authored
- Expected artifact inventory (7 items) — authored
- Full dependency map (3 upstream done, downstream consumers, 4 policy sources) — authored
- 5 open reviewer questions for parent-task review — authored

No canonical truth was modified. This packet is formally closed and available as an acceptance
scaffold for the `BP5-WB-006` parent task (Knowledge Workbench packet family).

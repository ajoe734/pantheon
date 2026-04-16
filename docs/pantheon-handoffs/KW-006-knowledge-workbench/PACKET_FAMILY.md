# KW-006 Knowledge Workbench — Canonical Packet Family

## Header

- Packet family ID: `KW-006`
- Workbench: Knowledge Workbench
- Phase origin: `BP5-WB-006`
- Lovable readiness: **not ready** — Institutional Memory has a design note and schema, and Strategy Spec has a canonical object schema, but none of the five modules yet has a canonical BFF route or a workbench-ready packet contract
- Recommended wave: Wave 3 — after Operator Console (Waves 1-2) and Persona Workbench (Waves 1-2) packetization are settled
- Owner: Claude
- Reviewer: Codex

---

## Objective

Expose institutional memory, evidence, strategy specs, and durable research notes as navigable, queryable product surfaces backed by canonical BFF routes. The Knowledge Workbench must not rely on raw schema browsing, direct retrieval-facade calls, or client-side joins across memory, notes, evidence, insight, and spec data.

---

## Existing Pantheon Support (pre-conditions)

Before any Knowledge Workbench module can be packetized, the following artifacts must be treated as known truth:

| Artifact | Location | What it defines |
|---|---|---|
| `Pantheon Memory Layer Design Note` | `services/memory/MEMORY_LAYER_DESIGN_NOTE.md` | Canonical Memory Plane object split (`PersonaMemory`, `InstitutionalMemoryEntry`), write authority, retrieval facade (`GET /memory/retrieve`), and the rule that institutional knowledge is written by owning services rather than sessions |
| `InstitutionalMemoryEntry` schema | `services/memory/institutional_memory_entry.schema.json` | Canonical shared-memory object fields such as `entry_id`, `knowledge_type`, `content.headline`, `content.body`, `scope`, `scope_filter`, `source_event_type`, `source_event_id`, and `reuse_count` |
| `StrategySpec` schema | `services/control-plane/specs/strategy_spec.schema.json` | Canonical `StrategySpec` object fields (`strategy_id`, `spec_version`, `market_scope`, `data_dependencies`, `execution_profile`, `evaluation_plan`, `governance`, `provenance`) |
| `RS-002 StrategySpec Normalization` | `services/research/strategy_spec/README.md` | Research-to-canonical normalization bridge proving that downstream consumers should read the governed `StrategySpec` object rather than a raw research-first note shape |
| `Consultation Surface Contract` CS-05 | `services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md` | A proven session-scoped evidence-link resolution pattern (`GET /api/v1/consultations/{session_id}/evidence`) showing that BFF-owned evidence links can be resolved and status-marked without client-side URL construction |
| L3 storage design intent | `Pantheon_資料表_Schema_設計版.md` | Design-intent tables such as `registry.strategy_specs`, `registry.insight_cards`, and `registry.evidence_bundles`, plus research-note / semantic-index storage hints; these are not canonical BFF truth yet |

These artifacts define object- and storage-level truth. They do **not** define a Knowledge Workbench IA, BFF list/detail routes, degradation semantics, note attachment rules, insight aggregation rules, or strategy-spec version browsing. Those are the gaps this packet family addresses.

---

## Module Inventory

| Module ID | Module name | Screen / surface scope | Lovable readiness | Wave order |
|---|---|---|---|---|
| `KW-01` | Institutional Memory | memory entry list, entry detail, lifecycle state machine, tag/type filters | not ready | Wave 3 — 1st |
| `KW-02` | Research Notes | note list, note detail, attach-to-entity selector, ownership view | not ready | Wave 3 — 2nd |
| `KW-03` | Evidence Refs | evidence reference list, reference detail, linked-decision panel, source-document link | not ready | Wave 3 — 3rd |
| `KW-04` | Insight Cards | browsable card grid, card detail panel, filter rail (tag, entity, recency), linked-source drilldown | not ready | Wave 3 — 4th |
| `KW-05` | Strategy Spec | spec list, versioned spec viewer, lifecycle state, evidence citation panel, diff or compare surface | not ready | Wave 3 — 5th |

---

## KW-01 Institutional Memory

### Surface scope

- **Memory entry list**: paginated list of institutional memory entries with `entry_id`, `knowledge_type`, `content.headline`, `scope`, `written_at`, `write_authority`, and retrieval tags. Filter by `knowledge_type`, `scope`, `scope_filter`, and tag. Querying is backend-side; the UI must not approximate retrieval ranking locally.
- **Entry detail**: full entry view showing `content.headline`, `content.body`, optional `content.structured_payload`, `source_event_type`, `source_event_id`, `contributing_persona_ids`, `scope`, `scope_filter`, `reuse_count`, and `superseded_by` when present.
- **Lifecycle state machine**: `draft → active → archived`. This lifecycle is required by the backlog but is **not** present in the current schema. The UI must not infer lifecycle from `superseded_by`, `written_at`, or missing fields.
- **Filter rail**: filter by `knowledge_type`, tag, scope, and recency. Filter vocab must be backend-provided. Do not hardcode type labels beyond the canonical schema enums.
- **Degradation**: when `meta.surfaces.institutional_memory_list` or `meta.surfaces.institutional_memory_detail` is `degraded` or `unavailable`, show the canonical non-dismissable degradation banner. Do not show "no entries" as authoritative when the read surface is stale.

### Backend gaps

| Route / contract | Status | Notes |
|---|---|---|
| `GET /api/v1/knowledge/memory` | **missing** | list route; must support `knowledge_type`, `scope`, `scope_filter`, `tag`, `query`, `page_token`, `page_size`; response must include `meta.surfaces.institutional_memory_list` |
| `GET /api/v1/knowledge/memory/{entry_id}` | **missing** | detail route; must expose schema-backed fields plus the explicit workbench lifecycle state and any resolved linked-entity references once that contract exists; must include `meta.surfaces.institutional_memory_detail` |
| Institutional memory projection | **missing** | the Memory Plane defines retrieval through `GET /memory/retrieve`, but there is no BFF-owned browse/list/detail projection with pagination and workbench-facing surface metadata |
| Memory entry lifecycle and identity contract | **missing** | the backlog requires lifecycle (`draft | active | archived`) and an identity schema covering entry type, tags, and linked artifacts. The current schema provides `knowledge_type` and tags, but no lifecycle or linked-artifact contract. Those must be promoted to explicit BFF truth rather than invented client-side |

### Packetization prerequisite

The memory entry lifecycle and identity schema must be locked before any other Knowledge Workbench module can reference institutional knowledge. The open question is whether the backlog's "entry type" is the current canonical `knowledge_type` field or a distinct UI-facing alias. That mapping must be defined in the BFF, not inferred in Lovable.

### Lovable readiness gate

`false` — the list route, detail route, institutional-memory projection, and lifecycle or identity contract must all be implemented and field shapes locked before a screen spec can be opened.

---

## KW-02 Research Notes

### Surface scope

- **Note list**: paginated list of research notes showing `note_id`, title or summary excerpt, `owner_ref`, attached entity summary, `created_at`, and `updated_at`. Filter by owner and attachment target.
- **Note detail**: full note view showing note body, attachment target, owner, linked evidence refs, and linked institutional-memory anchors where present.
- **Attach-to-entity selector**: note creation or filtering must use a backend-shaped attachment taxonomy. Supported targets must be explicit (`research_ticket`, `persona`, `strategy_spec`, or `free_standing`) rather than inferred from opaque string ids.
- **Ownership view**: notes grouped or filtered by owner and attachment target. The UI must not infer ownership from path naming, creator initials, or client-side workspace context.
- **Degradation**: when `meta.surfaces.research_note_list` or `meta.surfaces.research_note_detail` is `degraded` or `unavailable`, show the canonical degradation banner. Never collapse a degraded note surface into an empty list or empty note body.

### Backend gaps

| Route / contract | Status | Notes |
|---|---|---|
| `POST /api/v1/knowledge/notes` | **missing** | create route; must define the note body shape, attachment target fields, and initial ownership metadata |
| `GET /api/v1/knowledge/notes` | **missing** | list route; must support `owner_ref`, `attachment_type`, `attachment_ref`, `page_token`, `page_size`; response must include `meta.surfaces.research_note_list` |
| `GET /api/v1/knowledge/notes/{note_id}` | **missing** | detail route; must expose note content, attachment target, owner metadata, linked evidence refs, and `meta.surfaces.research_note_detail` |
| Research note ownership and attachment contract | **missing** | no canonical Pantheon note object exists today. The attachment taxonomy, referential integrity rules, and the meaning of a free-standing note must all be defined before the list and detail shells can be packetized |

### Packetization prerequisite

Note ownership and attachment semantics must be locked before the list and detail shells can be packet-defined. This depends on `KW-01` settling how institutional-memory identities are referenced so that a note can link to a stable knowledge anchor instead of a client-invented label.

### Lovable readiness gate

`false` — the create route, list route, detail route, and ownership or attachment contract must all be implemented and field shapes locked before a screen spec can be opened.

---

## KW-03 Evidence Refs

### Surface scope

- **Evidence reference list**: paginated list showing `ref_id`, source-document identity, link type, linked decision or artifact summary, credibility metadata, and last verification or capture timestamp.
- **Reference detail**: full evidence reference view showing source document identity, excerpt or storage preview metadata, link taxonomy, credibility metadata, linked decision or artifact refs, and any related research-note context.
- **Linked-decision panel**: a BFF-resolved panel showing the downstream object(s) this evidence supports. The UI must not reverse-resolve raw ids into routes or labels.
- **Source-document link**: opens the canonical target surface or file reference resolved by the BFF. Do not construct URLs from raw `storage_ref`, `ref_id`, or guessed path conventions.
- **Degradation**: when `meta.surfaces.evidence_refs_list` or `meta.surfaces.evidence_ref_detail` is `degraded` or `unavailable`, show the canonical degradation banner. Never treat "no evidence" as authoritative when the surface is stale.

### Backend gaps

| Route / contract | Status | Notes |
|---|---|---|
| `GET /api/v1/knowledge/evidence` | **missing** | list route; must support `linked_entity_type`, `linked_entity_ref`, `link_type`, `credibility`, `page_token`, `page_size`; response must include `meta.surfaces.evidence_refs_list` |
| `GET /api/v1/knowledge/evidence/{ref_id}` | **missing** | detail route; must expose source-document identity, link-type taxonomy, linked target refs, credibility metadata, and `meta.surfaces.evidence_ref_detail` |
| Evidence reference read model | **missing** | there is no canonical reusable evidence-ref object or BFF projection for the Knowledge Workbench. CS-05 proves session-scoped evidence links can be resolved, but it does not provide a cross-workbench evidence registry or detail model |
| Evidence link resolution contract | **missing** | each knowledge evidence ref must carry a BFF-resolved target link and availability state. The UI must not construct evidence URLs from raw ids, raw storage refs, or heuristic object names |

### Packetization prerequisite

The evidence reference shape, link-type taxonomy, linked-object contract, and credibility metadata must be agreed before a browse or detail view can be packet-defined. This depends on `KW-01` for anchor identity and `KW-02` for source-document or note context.

### Lovable readiness gate

`false` — the list route, detail route, evidence-reference read model, and evidence-link resolution contract must all be implemented and field shapes locked before a screen spec can be opened.

---

## KW-04 Insight Cards

### Surface scope

- **Card grid**: browsable grid of insight cards showing `insight_id`, summary, confidence, scope, created-by, created-at, and a short evidence count or source summary. The UI renders backend-provided cards; it must not aggregate raw note, evidence, and memory records locally.
- **Card detail panel**: expanded card view showing summary, `source_ref`, scope, confidence, supporting evidence refs, and the backend-provided linked-source drilldown targets.
- **Filter rail**: filter by tag, linked entity, and recency. Filter vocab and result counts must be backend-shaped because L3 design tables do not define a canonical tag or entity taxonomy for insight cards.
- **Linked-source drilldown**: open the canonical upstream entity (memory entry, evidence ref, note, or strategy-spec citation) using BFF-provided links rather than a client-side join.
- **Degradation**: when `meta.surfaces.insight_cards` or `meta.surfaces.insight_card_detail` is `degraded` or `unavailable`, show the canonical degradation banner. Do not silently drop cards or filters when aggregation inputs are stale.

### Backend gaps

| Route / contract | Status | Notes |
|---|---|---|
| Insight aggregation endpoint | **missing** | primary card-grid route; must return backend-shaped card rows, filter metadata, and `meta.surfaces.insight_cards` |
| Insight card detail endpoint | **missing** | per-card detail route; must expose `source_ref`, `scope`, `confidence`, `evidence_refs`, resolved linked sources, and `meta.surfaces.insight_card_detail` |
| Card-surface read model | **missing** | L3 storage design lists `registry.insight_cards` fields such as `source_ref`, `scope`, `summary`, `confidence`, and `evidence_refs_json`, but there is no canonical read model for workbench filters, linked-entity drilldown, or aggregation provenance |
| Filter taxonomy and aggregation contract | **missing** | tag, linked-entity, and recency filters must be defined as backend truth. Card production depends on `KW-01` institutional-memory anchors and `KW-03` evidence refs as stable aggregation inputs |

### Packetization prerequisite

Insight-card identity, display contract, and filter semantics must be locked before Lovable can render a real card surface. The existing L3 table is a storage hint only; it does not define how the BFF groups, ranks, or explains cards.

### Lovable readiness gate

`false` — the aggregation endpoint, detail endpoint, card-surface read model, and filter or aggregation contract must all be implemented and field shapes locked before a screen spec can be opened.

---

## KW-05 Strategy Spec

### Surface scope

- **Spec list**: paginated list of canonical strategy specs showing `strategy_id`, `title`, `spec_version`, provenance source, and a workbench lifecycle state (`draft`, `approved`, `deprecated`). The current canonical schema does not define lifecycle state, so this field must come from a BFF or registry projection.
- **Versioned spec viewer**: full viewer over the canonical `StrategySpec` object, including `hypothesis`, `objective`, `market_scope`, `data_dependencies`, `execution_profile`, `evaluation_plan`, `governance`, and `provenance`.
- **Evidence citation panel**: shows the evidence and provenance chain backing the current spec version. This includes `data_dependencies`, `provenance.source_refs`, and any linked knowledge evidence refs. The UI must not flatten raw ids or storage refs into ad hoc citation strings.
- **Diff or compare surface**: backend-composed field diff between two versions of the same strategy spec. The UI renders the provided diff shape rather than comparing raw JSON locally.
- **Degradation**: when `meta.surfaces.strategy_spec_list` or `meta.surfaces.strategy_spec_detail` is `degraded` or `unavailable`, show the canonical degradation banner. Never treat the latest locally cached spec as authoritative when the version surface is stale.

### Backend gaps

| Route / contract | Status | Notes |
|---|---|---|
| Strategy-spec list route | **missing** | no BFF list surface exists over canonical `StrategySpec` objects; the route must support `lifecycle_state`, `source_kind`, `page_token`, `page_size`, and return `meta.surfaces.strategy_spec_list` |
| Versioned strategy-spec detail route | **missing** | no BFF viewer route exists; it must support version selection, expose the canonical `StrategySpec` payload, include lifecycle state and citation bundle, and return `meta.surfaces.strategy_spec_detail` |
| Strategy-spec versioning and lifecycle contract | **missing** | the backlog requires `draft | approved | deprecated` and version semantics. The current schema has `spec_version`, but no lifecycle, ancestry, or version-selection contract |
| Strategy-spec diff or compare contract | **missing** | the backend must compose field-level diffs and version ancestry; the UI must not compare raw spec JSON locally |

### Packetization prerequisite

Spec lifecycle states and versioning semantics must be established before a viewer or comparison surface can be packet-defined. The canonical `StrategySpec` object already exists, but it is an object schema, not a workbench read model. `KW-05` depends on `KW-01` for lineage anchors and `KW-03` for backing citations.

### Lovable readiness gate

`false` — the list route, detail route, versioning or lifecycle contract, and diff or compare contract must all be implemented and field shapes locked before a screen spec can be opened.

---

## Backend Gap Matrix

Each row is scoped to one or more modules. A module advances to Lovable-ready when all rows assigned to that module (and its upstream prerequisites) are resolved — not when every gap in the family is resolved. See the Promotion Criteria section for the per-module gate definition.

| Route or contract | Module(s) | Gap type | Blocking what |
|---|---|---|---|
| `GET /api/v1/knowledge/memory` | KW-01 | missing read route | institutional-memory list surface |
| `GET /api/v1/knowledge/memory/{entry_id}` | KW-01, KW-02, KW-03, KW-04, KW-05 | missing read route | entry-detail anchor for downstream linked-memory resolution |
| Memory entry lifecycle and identity contract | KW-01, KW-02, KW-03, KW-04, KW-05 | missing lifecycle or object contract | `draft | active | archived`, entry-type mapping, tags, and linked-artifact semantics |
| `POST /api/v1/knowledge/notes` | KW-02 | missing write route | note creation and attachment capture |
| `GET /api/v1/knowledge/notes` | KW-02, KW-03 | missing read route | note list surface and evidence source-context lookup |
| `GET /api/v1/knowledge/notes/{note_id}` | KW-02, KW-03 | missing read route | note detail and source-context resolution for evidence refs |
| Research note ownership and attachment contract | KW-02, KW-03 | missing object contract | owner semantics, attachment taxonomy, and referential integrity |
| `GET /api/v1/knowledge/evidence` | KW-03, KW-04, KW-05 | missing read route | evidence list surface and downstream card or citation browsing |
| `GET /api/v1/knowledge/evidence/{ref_id}` | KW-03, KW-04, KW-05 | missing read route | evidence detail, card drilldown, and strategy-spec citation drilldown |
| Evidence reference read model | KW-03, KW-04, KW-05 | missing object contract | source-document identity, link taxonomy, linked-object refs, and credibility metadata |
| Evidence link resolution contract | KW-03, KW-04, KW-05 | missing BFF-side resolution | canonical evidence links with availability state; no client-side URL construction |
| Insight aggregation endpoint | KW-04 | missing read route | entire Insight Cards module |
| Insight card detail endpoint | KW-04 | missing read route | card detail and linked-source drilldown |
| Card-surface read model | KW-04 | missing object contract | card identity, scope, summary, confidence, and aggregation provenance |
| Filter taxonomy and aggregation contract | KW-04 | missing filter or computation contract | tag, linked-entity, and recency filters; blocks card grid and detail semantics |
| Strategy-spec list route | KW-05 | missing read route | strategy-spec index surface |
| Versioned strategy-spec detail route | KW-05 | missing read route | strategy-spec viewer and citation panel |
| Strategy-spec versioning and lifecycle contract | KW-05 | missing lifecycle contract | version ancestry and `draft | approved | deprecated` state |
| Strategy-spec diff or compare contract | KW-05 | missing comparison contract | compare surface; backend must own the diff |

---

## Internal Ordering and Dependency Chain

| Position | Module | Why this order | Upstream dependency within workbench |
|---|---|---|---|
| Wave 3 — 1st | `KW-01 Institutional Memory` | foundational knowledge store that all later modules rely on for anchor identity, lineage, and reusable institutional truth | none — can start as soon as Wave 3 opens |
| Wave 3 — 2nd | `KW-02 Research Notes` | note attachments need a stable institutional-memory identity schema before the workbench can define what a note points at | Institutional Memory identity contract and `GET /api/v1/knowledge/memory/{entry_id}` |
| Wave 3 — 3rd | `KW-03 Evidence Refs` | evidence refs require both a stable anchor entity and a stable source-context model before link taxonomy and credibility display can be locked | Institutional Memory anchor identity; Research Notes source-context semantics |
| Wave 3 — 4th | `KW-04 Insight Cards` | insight cards are a synthesis layer over memory, notes, and evidence; filter semantics are not honest until those upstream inputs are stable | Institutional Memory and Evidence Refs as aggregation inputs |
| Wave 3 — 5th | `KW-05 Strategy Spec` | the formal spec viewer must cite evidence and trace lineage through the knowledge graph; versioning and diff semantics build on the full knowledge surface | Institutional Memory for lineage; Evidence Refs for backing citations |

---

## Promotion Criteria

A Knowledge Workbench module moves from **not ready** to **ready** (and may be handed to Lovable) when all of the following are true:

1. All BFF routes and contracts listed in that module's Backend Gaps table are implemented and have agreed field shapes.
2. The module's `meta.surfaces.*` staleness signals are defined and wired through to the canonical degradation banner (`PKT-005`).
3. Any lifecycle or authority signals exposed by the module are backend-shaped and documented. The UI must not derive lifecycle state, compare eligibility, or note-attachment validity locally.
4. An example payload JSON exists for the module's primary read surface.
5. The upstream prerequisite modules are already Lovable-ready (per the dependency chain above).

No Knowledge Workbench module should be handed to Lovable before its own criteria and all upstream criteria are met.

---

## Cross-Cutting Rules

### Retrieval facade is not the screen contract

The Memory Plane retrieval facade (`GET /memory/retrieve`) is a session-facing query API. It is not a substitute for a paginated Knowledge Workbench browse surface. The UI must not call it directly or reverse-engineer list or detail screens from ranked retrieval results.

### Existing object schemas are not workbench packets

`InstitutionalMemoryEntry` and `StrategySpec` are canonical objects, but object schemas alone do not define:

- BFF list/detail routes
- lifecycle display semantics
- degradation behavior
- version browsing or compare behavior
- citation or linked-entity drilldown behavior

Those rules must be defined at the BFF or packet layer before Lovable work begins.

### No client-side knowledge-graph synthesis

The UI must never:

- infer institutional-memory lifecycle from `superseded_by`, timestamps, or missing fields
- invent research-note attachment semantics from opaque ids or route shapes
- construct evidence links from raw `ref_id` or `storage_ref` values
- aggregate insight cards by joining memory, notes, and evidence locally
- compute strategy-spec diffs by comparing raw JSON client-side

### Evidence links must be BFF-resolved

CS-05 proves the BFF can return per-link availability and resolved evidence links. Knowledge Workbench evidence surfaces must follow the same rule: the BFF returns resolved targets and availability, while the UI only renders the provided link state.

### Degradation banner inheritance

All five modules must inherit the canonical degradation banner from `PKT-005`. The banner must be non-dismissable. Individual surface staleness states (`meta.surfaces.institutional_memory_list`, `meta.surfaces.research_note_detail`, `meta.surfaces.evidence_refs_list`, `meta.surfaces.insight_cards`, `meta.surfaces.strategy_spec_detail`) must come from the BFF, not local heuristics.

---

## Canonical References

- Backlog source: `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` (Knowledge Workbench section)
- Memory object truth: `services/memory/MEMORY_LAYER_DESIGN_NOTE.md`, `services/memory/institutional_memory_entry.schema.json`
- Strategy-spec object truth: `services/control-plane/specs/strategy_spec.schema.json`, `services/research/strategy_spec/README.md`
- Evidence-link precedent: `services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md` (CS-05)
- L3 design intent only: `Pantheon_資料表_Schema_設計版.md` (`registry.strategy_specs`, `registry.insight_cards`, `registry.evidence_bundles`, research-note storage hints)
- Cross-cutting substrate: `PKT-005` degradation banner and SSE substrate must be inherited anywhere live-update or stale-state handling is introduced
- Handoff directory: `docs/pantheon-handoffs/KW-006-knowledge-workbench/`

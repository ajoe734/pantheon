# KW-006 Knowledge Workbench — Canonical Packet Family

## Header

- Packet family ID: `KW-006`
- Workbench: Knowledge Workbench
- Phase origin: `BP5-WB-006`
- Lovable readiness: **partially opened** — `KW-01` is route-live; `KW-02` Research Notes routes live (handoff bundle published 2026-04-21); `KW-03` Evidence Refs routes live (handoff bundle published 2026-04-21); `KW-04` Insight Cards routes live (handoff bundle published 2026-04-22); `KW-05` Strategy Spec routes live (handoff bundle published 2026-04-22) and ready for frontend activation against backend-owned version identity and compare semantics
- Overview packet status: `PKT-knowledge-workbench` is published as a truthful overview surface; all five Knowledge Workbench modules are now on the live-route side of the boundary
- Recommended wave: Wave 3 — after Operator Console (Waves 1-2) and Persona Workbench (Waves 1-2) packetization are settled
- Owner: Claude
- Reviewer: Codex2

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
| `KW-01 BFF Contract` | `docs/bff/KW-01-institutional-memory.md` | Canonical BFF routes for list and detail browse, paginated read models, and lifecycle/identity resolution |
| `StrategySpec` schema | `services/control-plane/specs/strategy_spec.schema.json` | Canonical `StrategySpec` object fields (`strategy_id`, `spec_version`, `market_scope`, `data_dependencies`, `execution_profile`, `evaluation_plan`, `governance`, `provenance`) |
| `RS-002 StrategySpec Normalization` | `services/research/strategy_spec/README.md` | Research-to-canonical normalization bridge proving that downstream consumers should read the governed `StrategySpec` object rather than a raw research-first note shape |
| `Consultation Surface Contract` CS-05 | `services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md` | A proven session-scoped evidence-link resolution pattern (`GET /api/v1/consultations/{session_id}/evidence`) showing that BFF-owned evidence links can be resolved and status-marked without client-side URL construction |
| L3 storage design intent | `Pantheon_資料表_Schema_設計版.md` | Design-intent tables such as `registry.strategy_specs`, `registry.insight_cards`, and `registry.evidence_bundles`, plus research-note / semantic-index storage hints; these are not canonical BFF truth yet |

These artifacts define object- and storage-level truth. They do **not** define a Knowledge Workbench IA, BFF list/detail routes, degradation semantics, note attachment rules, insight aggregation rules, or strategy-spec version browsing. Those are the gaps this packet family addresses.

---

## Module Inventory

| Module ID | Module name | Screen / surface scope | Lovable readiness | Wave order |
|---|---|---|---|---|
| `KW-01` | Institutional Memory | memory entry list, entry detail, lifecycle state machine, tag/type filters | ready | Wave 3 — 1st |
| `KW-02` | Research Notes | note list, note detail, attach-to-entity selector, ownership view | **route-live — ready for Lovable implementation** | Wave 3 — 2nd |
| `KW-03` | Evidence Refs | evidence reference list, reference detail, linked-decision panel, source-document link | **route-live — ready for Lovable implementation** | Wave 3 — 3rd |
| `KW-04` | Insight Cards | browsable card grid, card detail panel, filter rail (tag, entity, recency), linked-source drilldown | route-live — handoff bundle at `docs/pantheon-handoffs/KW-04-insight-cards/` | Wave 3 — 4th |
| `KW-05` | Strategy Spec | spec list, versioned spec viewer, lifecycle state, evidence citation panel, diff or compare surface | route-live — handoff bundle at `docs/pantheon-handoffs/KW-05-strategy-spec/` | Wave 3 — 5th |

---

## KW-01 Institutional Memory

### Surface scope

- **Memory entry list**: paginated list of institutional memory entries with `entry_id`, `knowledge_type`, `content.headline`, `scope`, `written_at`, `write_authority`, and retrieval tags. Filter by `knowledge_type`, `scope`, `scope_filter`, and tag. Querying is backend-side; the UI must not approximate retrieval ranking locally.
- **Entry detail**: full entry view showing `content.headline`, `content.body`, optional `content.structured_payload`, `source_event_type`, `source_event_id`, `contributing_persona_ids`, `scope`, `scope_filter`, `reuse_count`, and `superseded_by` when present.
- **Lifecycle state machine**: `active → archived → superseded`. The UI displays the lifecycle status provided by the BFF. Superseded entries include a `superseded_by` reference.
- **Filter rail**: filter by `knowledge_type`, tag, scope, and recency. Filter vocab must be backend-provided. Do not hardcode type labels beyond the canonical schema enums.
- **Degradation**: when `meta.surfaces.memory_list` or `meta.surfaces.entry_detail` is `degraded` or `unavailable`, show the canonical non-dismissable degradation banner. Do not show "no entries" as authoritative when the read surface is stale.

### Backend gaps

| Route / contract | Status | Notes |
|---|---|---|
| `GET /api/v1/knowledge/memory` | **implemented** | list route; supports `knowledge_type`, `scope`, `scope_filter`, `tag`, `page`, `page_size`; response includes `meta.surfaces.memory_list` |
| `GET /api/v1/knowledge/memory/{entry_id}` | **implemented** | detail route; exposes schema-backed fields, lifecycle status, and source context; includes `meta.surfaces.entry_detail` |
| Institutional memory projection | **implemented** | BFF-owned browse/list/detail projection defined in `docs/bff/KW-01-institutional-memory.md` |
| Memory entry lifecycle and identity contract | **implemented** | lifecycle and identity resolution locked in `KW-01` BFF contract; entry types map directly to canonical `knowledge_type` enums |

### Packetization prerequisite

The memory entry lifecycle and identity schema are locked. Downstream modules (KW-02 to KW-05) must reference `entry_id` for anchoring and lineage.

### Lovable readiness gate

`true` — the list route, detail route, institutional-memory projection, and lifecycle/identity contracts are published. Lovable may proceed with production UI for the Institutional Memory module.


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
| `POST /api/v1/knowledge/notes` | **live** | create route live and returning the published `note_id`, `created_at`, and `route_href` shape; validates `attachment_type`, `attachment_ref`, `linked_memory_anchors`; owner is server-assigned |
| `GET /api/v1/knowledge/notes` | **live** | list route live and returning `owner_ref`, `attachment`, `tags`, `excerpt`, pagination, and `meta.surfaces.research_note_list` |
| `GET /api/v1/knowledge/notes/{note_id}` | **live** | detail route live and returning note body, `linked_evidence_refs` with resolution state, `linked_memory_anchors`, and per-panel surface state |
| Research note ownership and attachment contract | **ratified** | canonical `owner_ref` shape, attachment taxonomy (`research_ticket`, `persona`, `strategy_spec`, `free_standing`), referential integrity rules, and `free_standing` semantics are locked in `docs/bff/KW-02-research-notes.md` |

### Packetization prerequisite

All three notes routes are live and returning the published field shape. The frontend handoff bundle is published at `docs/pantheon-handoffs/KW-02-research-notes/FRONTEND_CHANGE_SPEC.md`.

### Lovable readiness gate

`route-live` — the create/list/detail routes are live, ownership contract and attachment taxonomy are locked, and the frontend handoff bundle is published. Lovable UI implementation may begin.

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
| `GET /api/v1/knowledge/evidence` | **live** | list route live and returning the published `evref-{UUID}` row shape, backend-owned filters (`linked_entity_type`, `linked_entity_ref`, `link_type`, `credibility_tier`, `verified`), pagination, and `meta.surfaces.evidence_refs_list` |
| `GET /api/v1/knowledge/evidence/{ref_id}` | **live** | detail route live and returning source-document detail, `resolved_link`, `linked_decisions`, `source_note_context`, `source_memory_context`, and per-panel surface state |
| Evidence reference read model | **ratified** | canonical `evref-{UUID}` identity, `link_type` taxonomy, `credibility` metadata, `resolved_link` shape, `source_note_context`, and `source_memory_context` are locked in `docs/bff/KW-03-evidence-refs.md` |
| Evidence link resolution contract | **live** | BFF-owned `resolved_link` object with `availability` state (`available | unavailable | external`) and `route_href` is locked and implemented; `open_in_new_tab` is authoritative for external links |

### Packetization prerequisite

Both evidence routes are live and returning the published field shape. Upstream prerequisite `KW-01` is live, `KW-02` notes routes are live, and example payloads are published in `docs/examples/KW-03-evidence-refs.json`. The frontend handoff bundle is published at `docs/pantheon-handoffs/KW-03-evidence-refs/FRONTEND_CHANGE_SPEC.md`.

### Lovable readiness gate

`route-live` — the list/detail routes are live, evidence-reference read model is confirmed, evidence-link resolution is implemented, and the frontend handoff bundle is published. Lovable UI implementation may begin.

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
| Insight aggregation endpoint | **live** | `GET /api/v1/knowledge/insights` is implemented and returning the published card-grid, filter-metadata, pagination, and `meta.surfaces.insight_cards` shape |
| Insight card detail endpoint | **live** | `GET /api/v1/knowledge/insights/{insight_id}` is implemented and returning scope context, supersession, supporting evidence refs, linked sources, and per-panel surface state |
| Card-surface read model | **ratified** | canonical `ins-{UUID}` identity, card lifecycle (`active | superseded | archived`), confidence scale, aggregation provenance, and linked-source drilldown contract are locked |
| Filter taxonomy and aggregation contract | **implemented** | tag, linked-entity, and recency filters are backend-owned in the live BFF response; the remaining gap is publishing the frontend handoff bundle |

### Packetization prerequisite

Insight-card identity, display contract, and filter semantics are now locked and served by the live BFF routes. The module-local frontend handoff bundle is published, so UI activation may consume the current route-live truth without re-synthesizing cards or filters client-side.

### Published contract bundle

- BFF contract: `docs/bff/KW-04-insight-cards.md`
- Example payload: `docs/examples/KW-04-insight-cards.json`
- Frontend change spec: `docs/pantheon-handoffs/KW-04-insight-cards/FRONTEND_CHANGE_SPEC.md`

### Lovable readiness gate

`route-live` — the aggregation/detail routes are live, the frontend change spec is published, and UI activation may proceed without client-side filter synthesis.

---

## KW-05 Strategy Spec

### Surface scope

- **Spec list**: paginated list of canonical strategy specs showing `strategy_id`, `current_spec_version_id`, `current_spec_version`, title, source metadata, and lifecycle state (`draft`, `candidate`, `approved`, `retired`). Lifecycle state and version identity are BFF-owned truth, not guessed from raw schema fields.
- **Versioned spec viewer**: full viewer over one immutable strategy-spec version using `spec_version_id`, `spec_version`, `parent_spec_version_id`, and the canonical `StrategySpec` body such as `hypothesis`, `objective`, `market_scope`, `execution_profile`, `evaluation_plan`, and `governance`.
- **Evidence citation panel**: shows the backend-composed evidence and provenance chain backing the current spec version. This includes `derived_from_source_refs[]`, evidence refs, memory anchors, and any linked knowledge citations. The UI must not flatten raw ids or storage refs into ad hoc citation strings.
- **Diff or compare surface**: backend-composed compare output between `left_spec_version_id` and `right_spec_version_id`. The UI renders `changed_sections[]` and `breaking_changes[]` rather than comparing raw JSON locally.
- **Degradation**: when `meta.surfaces.strategy_spec_list` or `meta.surfaces.strategy_spec_detail` is `degraded` or `unavailable`, show the canonical degradation banner. Never treat the latest locally cached spec as authoritative when the version surface is stale.

### Backend gaps

| Route / contract | Status | Notes |
|---|---|---|
| Strategy-spec list route | **live** | `docs/bff/KW-05-strategy-spec.md` ratifies the browse route, pagination envelope, and list projection; the current BFF now serves the live route family |
| Versioned strategy-spec detail route | **live** | detail route, version selector semantics, and object projection are ratified and implemented in the current BFF |
| Version history route | **live** | history route, ancestry projection, and version row shape are ratified and implemented in the current BFF |
| Strategy-spec versioning and lifecycle contract | **ratified** | canonical identity is now `strategy_id + spec_version_id`; lifecycle is `draft | candidate | approved | retired`; ancestry and immutability rules are locked |
| Strategy-spec diff or compare contract | **live** | compare semantics are locked around backend-generated `left_spec_version_id`, `right_spec_version_id`, `changed_sections[]`, `breaking_changes[]`, and `evidence_refs[]`; the compare route is implemented in the current BFF |

### Packetization prerequisite

The KW-05 version model, lifecycle, ancestry, and compare semantics are ratified in `docs/bff/KW-05-strategy-spec.md` and are now served by the live browse/detail/history/compare route family. The module-local frontend activation packet is published; remaining work is UI activation against the backend-owned route truth.

### Published contract bundle

- BFF contract: `docs/bff/KW-05-strategy-spec.md`
- Example payload: `docs/examples/KW-05-strategy-spec.json`
- Frontend change spec: `docs/pantheon-handoffs/KW-05-strategy-spec/FRONTEND_CHANGE_SPEC.md`

### Lovable readiness gate

`route-live` — the browse/detail/version-history/compare routes are live, the frontend change spec is published, and Lovable UI activation may proceed against the backend-owned version identity, ancestry, lifecycle, and compare semantics.

---

## Backend Gap Matrix

Each row is scoped to one or more modules. A module advances to Lovable-ready when all rows assigned to that module (and its upstream prerequisites) are resolved — not when every gap in the family is resolved. See the Promotion Criteria section for the per-module gate definition.

| Route or contract | Module(s) | Gap type | Blocking what |
|---|---|---|---|
| `GET /api/v1/knowledge/memory` | KW-01 | **resolved** — `docs/bff/KW-01-institutional-memory.md` | institutional-memory list surface |
| `GET /api/v1/knowledge/memory/{entry_id}` | KW-01, KW-02, KW-03, KW-04, KW-05 | **resolved** — `docs/bff/KW-01-institutional-memory.md` | entry-detail anchor for downstream linked-memory resolution |
| Memory entry lifecycle and identity contract | KW-01, KW-02, KW-03, KW-04, KW-05 | **resolved** — `docs/bff/KW-01-institutional-memory.md` | lifecycle and identity resolution locked; entry types map to canonical `knowledge_type` enums |
| `POST /api/v1/knowledge/notes` | KW-02 | **resolved** — `docs/bff/KW-02-research-notes.md`; handoff bundle at `docs/pantheon-handoffs/KW-02-research-notes/` | note creation and attachment capture |
| `GET /api/v1/knowledge/notes` | KW-02, KW-03 | **resolved** — `docs/bff/KW-02-research-notes.md`; note list with `owner_ref`, `attachment`, `tags`, pagination, and surface state live | note list surface and evidence source-context lookup |
| `GET /api/v1/knowledge/notes/{note_id}` | KW-02, KW-03 | **resolved** — `docs/bff/KW-02-research-notes.md`; note detail with `linked_evidence_refs`, `linked_memory_anchors`, and per-panel surface state live | note detail and source-context resolution for evidence refs |
| Research note ownership and attachment contract | KW-02, KW-03 | ratified | owner semantics, attachment taxonomy, and referential integrity are locked |
| `GET /api/v1/knowledge/evidence` | KW-03, KW-04, KW-05 | **resolved** — `docs/bff/KW-03-evidence-refs.md`; handoff bundle at `docs/pantheon-handoffs/KW-03-evidence-refs/` | evidence list surface and downstream card browsing |
| `GET /api/v1/knowledge/evidence/{ref_id}` | KW-03, KW-04, KW-05 | **resolved** — `docs/bff/KW-03-evidence-refs.md`; evidence detail with `resolved_link`, `linked_decisions`, source contexts, and per-panel surface state live | evidence detail, card drilldown, and citation drilldown |
| Evidence reference read model | KW-03, KW-04, KW-05 | ratified | source-document identity, link taxonomy, linked-object refs, and credibility metadata are locked |
| Evidence link resolution contract | KW-03, KW-04, KW-05 | **resolved** — BFF-owned `resolved_link` with `available | unavailable | external` states is implemented; no client-side URL construction | canonical evidence links with availability state |
| Insight aggregation endpoint | KW-04 | **resolved** — `docs/bff/KW-04-insight-cards.md`; list route and backend-owned filter metadata are live | insight card grid and filter rail |
| Insight card detail endpoint | KW-04 | **resolved** — `docs/bff/KW-04-insight-cards.md`; detail route and linked-source drilldown are live | card detail and linked-source drilldown |
| Card-surface read model | KW-04 | ratified | card identity, scope, summary, confidence, and aggregation provenance are locked |
| Filter taxonomy and aggregation contract | KW-04 | **resolved** — backend-owned tag, linked-entity, and recency filters are live in the list response | trustworthy filter rail for the card grid |
| Strategy-spec list route | KW-05 | **resolved** — `docs/bff/KW-05-strategy-spec.md`; browse route, pagination envelope, and list projection are live in the current BFF | strategy-spec browse surface |
| Versioned strategy-spec detail route | KW-05 | **resolved** — `docs/bff/KW-05-strategy-spec.md`; detail route, version selector semantics, and citation panel are live in the current BFF | strategy-spec viewer and citation panel |
| Version history route | KW-05 | **resolved** — `docs/bff/KW-05-strategy-spec.md`; version-history route and ancestry projection are live in the current BFF | version-history surface |
| Strategy-spec versioning and lifecycle contract | KW-05 | ratified | version identity, ancestry, lifecycle, and immutability semantics are locked |
| Strategy-spec diff or compare contract | KW-05 | **resolved** — compare route is live and backend-generated compare semantics are implemented in the current BFF | compare surface |

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

A Knowledge Workbench module moves from **contract-ready / pending-bff** or **blocked** to **ready** (and may be handed to Lovable) when all of the following are true:

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

# Evidence Operations Gap And Development Plan

Status: planning gap, not implementation-complete
Date: 2026-07-02
Owner lane: Management Console / BFF / Knowledge Evidence

## Executive Summary

The current Evidence Explorer is architecturally important but not operationally
useful enough to justify first-tier Management navigation. It exposes evidence
reference rows, credibility labels, and partial detail views, but it does not
let an operator reliably answer or act on:

- Where is the original source?
- Can I open the linked artifact or downstream object?
- What chain connects source, artifact, readiness, decision, and assertion?
- Is the evidence traceable, stale, incomplete, redacted, or actionable?
- Who owns review or remediation?
- How do I request more evidence, create a disposition task, or assign a
  reviewer?

The target is to upgrade `/management/evidence` from a read-only registry into
an Evidence Operations page. The work should reuse the existing KW-03 evidence
contract, BFF-owned link resolution, command plane, task/reviewer bridge,
readiness evidence panels, artifact detail pages, and lineage APIs. It should
not create a parallel evidence, task, reviewer, or URL-resolution system.

## First-Tier Placement Judgment

Evidence belongs near the top of the management information architecture only
because it is the trust substrate for readiness, artifacts, decisions,
assertions, and incident follow-up. That architectural importance does not by
itself justify first-tier navigation placement.

The current page was effectively promoted on strategic importance, not on
shipped operator capability. As implemented, it is still closer to a read-only
registry than an operations surface: it can show that evidence records exist,
but it cannot yet let an operator trace, assign, remediate, or close evidence
work. That is an IA mismatch.

Production rule:

- keep it in first-tier navigation only after Evidence Operations can answer
  "where is the proof, who/what relies on it, what is wrong, who owns it, and
  what action can I take now?";
- otherwise move it to Advanced Registry or System Diagnostics until those
  operation capabilities ship.

## Current Live Symptom

The hosted dev BFF currently returns two management evidence rows:

- `evref-rart-20260615-002`
- `evref-rart-20260615-001`

Both are `credibility.tier = producer_record` and `verified = true`, but both
also have:

- `source_type = null`
- `source_ref = null`
- `link_type = null`
- `resolved_link.availability = unavailable`
- `linked_decisions = []`
- no source note or memory context

That means the surface can truthfully say "the evidence read surface is ok" but
the row is still not operationally traceable. The page must distinguish surface
health from row-level actionability.

## Current Performance Symptom

Hosted dev timing probes on 2026-07-02 show that the user-visible slowness is
not explained by payload size or row count:

- `/bff/management/evidence?page_size=5`
  - payload: about 6 KB
  - time to first byte: about 3.28 seconds
  - total: about 3.28 seconds
- `/api/v1/knowledge/evidence?page_size=5`
  - payload: about 1.3 KB
  - time to first byte: about 0.11 seconds
- `/api/v1/knowledge/evidence/evref-rart-20260615-002`
  - payload: about 1 KB
  - time to first byte: about 0.09 seconds

The management endpoint and the knowledge endpoint share the same underlying
evidence read-store path, so the observed gap points at the management
aggregate request path, proxy/middleware path, or aggregate instrumentation gap
rather than the evidence dataset itself. Do not tune the UI table before
instrumenting `/bff/management/evidence` end-to-end.

Production target:

- list aggregate p95 under 500 ms for 100 rows on warm dev infrastructure;
- detail aggregate p95 under 300 ms when related surfaces are warm;
- payload size and row count included in logs;
- per-stage timings for auth, read-store load, filtering, redaction, public
  item mapping, summary/facets, and response serialization;
- explicit degraded meta when a related surface is omitted because of latency
  budget.

## Existing Architecture To Reuse

### KW-03 Evidence Refs Contract

Authoritative docs:

- `docs/bff/KW-03-evidence-refs.md`
- `docs/examples/KW-03-evidence-refs.json`

Already defined:

- Stable `ref_id`.
- Backend-owned `link_type` taxonomy.
- Backend-owned `credibility`.
- Backend-owned `resolved_link`.
- List route: `GET /api/v1/knowledge/evidence`.
- Detail route: `GET /api/v1/knowledge/evidence/{ref_id}`.
- Detail panels for `linked_decisions`, `source_note_context`, and
  `source_memory_context`.
- Explicit rule that the frontend must not construct URLs from raw `ref_id`,
  `source_ref`, object names, or storage refs.

Reuse decision:

- Keep KW-03 as the canonical evidence reference contract.
- Extend it with operation-specific fields through management BFF composition,
  not by making the frontend infer missing state.

### Management Evidence BFF

Authoritative code:

- `services/control-plane/bff/main.py`
  - `_build_management_evidence_payload`
  - `GET /bff/management/evidence`
  - `GET /api/v1/knowledge/evidence`
  - `GET /api/v1/knowledge/evidence/{ref_id}`
- `services/control-plane/bff/read_store.py`
  - `evidence_refs` dataset config
  - `list_evidence_refs`
  - `get_evidence_ref_detail`
  - `_kw03_entity_route_href`
  - `_kw03_normalize_resolved_link`

Already defined:

- `PANTHEON_BFF_EVIDENCE_REF_STORE` as the evidence ref read surface.
- List filters for linked entity type/ref, link type, credibility tier, and
  verified state.
- Capability redaction via `redact_evidence_refs`.
- BFF-composed management list with summary/facets/meta.
- BFF detail path via the knowledge route.

Reuse decision:

- Keep `/bff/management/evidence` as the management aggregate entrypoint.
- Add an operation detail aggregate instead of asking the frontend to join
  knowledge evidence, artifact, readiness, lineage, command, and task state.

### Source Ingestion And Knowledge Evidence Plane

Authoritative code:

- `services/source_ingestion/main.py`
  - `_persist_source_evidence_refs`
  - `_evidence_item_for_record`
- `services/knowledge/evidence/models.py`
- `services/knowledge/evidence/bundle_builder.py`
- `services/knowledge/evidence/repository.py`
- `docs/contracts/evidence_bundle.schema.json`

Already defined:

- `SourceRecord`, `EvidenceItem`, `EvidenceBundle`, and `KnowledgeObject`.
- Durable JSONL repository for governed source evidence records.
- Bundle invariants: no rejected source, known source ids, known evidence item
  ids, non-empty citations, confidence range.
- Source ingestion persists evidence bundles and knowledge objects.

Important boundary:

- Source ingestion evidence is not automatically a Management Evidence row.
  A row appears in `/management/evidence` only after a projector emits an
  `evidence_refs` record into the BFF evidence ref read surface.

Reuse decision:

- Do not duplicate `EvidenceItem` or `EvidenceBundle`.
- Add or fix projectors that turn governed evidence into `evidence_refs` with
  complete source, link, credibility, and related-object metadata.

### Readiness Evidence

Authoritative code:

- `services/control-plane/bff/main.py`
  - `_readiness_evidence_ref`
  - readiness aggregate builders
- Frontend:
  - `src/management/components/readiness/EvidencePacketList.tsx`
  - readiness pages under `src/management/pages/oversight/`

Already defined:

- Readiness pages surface proof packets and missing/stale/verified evidence.
- UI has an EvidencePacketList and "request more evidence" strings.

Reuse decision:

- Evidence Operations should show readiness relationships through BFF-resolved
  related readiness refs.
- Do not copy readiness packet rendering logic into a separate evidence-only
  widget if the existing component can be adapted.

### Artifact Detail And Lineage

Authoritative code/docs:

- Frontend `src/management/pages/ArtifactDetail.tsx`
- `docs/bff/PKT-003-lineage-view.md`
- `GET /api/v1/lineage`
- `GET /api/v1/lineage/edges/{edge_id}`
- `GET /api/v1/lineage/graph`
- `GET /api/v1/lineage/inspiration/{artifact_id}`

Already defined:

- Management artifact detail exists at `/management/artifacts/:id`.
- Artifact detail already has overview, lineage, rollback, consumers, metadata,
  and audit tabs.
- Lineage APIs are BFF-owned and explicitly disallow client-side graph
  reconstruction.

Reuse decision:

- Evidence Operations should link to `/management/artifacts/:id` for the
  linked artifact.
- For chain view, the BFF should compose or reference lineage APIs. The
  frontend should not synthesize lineage from raw ids.

### Command Plane And Human Gate Evidence Requests

Authoritative code:

- `services/control-plane/bff/main.py`
  - `POST /api/v1/operator/commands`
  - `POST /bff/v1/commands`
  - `_validate_human_gate_decision`
- `services/control-plane/bff/models.py`
  - `CommandType.HUMAN_GATE_REQUEST_MORE_EVIDENCE`
- `services/control-plane/bff/command_executor.py`
  - command dispatch table
- Frontend:
  - `src/lib/bff/runAction.ts`
  - `src/lib/bff/commandClient.ts`

Already defined:

- Final command path `/bff/v1/commands`.
- Foundation command path `/api/v1/operator/commands`.
- Idempotency, command store, audit context, concurrent safety.
- HumanGate request-more-evidence command with role checks.
- Frontend live write gate and command client.

Reuse decision:

- Evidence actions must go through the command plane, not ad hoc patch routes.
- Where an evidence row is attached to a HumanGate item, use
  `HumanGateRequestMoreEvidence`.
- For evidence rows without a HumanGate target, add an evidence-specific command
  by extending the existing command system rather than building a new write
  path.

### Task And Reviewer Materialization

Authoritative code:

- `services/control-plane/bff/assistant/dev_docs_generator.py`
- `services/control-plane/bff/assistant/dev_bridge_dispatcher.py`
- `services/control-plane/bff/assistant/dev_bridge_models.py`
- `scripts/ai_status.py`
- `ai-task-archive/tasks/*.json`

Already defined:

- Execution tasks have owner, reviewer, depends_on, artifacts, and acceptance.
- The dev bridge can materialize task packets through `scripts/ai_status.py
  assign`.
- The dispatcher is explicit that raw web handlers must not shell out directly.

Reuse decision:

- Evidence disposition tasks should reuse the existing task packet / reviewer
  model.
- Evidence command admission should create a command/audit event. A trusted
  worker or supervisor path should materialize the task, not the HTTP handler.

## What Should Enter Evidence Operations

Evidence Operations should show canonical `evidence_refs`, not every low-level
`EvidenceItem`.

Rows should enter the page when one of these upstream systems emits an
`evidence_refs` read-model record:

1. Source ingestion / knowledge evidence projectors
   - A governed `SourceRecord` and `EvidenceItem` are persisted.
   - A projector links it to a note, memory entry, insight, strategy spec,
     experiment, artifact, decision, readiness gate, or assertion.
   - The row includes source document metadata and BFF-resolvable links.

2. Research orchestrator / artifact producer records
   - A candidate artifact or model is produced.
   - The projector emits a provenance evidence ref linked to the artifact.
   - The row must include artifact route resolution and provenance link type.

3. Readiness proof packets
   - A readiness page has proof packets, readbacks, audits, or missing evidence.
   - The projector emits evidence refs tied to a readiness gate or blocker.

4. Governance / decision / assertion surfaces
   - A decision, approval, assertion, mutation review, or post-incident review
     cites evidence.
   - The projector emits evidence refs or links existing refs through
     `linked_decisions` / related entities.

5. Manual operator remediation
   - An operator requests more evidence or creates a disposition task.
   - This should create operation state and task/audit refs. It should not
     invent a fake source document until a real evidence ref is produced.

## Target Product Behavior

### List View

The first screen should answer "what evidence needs attention now?"

Required list features:

- Actionability counts:
  - traceable
  - incomplete
  - unresolved source
  - stale
  - needs reviewer
  - needs evidence
  - redacted
- Filters:
  - actionability
  - linked entity type/ref
  - source type
  - link type
  - credibility
  - reviewer
  - operation status
- Row actions:
  - open source
  - open linked artifact/object
  - inspect chain
  - mark stale
  - request more evidence
  - create disposition task
  - assign reviewer
- Row status should not show a row as fully healthy just because the surface is
  `ok` or credibility is verified.

### Detail View

Every evidence detail should answer:

- What is this evidence?
- Where did it come from?
- Who or what captured it?
- How trustworthy is it?
- Can the original source be opened or previewed?
- Which artifact, readiness gate, decision, assertion, note, memory entry,
  insight, or strategy spec does it support or challenge?
- What chain connects source -> evidence -> artifact/readiness/decision/assertion?
- What operation state exists: stale, needs evidence, assigned reviewer,
  disposition task, command history, audit events?

Required panels:

- Operations header:
  - actionability state
  - owner/reviewer
  - status
  - due date/SLA if any
  - primary action
- Source panel:
  - `resolved_link`
  - source type
  - captured at/by
  - preview availability
  - source completeness warnings
- Linked object panel:
  - management href for artifact/readiness/decision/assertion when available
  - no client-side route construction
- Chain panel:
  - BFF-composed nodes and edges
  - clear empty state when no chain is recorded
- Relationships panel:
  - decisions
  - readiness gates
  - assertions
  - artifacts
  - notes/memory/insights/specs
- Disposition panel:
  - existing task ids
  - create task CTA
  - reviewer assignment
  - command/audit history

## BFF Contract Additions

Add a management-specific aggregate. Keep KW-03 stable and additive.

### List Item Additions

`GET /bff/management/evidence`

Add fields:

```json
{
  "actionability": {
    "state": "traceable | incomplete | unresolved_source | stale | needs_evidence | under_review | redacted",
    "severity": "ok | info | warning | critical",
    "reasons": ["missing_source_type", "missing_link_type", "resolved_link_unavailable"],
    "can_trace": true,
    "can_open_source": false,
    "can_open_linked_object": true
  },
  "operation": {
    "status": "none | open | stale | needs_evidence | under_review | resolved",
    "owner": null,
    "reviewer": null,
    "task_refs": [],
    "last_action_at": null
  },
  "linked_object_link": {
    "availability": "available | unavailable",
    "route_href": "/management/artifacts/rart-20260615-002",
    "display_label": "Open artifact"
  },
  "allowedActions": {
    "canOpenSource": false,
    "canOpenLinkedObject": true,
    "canInspectChain": true,
    "canMarkStale": true,
    "canRequestEvidence": true,
    "canCreateDispositionTask": true,
    "canAssignReviewer": true
  }
}
```

Rules:

- `actionability` is derived by the BFF from KW-03 fields plus operation state.
- `allowedActions` is BFF-owned and role-aware.
- The frontend must not infer actionability from `credibility.tier` alone.
- Missing `source_type`, `source_ref`, `link_type`, or unavailable
  `resolved_link` must produce a row-level warning even when the aggregate
  surface is `ok`.

### Detail Additions

Add or create:

- `GET /bff/management/evidence/{ref_id}`

Response should include KW-03 detail plus:

```json
{
  "operation": {},
  "actionability": {},
  "linked_object_link": {},
  "relationships": {
    "artifacts": [],
    "readiness": [],
    "decisions": [],
    "assertions": [],
    "notes": [],
    "memory": [],
    "insights": [],
    "strategy_specs": []
  },
  "chain": {
    "nodes": [],
    "edges": [],
    "empty_reason": null
  },
  "tasks": [],
  "audit_events": [],
  "allowedActions": {}
}
```

Rules:

- `relationships` are BFF-resolved. The frontend must not reverse-resolve ids.
- `chain` is BFF-composed or references lineage APIs. The frontend must not
  construct graph edges from raw ids.
- `assertions` can initially be a normalized projection of known safety,
  readiness, and validation assertions. If a canonical assertion read model
  does not exist, expose `meta.surfaces.assertions = unavailable` and show a
  clear gap rather than fake data.

## Operation State Model

Add an `evidence_operations` read/write projection.

Minimal event shape:

```json
{
  "event_id": "evop-...",
  "ref_id": "evref-...",
  "action": "mark_stale | request_more_evidence | create_disposition_task | assign_reviewer | resolve",
  "actor_id": "pantheon-dev-browser",
  "created_at": "2026-07-02T00:00:00Z",
  "reason": "Source link unavailable",
  "status_after": "stale",
  "owner": "Codex",
  "reviewer": "Claude",
  "task_refs": ["EVID-OPS-..."],
  "command_id": "cmd-...",
  "audit_ref": "audit-..."
}
```

Projection shape:

```json
{
  "ref_id": "evref-...",
  "status": "open | stale | needs_evidence | under_review | resolved",
  "owner": "Codex",
  "reviewer": "Claude",
  "task_refs": [],
  "last_action_at": "2026-07-02T00:00:00Z",
  "last_reason": "Source link unavailable",
  "command_refs": [],
  "audit_refs": []
}
```

Storage options:

- Short term: append JSONL beside BFF command/read-state data, exposed through
  `ReadSurfaceStore`.
- Production target: service-owned projection with the same surface status
  discipline as other management read models.

Do not mutate the source evidence ref to mark stale. Staleness is an operation
overlay unless the canonical source projector later emits a corrected evidence
record.

## Action Plan Without Rebuilding Existing Systems

### `mark_stale`

Reuse:

- `/bff/v1/commands`
- command store
- audit context
- evidence operation projection

Add:

- `CommandType.EVIDENCE_REF_ACTION` or equivalent extension.
- `ObjectType.EVIDENCE_REF`.
- action id `mark_stale`.
- operation event writer.

Do not:

- Directly patch the `evidence_refs` read model from the frontend.

### `request_more_evidence`

Reuse:

- `HumanGateRequestMoreEvidence` when the evidence is attached to a
  `HumanGateItem`.
- Generic command admission otherwise.
- operation event writer.

Add:

- BFF resolver that maps evidence -> human gate/readiness blocker when present.
- Fallback evidence action `request_more_evidence` for non-HumanGate evidence.

### `create_disposition_task`

Reuse:

- dev docs / task packet model
- owner/reviewer fields
- `scripts/ai_status.py assign` through the trusted dev bridge/supervisor path

Add:

- command action `create_disposition_task`.
- task payload template:
  - title
  - ref_id
  - linked object
  - source gaps
  - expected acceptance
  - owner
  - reviewer
  - artifact/doc refs
- trusted background worker that materializes the task.

Do not:

- Shell out from the HTTP route.
- Write task files directly from the browser-facing request handler.

### `assign_reviewer`

Reuse:

- existing task/reviewer schema.
- operation projection.

Add:

- command action `assign_reviewer`.
- role validation.
- if no disposition task exists, prompt/create one or store reviewer on the
  evidence operation record.

### `open_source`

Reuse:

- KW-03 `resolved_link`.
- source note/memory contexts.
- BFF preview token rules.

Add:

- source completeness warnings.
- operation action disabled reasons when `resolved_link` is unavailable.
- source projectors for current `producer_record` refs so they do not remain
  source-less.

### `open_artifact`

Reuse:

- `/management/artifacts/:id`.
- research artifact BFF routes.

Add:

- management-specific `linked_object_link` resolver.
- for `entity_type = artifact`, resolve to `/management/artifacts/{entity_ref}`
  when the artifact exists.

### `inspect_chain`

Reuse:

- KW-03 linked decisions.
- lineage APIs.
- artifact detail lineage/audit concepts.

Add:

- BFF-composed `chain.nodes[]` and `chain.edges[]`.
- empty/degraded state when lineage/assertion/readiness relationships are not
  available.

## Implementation Phases

### Phase 0 - Contract And Fixture Repair

Goal: stop misleading operators.

Tasks:

- Add actionability derivation helper in BFF.
- Mark rows with missing source/link fields as `incomplete`.
- Add endpoint timing instrumentation for `/bff/management/evidence`.
- Add a latency budget before expanding detail joins or related-surface fanout.
- Extend fixtures/tests for current `evref-rart-*` shape.
- Decide whether `producer_record` is a valid credibility tier or should map to
  KW-03 `primary/secondary/tertiary/unverified` with a separate method field.
- Add a row-level warning when `verified = true` but `resolved_link` is
  unavailable.

Acceptance:

- Current live-like `evref-rart-*` records render as incomplete/unresolved, not
  operationally healthy.
- BFF tests prove surface `ok` does not imply row actionability `traceable`.
- Warm list aggregate does not spend multiple seconds before first byte when
  the underlying knowledge evidence list returns in about 100 ms.

Implementation progress on 2026-07-02:

- `/bff/management/evidence` list items now include BFF-derived
  `actionability`, `allowedActions`, `disabledActionReasons`, `operation`, and
  `linkedObjectLink`.
- Verified-but-untraceable producer-style rows are marked
  `unresolved_source` with specific reasons instead of appearing healthy.
- Artifact-linked rows receive a BFF-owned management artifact href through
  `linkedObjectLink`.
- The list aggregate now returns `meta.performance.timings_ms` with per-stage
  timings, row count, filtered total, and page size.
- Mutation actions remain disabled until the command/task/reviewer phases are
  implemented.

### Phase 1 - Read Model Enrichment

Goal: make rows traceable before adding mutations.

Tasks:

- Add `linked_object_link`.
- Resolve artifact management routes for `entity_type = artifact`.
- Populate source metadata for research orchestrator `producer_record` refs.
- Add `relationships` and `chain` to detail.
- Add `meta.surfaces.assertions`, `meta.surfaces.readiness_relationships`,
  `meta.surfaces.chain`, and `meta.surfaces.operation_state`.

Acceptance:

- Every row can either open source or explains why source is unavailable.
- Artifact-linked rows can open `/management/artifacts/:id` when the artifact
  exists.
- Detail view shows chain empty state instead of pretending there is no chain
  concern.

Implementation progress on 2026-07-02:

- `/bff/management/evidence/{ref_id}` now composes a management detail
  aggregate from the existing KW-03 evidence detail read model.
- Detail responses include source, linked object link, actionability,
  relationships, chain, operation placeholder, task/audit placeholders,
  allowed actions, and disabled action reasons.
- Relationship buckets are BFF-shaped for artifacts, readiness, decisions,
  assertions, notes, memory, insights, strategy specs, and experiments.
- Chain nodes/edges are BFF-composed from source -> evidence -> related
  downstream entities, with degraded reasons when source traceability is weak.
- Assertion/readiness/operation/task/audit surfaces are explicit in
  `meta.surfaces`; missing canonical projections are shown as unavailable or
  degraded rather than fabricated.

### Phase 2 - Operation State And Commands

Goal: make evidence actionable.

Tasks:

- Add `evidence_operations` event log/projection.
- Add evidence action command type/target through existing command admission.
- Implement actions:
  - mark stale
  - request more evidence
  - create disposition task
  - assign reviewer
  - resolve
- Wire command audit refs into the detail view.
- Ensure idempotency and concurrent target safety.

Acceptance:

- Repeated commands with the same idempotency key replay safely.
- Concurrent actions on the same evidence ref are rejected or serialized.
- RBAC rejects unauthorized mutations.
- Commands produce audit and operation projection records.

Implementation progress on 2026-07-02:

- Added `EvidenceRefAction` command admission through `/bff/v1/commands` with
  `target.type = EvidenceRef`.
- Supported operation actions now include `mark_stale`,
  `request_more_evidence`, `create_disposition_task`, `assign_reviewer`, and
  `resolve`.
- Added an evidence operation projection store backed by
  `PANTHEON_BFF_EVIDENCE_OPERATION_STORE` for local/dev read-model durability.
- Evidence list and detail now overlay operation status, command refs, task
  refs, and audit events from the operation projection.
- The command worker records operation events and marks the command executed;
  idempotency replay and target mismatch behavior are covered by tests.
- Task refs can be attached to evidence operations, but trusted task packet
  materialization remains Phase 3.

### Phase 3 - Task/Reviewer Integration

Goal: make disposition follow-up visible and owned.

Tasks:

- Add a trusted worker path from evidence operation command to task packet.
- Materialize disposition tasks with owner/reviewer/dependencies/artifacts.
- Attach task ids back to `evidence_operations`.
- Surface task state in Evidence detail.

Acceptance:

- Creating a disposition task yields a visible task id.
- Assigning reviewer updates operation state and task reviewer when a task
  exists.
- No browser-facing request shells out directly.

### Phase 4 - Frontend Upgrade

Goal: turn the page into Evidence Operations.

Tasks:

- Rename first-tier label to Evidence Operations if retained in Oversight.
- Add actionability metrics and filters.
- Add row CTAs using BFF `allowedActions`.
- Add detail panels for operations, source, linked object, chain,
  relationships, tasks, and audit.
- Use existing `runAction` / command client for actions.
- Use existing artifact/detail/readiness components where possible.

Acceptance:

- Operator can open source when available.
- Operator can open linked artifact when available.
- Operator can see why a row is not traceable.
- Operator can mark stale, request evidence, create task, and assign reviewer
  with command receipts.
- UI never constructs source or linked-object URLs from raw ids.

### Phase 5 - IA Decision Gate

Goal: justify first-tier placement.

Evidence Operations remains in the Oversight group only if:

- actionability counts exist,
- row actions exist,
- detail actions exist,
- task/reviewer ownership exists,
- current incomplete rows are not presented as healthy.

If those are not shipped, move the page to Advanced Registry or System
Diagnostics until Phase 4 is complete.

## Proposed Execution Tasks

### EVID-OPS-001 - BFF Actionability And Contract Additions

Scope:

- Add actionability and allowedActions to `/bff/management/evidence`.
- Add contract tests for incomplete/unresolved rows.

Key files:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_kw03_evidence_refs_contract.py`
- `docs/bff/KW-03-evidence-refs.md`

### EVID-OPS-002 - Evidence Detail Aggregate

Scope:

- Add `/bff/management/evidence/{ref_id}`.
- Compose operation, relationships, chain, tasks, and audit.

Key files:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- lineage/readiness read helpers

### EVID-OPS-003 - Artifact And Source Link Resolution

Scope:

- Resolve artifact management href.
- Fix `producer_record` refs so source/link metadata is present where possible.
- Add explicit incomplete reasons when not possible.

Key files:

- `services/control-plane/bff/read_store.py`
- research artifact projectors
- evidence ref fixtures

### EVID-OPS-004 - Evidence Operation State Store

Scope:

- Add operation event/projection model.
- Expose operation state through BFF.

Key files:

- `services/control-plane/bff/read_store.py`
- new evidence operation store module or BFF-owned JSONL store
- tests for replay/projection

### EVID-OPS-005 - Evidence Action Commands

Scope:

- Extend command plane with evidence ref target/action.
- Implement mark stale, request evidence, assign reviewer, create task, resolve.

Key files:

- `services/control-plane/bff/models.py`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/command_executor.py`
- command tests

### EVID-OPS-006 - Disposition Task Materialization

Scope:

- Reuse dev bridge/task packet model through trusted worker path.
- Attach task refs back to evidence operation state.

Key files:

- `services/control-plane/bff/assistant/dev_docs_generator.py`
- `services/control-plane/bff/assistant/dev_bridge_dispatcher.py`
- `scripts/ai_status.py`
- operation worker/supervisor integration

### EVID-OPS-007 - Frontend Evidence Operations List

Scope:

- Add metrics, filters, row CTAs, actionability warnings.
- Keep BFF-owned links and actions.

Key files in frontend repo:

- `src/management/pages/oversight/_core.tsx`
- `src/lib/bff-v1/management.ts`
- `src/lib/bff/commandClient.ts`
- `src/i18n/locales/*`

### EVID-OPS-008 - Frontend Evidence Detail Workbench

Scope:

- Add operation header, source, linked object, chain, relationships, tasks,
  audit, and command receipts.

Key files in frontend repo:

- `src/management/pages/oversight/_core.tsx`
- shared artifact/readiness/task/audit components
- tests

### EVID-OPS-009 - Live Probe And IA Gate

Scope:

- Add hosted probe for `/management/evidence`.
- Verify that current `evref-rart-*` rows show incomplete/unresolved state.
- Decide first-tier vs Advanced Registry placement based on shipped capability.

Key files:

- probe scripts under `scripts/` or management load probes
- frontend route tests
- BFF smoke tests

### EVID-OPS-010 - Management Evidence Latency Budget

Scope:

- Instrument `/bff/management/evidence` with per-stage timings.
- Compare management aggregate timing with `/api/v1/knowledge/evidence`.
- Add a regression probe for small payload, warm read-store latency.
- Prevent future chain/relationship joins from turning the list endpoint into a
  blocking fanout path.

Key files:

- `services/control-plane/bff/main.py`
- BFF observability/logging helpers
- hosted probe scripts

## Validation Plan

Backend:

- `python -m pytest services/control-plane/bff/test_kw03_evidence_refs_contract.py -q`
- command admission tests for evidence actions
- read-store projection tests for actionability and operation state
- source/projector tests for current artifact producer refs

Frontend:

- Evidence list renders actionability and row actions.
- Evidence detail renders operations, relationships, and chain panels.
- Disabled actions include reason text.
- Source and linked-object URLs come from BFF fields only.
- Command submissions include idempotency headers and show receipts.

Live:

- Probe `/bff/management/evidence?page_size=5`.
- Probe `/api/v1/knowledge/evidence/{ref_id}` for current refs.
- Compare `/bff/management/evidence?page_size=5` with
  `/api/v1/knowledge/evidence?page_size=5` and flag multi-second aggregate
  overhead.
- Probe `/management/evidence` screenshot:
  - no blank state,
  - incomplete/unresolved badges visible,
  - source disabled reason visible,
  - artifact/action CTAs visible when BFF allows them.

## Non-Goals

- Do not create a second evidence repository.
- Do not make the frontend infer source URLs or lineage routes.
- Do not mutate source evidence refs to store operation status.
- Do not shell out from browser-facing HTTP handlers.
- Do not call an incomplete row "healthy" because its source dataset is `ok`.
- Do not hide missing assertions/readiness relationships; show explicit
  unavailable/degraded surfaces.

## Production-Level Definition Of Done

The upgrade is complete only when:

- every visible row has a BFF-derived actionability state;
- every row can either open source or displays a specific BFF-derived reason it
  cannot;
- artifact-linked rows can open the management artifact surface when the
  artifact exists;
- detail shows source, linked object, chain, relationships, operation state,
  tasks, and audit;
- mark stale, request evidence, create disposition task, and assign reviewer
  flow through the command/task/reviewer systems with idempotency and audit;
- current `evref-rart-*` rows are no longer misleadingly presented as fully
  actionable;
- list and detail aggregates meet documented latency budgets or expose degraded
  surfaces instead of blocking the first render;
- first-tier navigation placement is backed by shipped operation capability, or
  the page is moved out of first-tier Oversight.

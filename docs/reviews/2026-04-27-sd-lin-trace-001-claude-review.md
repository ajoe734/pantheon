# SD-LIN-TRACE-001 Claude Review

Task: `SD-LIN-TRACE-001` - Build source-to-runtime-to-telemetry lineage trace query
Owner: Codex
Reviewer: Claude (auto-reassigned from Claude2 after quota termination)
Status decision: APPROVE

## Scope Reviewed

The first operator-facing derived `source_runtime_telemetry_trace` query that
joins source / strategy / experiment / artifact / approval / deployment plan /
runtime binding / broker-order lifecycle / telemetry / incident / postmortem /
evolution refs by `trace_id`. Per the materialization packet
(`docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md`), this
task owns the trace shape only; reconciliation lifecycle proof remains
`SD-RECON-001`, governed source-evidence validation remains
`SD-SRC-EVIDENCE-001`, and live / canary execution proof remains gated behind
`EP5-002-RUNTIME-LIVE-PROOF-001`.

## Acceptance Verification

| Acceptance target | Evidence | Result |
|---|---|---|
| Operator can query one source-to-runtime-to-telemetry trace by trace_id | `LineageReadService.query("source_runtime_telemetry_trace", trace_id=...)` at `services/telemetry/lineage_read/service.py:2380-2383`; routed through `ProjectionBuilder.source_runtime_telemetry_trace` to `_build_source_runtime_telemetry_trace` at `service.py:872-1209` | PASS |
| Trace joins source / strategy / experiment / artifact / approval / deployment plan / runtime binding / broker-order lifecycle / telemetry / incident / postmortem / evolution refs | `_build_source_runtime_telemetry_trace` walks telemetry events by `trace_id`, then accumulates binding/plan/pool/persona/artifact/strategy ids and resolves source/research, deployment, runtime, broker order (`_related_broker_order_nodes`), incident (`_related_incident_nodes`), postmortem (`_related_postmortem_nodes`), evolution (`_related_evolution_nodes`); covered by `test_query_source_runtime_telemetry_trace` | PASS |
| Read model remains derived-only and not a parallel truth source | Response sets `derived_only: True`; payload writes nothing back to owner stores; helpers only read from `LineageGraph` | PASS |
| Missing IDs / edges are explicit and replayable | `_known_or_missing` + `_add_missing_edge` populate `missing_edges[]` and `conflict_markers[]` with `missing_lineage_edge`; missing trace target returns `node_not_found`; covered by `test_source_runtime_trace_surfaces_missing_edges` and `test_query_source_runtime_trace_missing_param` | PASS |
| HTTP route exposes the operator-facing query | `GET /api/telemetry/lineage/traces/<trace_id>/source-runtime-telemetry` at `services/telemetry/main.py:533-539` reuses `_lineage_query_response` for stable error mapping | PASS |
| Missing trace target maps to stable HTTP error semantics | `_lineage_query_response` maps `node_not_found` → `LINEAGE_TARGET_NOT_FOUND` 404; covered by `test_missing_source_runtime_trace_returns_404` | PASS |
| Contract note names the query family and boundary | `services/registry/lineage/read_model_contract.md:212` adds `source_runtime_telemetry_trace` to the synchronous summary bucket and restates the missing-edge / conflict-marker invariant | PASS |
| Targeted service and route tests pass | `pytest services/telemetry/lineage_read/test_service.py services/telemetry/test_main_routes.py -q` → `38 passed in 0.65s` (rerun during review) | PASS |

## Boundary And Scope

The implementation stays inside the boundary the materialization packet asked
for:

- the trace is a derived-only projection assembled from owner-written refs;
  no telemetry / runtime / governance / source ownership is moved into the
  read model
- missing referenced nodes are surfaced through `missing_edges[]` and
  `conflict_markers[]`, not silently inferred or backfilled
- broker, incident, postmortem, and evolution refs are collected by linking
  through known telemetry / binding / plan / artifact / strategy ids — the
  trace does not claim to validate those owner records
- the HTTP route reuses the existing `_lineage_query_response` helper, so
  error mapping (`LINEAGE_UNAVAILABLE` / `INVALID_LINEAGE_QUERY` /
  `LINEAGE_TARGET_NOT_FOUND`) stays consistent with the other lineage routes
- no live / canary execution proof, no research activation promotion, and no
  reconciliation lifecycle claim is made

## Observations (Non-Blocking)

Notes for follow-up tasks; none block the trace shape:

- `_related_evolution_nodes` matches an `EvolutionDecision` whenever its
  `target_id`, `linked_incident_id`, `linked_postmortem_id`, `capital_pool_id`,
  or any `evidence_refs[].ref_id` intersects the union of trace-known refs.
  This is wide for an operator-facing trace and is the right default, but
  `SD-RECON-001` should consider whether `target_type` should constrain the
  match further once reconciliation evidence semantics solidify.
- `_related_broker_order_nodes` and `_related_incident_nodes` filter via
  `data.get("trace_id") == trace_id` in addition to id-set intersections; if
  upstream services adopt nested `authority_refs.trace_id` for these objects
  later, the helpers may need to mirror the `_nested_value` lookup already
  used for telemetry events.
- `_operator_trace_refs` adds a few non-canonical bucket keys
  (`source_record_ids`, `experiment_run_ids`, `approval_decision_ids`,
  `telemetry_event_ids`, `broker_order_event_ids`, `broker_order_ids`,
  `incident_ids`, `postmortem_ids`, `evolution_decision_ids`, `runtime_ids`,
  `request_ids`) on top of the canonical LIN-001 8-key envelope. The
  canonical 8 keys are still emitted; the extras are operator convenience.
  Document this extension in `LIN-001` follow-up if other consumers want to
  rely on the extras.

None of the above affects the derived-only invariant or the acceptance shape.

## Decision

Approve `SD-LIN-TRACE-001`.

The trace projection, HTTP route, contract note, missing-edge handling, and
targeted tests match the Registry / Lineage acceptance shape in
`docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md`. Downstream
work (`SD-RECON-001`, `EP5-002-PACKET-PREP-001`,
`CROSS-REPO-SD-VERIFY-001`) can now consume this trace without re-inventing
deep BFF joins.

## Verification Reproduction

```text
PYTHONPATH=/home/lupin/.local/lib/python3.12/site-packages \
  /home/lupin/.local/bin/pytest \
    services/telemetry/lineage_read/test_service.py \
    services/telemetry/test_main_routes.py -q
......................................                                   [100%]
38 passed in 0.65s
```

## Handoff Back To Owner

Task returns to `Codex` for finalization to `done` per the standard
review_approved → done lifecycle.

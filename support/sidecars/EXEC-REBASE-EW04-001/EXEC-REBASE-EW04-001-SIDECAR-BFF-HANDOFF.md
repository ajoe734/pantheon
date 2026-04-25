# EXEC-REBASE-EW04-001 BFF and Frontend Handoff Packet

**Sidecar kind:** `bff_handoff_packet`  
**Parent task:** `EXEC-REBASE-EW04-001` - Rebaseline EW-04 inspiration graph handoff truth to route-live status  
**Parent owner:** `Copilot`  
**Parent reviewer:** `Codex`  
**Sidecar owner:** `Codex`  
**Sidecar reviewer:** `Claude`  
**Date:** `2026-04-21`  
**Mutates canonical:** `no`

> Support artifact only. This packet does not change canonical truth, runtime
> behavior, or the active coordination bundle. It consolidates the repo's
> current EW-04 route-live truth, the already-closed frontend return loop, and
> the remaining wording drift so the parent owner can absorb the right cleanup
> into the main lane without reopening BFF implementation work.

---

## 1. Executive Summary

`EXEC-REBASE-EW04-001` is no longer a BFF-query-gap task.

What is already true in the repo:

- `GET /api/v1/lineage/inspiration/{artifact_id}` is live in
  `services/control-plane/bff/main.py`.
- `services/control-plane/bff/read_store.py` already projects the composed
  `artifact_id`, `inspiration_edges[]`, `strategy_tags[]`, and
  `meta.surfaces.inspiration` response shape.
- `services/control-plane/bff/test_ew04_inspiration_graph_contract.py` proves
  the published contract, the empty/unavailable branch, and the 404 branch.
- `.coordination/responses/EW-04-inspiration-graph-contract-ready.yaml` is
  `status: live`.
- `.coordination/responses/EW-04-inspiration-graph-lovable-ui-task.yaml` is
  `status: ready`.
- `.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml` is already
  `status: closed` with `pantheon_disposition: loop-complete`.
- `.coordination/requests/PKT-003-inspiration-graph-frontend-feedback.yaml`
  already exists, and the review packet says no Pantheon follow-up remains for
  this packet scope beyond deferred live browser QA.

What still drifts:

- `docs/examples/PKT-003-inspiration-graph.json` still carries
  `_packet_status: "contract-published"`.
- `docs/pantheon-handoffs/EW-004-evolution-workbench/PACKET_FAMILY.md` is
  mostly route-live, but one support-table row still says the EW-04 BFF route
  implementation is pending.
- `.coordination/responses/PKT-003-inspiration-graph-lovable-ui-task.yaml`
  remains a cycle-2 follow-up record (`status: follow-up-required`) even though
  the actual frontend return loop has already been re-reviewed and closed.

The parent task should therefore be treated as a narrative and coordination
rebaseline problem, not as missing BFF or missing frontend implementation.

## 2. Current Repo Truth Snapshot

| Area | Current truth | Notes for parent owner |
|---|---|---|
| Live route | `GET /api/v1/lineage/inspiration/{artifact_id}` | implemented in `services/control-plane/bff/main.py` |
| Route proof | `services/control-plane/bff/test_ew04_inspiration_graph_contract.py` | contract, unavailable, and 404 branches verified |
| Canonical BFF contract | `docs/bff/PKT-003-inspiration-graph.md` | already says route live |
| Screen spec | `docs/screens/PKT-003-inspiration-graph.md` | already says route-live and build-now |
| Frontend change spec | `docs/pantheon-handoffs/PKT-003-inspiration-graph/FRONTEND_CHANGE_SPEC.md` | already says production UI is unblocked |
| Active route-live contract-ready | `.coordination/responses/EW-04-inspiration-graph-contract-ready.yaml` | canonical active handoff truth |
| Naming-chain mirror contract-ready | `.coordination/responses/PKT-003-inspiration-graph-contract-ready.yaml` | exists to satisfy older PKT-003 delivery references |
| Active route-live UI task | `.coordination/responses/EW-04-inspiration-graph-lovable-ui-task.yaml` | `status: ready`; use this as the current dispatch truth |
| Historical mirror UI task | `.coordination/responses/PKT-003-inspiration-graph-lovable-ui-task.yaml` | `status: follow-up-required`; reflects an older front-return cycle, not the active dispatch truth |
| Front return loop | `.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml` + `...frontend-feedback.yaml` | already replay-clean and closed |
| Open BFF gap | none | only the example template exists; no active EW-04 BFF-gap request is open |

## 3. Source References

| Source | Why it matters |
|---|---|
| `services/control-plane/bff/main.py` | confirms the live EW-04 read route exists |
| `services/control-plane/bff/read_store.py` | confirms the composed inspiration projection already exists |
| `services/control-plane/bff/test_ew04_inspiration_graph_contract.py` | executable proof for contract shape and degraded branches |
| `.coordination/responses/EW-04-inspiration-graph-contract-ready.yaml` | canonical route-live handoff truth |
| `.coordination/responses/EW-04-inspiration-graph-lovable-ui-task.yaml` | canonical build-now frontend dispatch truth |
| `.coordination/responses/PKT-003-inspiration-graph-contract-ready.yaml` | PKT-003 naming-chain mirror for delivery dependencies |
| `.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml` | proves the frontend loop returned and Pantheon closed it |
| `.coordination/requests/PKT-003-inspiration-graph-frontend-feedback.yaml` | proves the feedback bundle exists and is replay-clean |
| `.coordination/reviews/PKT-003-inspiration-graph-review.md` | records the final replay checks and closure path |
| `docs/pantheon-handoffs/EW-004-evolution-workbench/PACKET_FAMILY.md` | still contains one stale EW-04 support-table sentence despite overall route-live framing |
| `docs/examples/PKT-003-inspiration-graph.json` | still carries stale `_packet_status` metadata |

## 4. BFF Query Matrix

There is only one EW-04 surface the frontend is allowed to consume.

| Surface | Method and path | Backend-owned purpose | Frontend rule |
|---|---|---|---|
| Inspiration graph read | `GET /api/v1/lineage/inspiration/{artifact_id}` | returns the composed artifact-centered graph view, strategy tags, and freshness signal | use this route only; do not traverse raw lineage routes to synthesize graph state |

Required response invariants already enforced by the live contract:

- `artifact_id` is the canonical queried identity.
- `inspiration_edges[]` is the only truthful edge list.
- `relationship_type` and `influence_weight` are BFF-owned, not UI-derived.
- `strategy_tags[]` is BFF-owned display data.
- `meta.snapshot_at` is the only "data as of" timestamp.
- `meta.surfaces.inspiration` is the only truthful degradation signal.

Failure rules already settled by the live contract:

- 404 means "Artifact not found"; do not synthesize a graph.
- `meta.surfaces.inspiration = "unavailable"` means show degradation and suppress
  graph rendering.
- missing required fields should emit a `bff-gap` handoff rather than inventing
  local state.

## 5. Truthful Operator Journey

### 5.1 Open the screen

```text
Operator opens /evolution/inspiration or /evolution/inspiration/:artifact_id
    |
    +-- no artifact_id
    |     show explicit prompt to enter/select an artifact
    |
    +-- artifact_id present
          continue to live EW-04 route
```

### 5.2 Fetch the graph

```text
GET /api/v1/lineage/inspiration/{artifact_id}
    |
    +-- 200
    |     render artifact-centered graph, strategy tags, and data-as-of timestamp
    |
    +-- 404
          render "Artifact not found"; do not synthesize lineage state
```

### 5.3 Read details

```text
Operator lands on graph response
    |
    +-- inspiration_edges[] non-empty
    |     render directed graph and allow edge selection
    |
    +-- inspiration_edges[] empty
          render "No inspiration edges recorded for {artifact_id}"
```

```text
Operator clicks an edge
    |
    v
Open read-only edge-detail drawer
    |
    v
Show source_artifact_id, relationship_type, influence_weight
```

### 5.4 Handle degradation correctly

```text
meta.surfaces.inspiration = fresh
    -> normal graph rendering

meta.surfaces.inspiration = stale
    -> non-dismissable staleness banner; keep available data visible

meta.surfaces.inspiration = unavailable
    -> non-dismissable degradation banner; suppress graph rendering
```

## 6. Residual Drift for Parent Absorption

These are the remaining cleanup targets. They do not reopen EW-04 route
implementation or frontend implementation.

### DRIFT-EW04-001 — Example payload metadata still says contract-published

Evidence:

- `docs/examples/PKT-003-inspiration-graph.json` still has
  `_packet_status: "contract-published"`.

Impact:

- downstream readers can still infer that EW-04 is pre-live when the route,
  frontend handoff, and closed UI loop all say otherwise.

Parent-owner action:

- if the parent lane touches canonical wording, update the example metadata to
  match route-live truth.

### DRIFT-EW04-002 — EW-004 packet-family support table still has one pending-BFF sentence

Evidence:

- `docs/pantheon-handoffs/EW-004-evolution-workbench/PACKET_FAMILY.md` overall
  inventory says EW-04 is route live and ready.
- the earlier "Existing Pantheon Support" row for EW-04 still ends with
  "BFF route implementation is pending."

Impact:

- one file currently tells two different stories about the same EW-04 module.

Parent-owner action:

- when absorbing the sidecar into the main task, normalize the stale support-row
  sentence to the same route-live truth already used elsewhere in the file.

### DRIFT-EW04-003 — PKT-003 mirror UI task should be treated as historical loop record, not active dispatch truth

Evidence:

- `.coordination/responses/EW-04-inspiration-graph-lovable-ui-task.yaml` is
  `status: ready` and points at the live route.
- `.coordination/responses/PKT-003-inspiration-graph-lovable-ui-task.yaml`
  remains `status: follow-up-required` with cycle-2 republish instructions.
- `.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml` is already
  `status: closed`, `pantheon_disposition: loop-complete`.
- `.coordination/requests/PKT-003-inspiration-graph-frontend-feedback.yaml`
  exists and records the replay-clean feedback bundle.

Impact:

- readers who open the PKT-003 mirror task first can mistake a historical
  follow-up record for the active route-live dispatch artifact.

Parent-owner action:

- do not reopen BFF or frontend work because of this historical file alone.
- if the parent lane wants to reduce confusion, add a clarifying note or
  otherwise make it explicit that the EW-04-named UI task is the active route-live
  dispatch truth and the PKT-003-named UI task is a historical loop record.

## 7. Parent Absorption Checklist

The main lane can absorb this sidecar without reopening implementation work.

1. Keep the live route, contract, screen spec, and frontend change spec as-is.
2. Treat EW-04 as route-live and frontend-returned; do not describe it as
   `pending-bff`.
3. If updating canonical wording, limit scope to narrative cleanup:
   - `docs/examples/PKT-003-inspiration-graph.json`
   - `docs/pantheon-handoffs/EW-004-evolution-workbench/PACKET_FAMILY.md`
   - any task-board / handoff wording that still treats the historical PKT-003
     mirror task as the current dispatch artifact
4. Leave review records and closed-loop request artifacts intact unless the
   coordination bus explicitly requires a closure annotation.

## 8. Reviewer Focus

For `Claude` reviewing this sidecar:

1. Confirm the packet stays support-only and does not mutate canonical truth.
2. Confirm the route-live evidence is sufficient to classify EW-04 as no longer
   blocked on BFF implementation.
3. Confirm the packet distinguishes active dispatch truth
   (`EW-04-inspiration-graph-lovable-ui-task.yaml`) from historical loop
   records (`PKT-003-inspiration-graph-lovable-ui-task.yaml` and the closed
   request pair).
4. Use this packet as a parent-lane absorption guide, not as a substitute for
   the canonical rebaseline edits.

## 9. References

- `docs/reviews/2026-04-20-exec-rebase-ew04-001-codex-review.md`
- `docs/reviews/2026-04-20-luv-reactivate-ew04-001-review.md`
- `.coordination/reviews/PKT-003-inspiration-graph-review.md`
- `.coordination/responses/EW-04-inspiration-graph-contract-ready.yaml`
- `.coordination/responses/EW-04-inspiration-graph-lovable-ui-task.yaml`
- `.coordination/responses/PKT-003-inspiration-graph-contract-ready.yaml`
- `.coordination/responses/PKT-003-inspiration-graph-lovable-ui-task.yaml`
- `.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml`
- `.coordination/requests/PKT-003-inspiration-graph-frontend-feedback.yaml`
- `docs/bff/PKT-003-inspiration-graph.md`
- `docs/screens/PKT-003-inspiration-graph.md`
- `docs/pantheon-handoffs/PKT-003-inspiration-graph/FRONTEND_CHANGE_SPEC.md`
- `docs/pantheon-handoffs/EW-004-evolution-workbench/PACKET_FAMILY.md`
- `docs/examples/PKT-003-inspiration-graph.json`

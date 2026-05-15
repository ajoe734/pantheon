# EXEC-FRONT-RW03-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `EXEC-FRONT-RW03-001` — Implement RW-03 analyze front-end flow against live Pantheon APIs
**Parent Owner**: `Copilot`
**Parent Reviewer**: `Codex`
**Parent Status**: `todo`
**Sidecar Task**: `EXEC-FRONT-RW03-001-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Copilot`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-04-21`
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime / registry / governance / main BFF implementations.
> It packages the current RW-03 route truth, residual hardening gaps, operator
> journey, and frontend consume rules into one parent-owner handoff packet.

---

## 1. Executive Summary

RW-03 is no longer a "missing BFF route family" problem.

What is already true in the repo:

- `GET /api/v1/research/analysis` is live in `services/control-plane/bff/main.py`.
- `GET /api/v1/research/analysis/{analysis_id}` is live in `services/control-plane/bff/main.py`.
- `read_store.py` already projects backend-owned `metric_group_refs`, `metric_groups`,
  and `comparative_summary`.
- `services/control-plane/bff/test_rw03_analyze_contract.py` proves list/detail
  contract behavior and service-backed override behavior.
- `WORKBENCH_DELIVERY_BACKLOG.md` already describes RW-03 as
  `contract-live — analysis list/detail routes implemented`.

What is still missing or drifting:

- no canonical frontend handoff folder exists yet at
  `docs/pantheon-handoffs/RW-03-analyze/`
- no RW-03 `contract-ready`, `lovable-ui-task`, `bff-gap.example`, or
  `ui-done.example` coordination files exist yet
- `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md` still says
  RW-03 is `pending-bff`, which is now stale against code and backlog truth
- the route family remains backlog-open until reads move fully from fallback-capable
  local snapshot behavior to service-owned truth

The parent task should therefore be treated as a frontend activation + handoff
bundle publication problem, not as a missing-route implementation problem.

## 2. Current Repo Truth Snapshot

| Area | Current truth | Notes for parent owner |
|---|---|---|
| Canonical BFF contract | `docs/bff/RW-03-analyze.md` | published and aligned with live routes |
| Example payload | `docs/examples/RW-03-analyze.json` | matches seeded live projections |
| Live list route | `GET /api/v1/research/analysis` | implemented |
| Live detail route | `GET /api/v1/research/analysis/{analysis_id}` | implemented |
| Metric grouping | backend-owned in `read_store.py` | frontend must not regroup raw metrics |
| Comparative summary | backend-owned in `read_store.py` | frontend must not compute local diffs |
| Contract proof | `services/control-plane/bff/test_rw03_analyze_contract.py` | verifies list/detail and service-backed override |
| Frontend handoff bundle | missing | this sidecar is support-only, not a substitute |
| Coordination bundle | missing | parent owner needs to publish if UI work starts |
| Backlog truth | `contract-live` in `WORKBENCH_DELIVERY_BACKLOG.md` | points at `AUTO-HARDEN-RW03-001` as remaining hardening |
| Packet-family truth | still `pending-bff` in `RW-005-research-workbench/PACKET_FAMILY.md` | stale narrative drift |

## 3. Source References

| Source | Why it matters |
|---|---|
| `docs/bff/RW-03-analyze.md` | canonical route family, filter semantics, degradation rules, and backend-owned grouping/comparison boundaries |
| `docs/examples/RW-03-analyze.json` | canonical list/detail payload examples for frontend wiring |
| `services/control-plane/bff/main.py:6564-6659` | live RW-03 route handlers, auth gate, query validation, links, and `meta.surfaces.analysis_results` projection |
| `services/control-plane/bff/read_store.py:4685-4784` | summary/detail projection, date-range filtering, ordering, and service-backed read path |
| `services/control-plane/bff/test_rw03_analyze_contract.py` | executable proof for route truth, invalid-filter rejection, and service-backed store override |
| `WORKBENCH_DELIVERY_BACKLOG.md:61-69` | current backlog truth says RW-03 routes are live but still need hardening |
| `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md` | current stale family narrative that still says RW-03 is pending BFF |

## 4. Live BFF Inventory

### 4.1 Route family

Both expected routes are live:

| Route | Method + Path | Live behavior |
|---|---|---|
| Analyze list | `GET /api/v1/research/analysis` | validates filters, pages results, returns backend-grouped summary rows and `meta.surfaces.analysis_results` |
| Analyze detail | `GET /api/v1/research/analysis/{analysis_id}` | returns summary, grouped metrics, comparative summary, links, and surface freshness metadata |

**Auth**: both routes require `_require_read_role`, so the frontend must treat RW-03 as a read-authorized surface only.

### 4.2 Query and validation semantics

| Input | Accepted values | Enforcement |
|---|---|---|
| `ticket_id` | string | filters one research ticket lineage |
| `experiment_id` | string | filters one experiment lineage |
| `status` | `queued`, `running`, `completed`, `failed` | invalid values return `422 INVALID_PARAMS` |
| `date_range` | `24h`, `7d`, `30d`, `90d` | invalid values return `422 INVALID_PARAMS` |
| `page_token` | backend-owned pagination token | passed through `_page_slice` |
| `page_size` | `1..100` | FastAPI query guard |

Implementation note:

- `main.py` currently tolerates comma-separated `status` values through
  `_split_csv_query(status)`, but the frontend should stay on the published
  discrete vocabulary in `docs/bff/RW-03-analyze.md` unless product explicitly
  expands the contract.

### 4.3 Required payload invariants already enforced

- `analysis_id` is the canonical row/detail identity.
- list rows already expose `metric_group_refs[]`; do not infer groups from raw
  metric names.
- detail payload already exposes ordered `metric_groups[]`; do not regroup,
  re-sort, or backfill metrics client-side.
- `comparative_summary` is already backend-shaped and authoritative.
- `links.workbench_detail`, `links.linked_ticket_detail`, and nullable
  `links.linked_experiment_detail` are the only truthful navigation targets.
- `meta.surfaces.analysis_results` is the only truthful degradation signal for
  this slice.

## 5. Remaining Gaps and Truth Drift

These findings are support-lane only. They do not reopen live RW-03 route truth.

### GAP-RW03-001 — No canonical frontend handoff bundle exists yet

Missing today:

- `docs/pantheon-handoffs/RW-03-analyze/FRONTEND_CHANGE_SPEC.md`
- `.coordination/responses/RW-03-analyze-contract-ready.yaml`
- `.coordination/responses/RW-03-analyze-lovable-ui-task.yaml`
- `.coordination/requests/RW-03-analyze-bff-gap.example.yaml`
- `.coordination/requests/RW-03-analyze-ui-done.example.yaml`

Impact:

- frontend lane lacks the canonical "build this now" packet even though the
  route family is live
- the parent task remains `todo` because there is still no truthful handoff
  surface to absorb

Parent-owner action:

- publish the missing handoff bundle before dispatching implementation

### GAP-RW03-002 — Research Workbench packet-family narrative is stale

Evidence:

- `WORKBENCH_DELIVERY_BACKLOG.md` says RW-03 is
  `contract-live — analysis list/detail routes implemented`
- `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md` still says
  RW-03 is `contract-published — pending BFF implementation`

Impact:

- downstream readers can incorrectly conclude that frontend must wait on missing
  BFF routes
- status/tracking layers tell two different stories about the same module

Parent-owner action:

- when absorbing this sidecar into mainline work, sync packet-family language to
  match the live-route truth without overclaiming service-owned hardening

### GAP-RW03-003 — Reads are live, but still fallback-capable until service-owned truth is wired

Evidence:

- backlog truth explicitly says the remaining gap is moving analysis reads off
  local fallback and onto service-owned truth
- `test_rw03_analyze_contract.py` proves service-backed data overrides the seeded
  snapshot when `PANTHEON_BFF_RESEARCH_ANALYSIS_STORE` is set
- absent that store, the seeded snapshot path can still satisfy the route family

Impact:

- frontend may be production-wired against truthful route shapes while still
  reading from fallback-capable data
- `meta.surfaces.analysis_results = "fresh"` means the current read surface is
  readable and fresh enough, not that the module has finished hardening

Parent-owner action:

- keep the UI handoff truthful about route-live status while separately tracking
  service-owned read hardening as follow-up work

## 6. Truthful Operator Journey

### 6.1 Discover analysis runs

```text
Operator opens the analysis list surface
    |
    v
Applies backend-owned filters:
  ticket_id
  experiment_id
  status
  date_range
    |
    v
GET /api/v1/research/analysis
    |
    +-- 200
    |     returns ordered summary rows with analysis_id, verdict,
    |     metric_group_refs, and canonical links
    |
    +-- 422
          invalid status/date_range; UI must not substitute local filtering
```

### 6.2 Inspect one analysis run

```text
Operator clicks a row
    |
    v
GET /api/v1/research/analysis/{analysis_id}
    |
    +-- 200
    |     returns:
    |       summary
    |       metric_groups[]
    |       comparative_summary
    |       links.linked_ticket_detail
    |       links.linked_experiment_detail (nullable)
    |
    +-- 404
          analysis no longer exists; refresh list instead of inventing a drawer state
```

### 6.3 Read grouped metrics and comparison truth

```text
Operator lands on detail
    |
    v
Reads backend-authored metric groups in returned order
    |
    v
Reads comparative_summary as the only diff truth
    |
    +-- if baseline exists
    |     show baseline_analysis_id, focus_metrics, and delta_highlights
    |
    +-- if no baseline exists
          show the backend-authored comparison basis without local backfill
```

### 6.4 Handle degradation correctly

```text
Response meta.surfaces.analysis_results = fresh
    -> normal list/detail rendering

Response meta.surfaces.analysis_results = stale
    -> non-dismissable staleness banner; keep data visible

Response meta.surfaces.analysis_results = degraded
    -> show available data with degradation banner; do not treat gaps as authoritative absence

Response meta.surfaces.analysis_results = unavailable
    -> suppress normal content and show unavailable state
```

## 7. Frontend Consume Rules

These rules are what the future canonical `FRONTEND_CHANGE_SPEC.md` should say.

### 7.1 Integration checklist

- Use the existing BFF client only; do not add raw `fetch` or `axios` in page components.
- Call only `GET /api/v1/research/analysis` and
  `GET /api/v1/research/analysis/{analysis_id}` for RW-03.
- Submit only the published query params: `ticket_id`, `experiment_id`,
  `status`, `date_range`, `page_token`, `page_size`.
- Treat `analysis_id` as the only canonical row/detail identity.
- Render `metric_group_refs[]` exactly as summary hints; never derive them from
  metric names.
- Render `metric_groups[]` exactly in backend order and backend grouping.
- Render `comparative_summary` exactly as returned; never fetch two details and
  compute your own comparison.
- Use `links.workbench_detail`, `links.linked_ticket_detail`, and nullable
  `links.linked_experiment_detail` as navigation truth; never synthesize URLs.
- If a metric has no `baseline_value` or `delta_display`, render the missing
  delta honestly; do not backfill or compute one locally.
- If `meta.surfaces.analysis_results` is `degraded` or `unavailable`, do not
  present empty rows/cards as authoritative proof that nothing exists.
- If any required field diverges from the published contract, emit a RW-03
  `bff-gap` handoff instead of mocking a comparison or metric panel.

### 7.2 Minimum UI surfaces implied by the live contract

| Surface | Required data |
|---|---|
| List page | `analysis_id`, `ticket_id`, `experiment_id`, `status`, `run_at`, `summary.headline`, `summary.verdict`, `metric_group_refs[]` |
| Detail header | `analysis_id`, `ticket_id`, `experiment_id`, `status`, `run_at`, `completed_at`, `summary.*` |
| Metric panels | `metric_groups[].label`, `description`, ordered `metrics[]`, delta fields when provided |
| Comparative panel | `comparative_summary.basis`, `baseline_analysis_id`, `focus_metrics[]`, `comparisons[].delta_highlights[]` |
| Degradation chrome | `meta.surfaces.analysis_results` |

## 8. Parent Absorption Checklist

The parent owner can absorb this sidecar into the main lane with the following
supporting follow-up, without changing RW-03 route semantics.

### 8.1 Publish the missing frontend handoff bundle

Recommended files:

- `docs/pantheon-handoffs/RW-03-analyze/FRONTEND_CHANGE_SPEC.md`
- `.coordination/responses/RW-03-analyze-contract-ready.yaml`
- `.coordination/responses/RW-03-analyze-lovable-ui-task.yaml`
- `.coordination/requests/RW-03-analyze-bff-gap.example.yaml`
- `.coordination/requests/RW-03-analyze-ui-done.example.yaml`

### 8.2 Keep truth layers aligned

Recommended sync targets:

- `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md`
  - move RW-03 from `pending-bff` to language consistent with route-live truth
- any future RW-03 frontend handoff bundle
  - clearly distinguish "live route family" from "service-owned hardening still open"

### 8.3 Preserve the sidecar boundary

- do not change `docs/bff/RW-03-analyze.md` unless contract truth actually changes
- do not reopen BFF runtime implementation work under this sidecar packet
- route any service-owned read hardening into its own parent-owned follow-up

## 9. Reviewer Focus

For `Copilot` reviewing this sidecar:

1. Confirm the packet stays support-only and does not mutate canonical truth.
2. Confirm the live-route claims match `main.py`, `read_store.py`, and
   `test_rw03_analyze_contract.py`.
3. Confirm the identified drift is real:
   backlog says route-live, packet family still says pending-bff, and no
   canonical RW-03 frontend handoff bundle exists yet.
4. Use this packet as the absorption guide for `EXEC-FRONT-RW03-001`, not as a
   replacement for the missing canonical handoff bundle.

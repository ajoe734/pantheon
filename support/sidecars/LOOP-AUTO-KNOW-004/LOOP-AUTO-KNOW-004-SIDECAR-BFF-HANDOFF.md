# LOOP-AUTO-KNOW-004 - BFF and Frontend Handoff Packet

**Sidecar kind:** `bff_handoff_packet`
**Sidecar task:** `LOOP-AUTO-KNOW-004-SIDECAR-BFF-HANDOFF`
**Parent task:** `LOOP-AUTO-KNOW-004` - Extract Agora interaction evidence into datasets
**Parent owner:** `Copilot`
**Parent reviewer:** `Codex`
**Sidecar owner:** `Claude2`
**Sidecar reviewer:** `Claude`
**Prepared:** `2026-06-27`
**Mutates canonical:** `no`
**Status:** Approved — closing out (PR #2466)

> Support artifact only. This packet does not change L1 truth, core
> contracts, BFF implementation, loop catalog registry, runtime authority, or
> governance implementation.
> It packages current BFF/Agora facts and suggested handoff guidance for
> the `LOOP-AUTO-KNOW-004` parent owner to decide what to absorb.

---

## 1. Purpose

`LOOP-AUTO-KNOW-004` needs a governed background worker that extracts Agora
interaction evidence (ask sessions, feedback events, journal entries, notes,
insights, training examples) into `Observe` or `Learn` learning datasets
without ever directly mutating running artifacts or live LEAN execution.

The key policy constraint (`LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md § 3.5`):

> Interaction evidence enters Observe / Learn or dataset builder only.
> It does not promote artifact, does not change running artifact, does not touch
> live LEAN.

The loop catalog (`docs/deployment/loop-catalog.registry.json`) marks both the
desired-state query and actual-state query as `planned`, and notes that
`controller_contract.status = not_implemented`. The operator truth projection
is assigned to `LOOP-AUTO-KNOW-004` and `LOOP-AUTO-BFF-001`.

This sidecar is intentionally narrow: it records the current interaction BFF
surface, the BFF query gap matrix, the operator journey, and frontend handoff
notes. It does not implement the parent extraction worker or BFF route changes.

---

## 2. Parent Acceptance Mapping

| Parent acceptance | Current evidence | Handoff implication |
|---|---|---|
| Interaction evidence is routed into Observe or Learn datasets | The BFF has write routes for signals, sessions, feedback, notes, journal, insights, and training examples (`/bff/agora/*`). The `read_store` knows about `agora_signals`, `agora_feedback`, `agora_handoffs`, `agora_training_examples`, etc. (`services/control-plane/bff/read_store.py:7151`). No dataset extraction worker exists. | Parent work must add a worker that consumes captured interaction records and writes `Observe` or `Learn` dataset entries. BFF needs a projection to expose extraction status. |
| Dataset extraction is idempotent | The loop catalog specifies `idempotency_key: planned: interaction_id + extraction_kind + target_dataset` and `duplicate_event_policy: planned` (`docs/deployment/loop-catalog.registry.json:576`). No enforcement exists yet. | Parent extraction worker must reject duplicate (interaction_id, extraction_kind, target_dataset) tuples. BFF should expose the idempotency key used per extraction record. |
| Evidence never promotes artifact or mutates running runtime directly | BFF write routes for signals, training examples, handoffs, and persona-lab commit only write to Agora stores or create handoff records routed to persona management review (`services/control-plane/bff/main.py:21850`). No direct runtime mutation path exists in current BFF. | Parent implementation must never add a write path from extraction worker to runtime-manager, runtime bindings, or LEAN directly. The BFF operator surface should label each extracted record with its dataset class (Observe / Learn) and confirm it did not touch runtime authority. |

---

## 3. Current Implementation Snapshot

### 3.1 Planning Context

- SA-21 defines `LOOP-AUTO-KNOW-004` as the Wave 6 slice whose output is:
  "Interaction evidence becomes governed learning datasets without touching runtime authority."
  (`docs/04/pantheon_sa/SA-21_global_loop_inventory_autopilot_execution_plan.md:329`).
- SA-21 Wave 6 acceptance says Agora evidence must be routed into `Observe`/`Learn`
  datasets only, never direct runtime mutation
  (`docs/04/pantheon_sa/SA-21_global_loop_inventory_autopilot_execution_plan.md:338`).
- The loop catalog marks `agora_interaction_evidence` at `current_maturity: api-only` and
  `target_maturity: reconciled`, with controller, desired-state query, and
  actual-state query all `not_implemented` or `planned`
  (`docs/deployment/loop-catalog.registry.json:519`).

### 3.2 Agora Interaction Write Surface (BFF)

| Interaction type | Write route | BFF line | Write destination |
|---|---|---|---|
| Signal (ask/research brief) | `POST /bff/agora/signals` | `services/control-plane/bff/main.py:20527` | `agora_signals` read-store overlay |
| Signal feedback | `POST /bff/agora/feedback`, `POST /bff/agora/signals/{id}/feedback` | `services/control-plane/bff/main.py:20680`, `20797` | `agora_feedback`, `agora_signal_feedback` |
| Session (committee / ask) | `POST /bff/agora/sessions` | `services/control-plane/bff/main.py:20943` | `agora_sessions` read-store overlay |
| Session message | `POST /bff/agora/sessions/{id}/messages` | `services/control-plane/bff/main.py:21046` | session messages overlay |
| Note (market note) | `POST /bff/agora/notes` | `services/control-plane/bff/main.py:21298` | `agora_notes` overlay |
| Journal entry | `POST /bff/agora/journal` | `services/control-plane/bff/main.py:21372` | journal store (audit write) |
| Insight | `POST /bff/agora/insights` | `services/control-plane/bff/main.py:21477` | `agora_insights` overlay |
| Training example | `POST /bff/agora/training-examples` | `services/control-plane/bff/main.py:21636` | `agora_training_examples` overlay |
| Handoff (persona-lab commit) | `POST /bff/agora/persona-lab/{id}/actions/submit-commit` | `services/control-plane/bff/main.py:21805` | `agora_handoffs` overlay + command |
| Handoff queue | `GET /bff/agora/handoffs` | `services/control-plane/bff/main.py:21776` | read from `agora_handoffs` dataset |

### 3.3 Agora Read Surface (BFF)

| Route | Returns | Evidence |
|---|---|---|
| `GET /bff/agora/signals` | Signal list with optional `review_status` filter | `services/control-plane/bff/main.py:20504` |
| `GET /bff/agora/signals/{id}` | Signal detail with feedback references | `services/control-plane/bff/main.py:20655` |
| `GET /bff/agora/sessions` | Committee / ask session list | `services/control-plane/bff/main.py:20922` |
| `GET /bff/agora/sessions/{id}` | Session detail with messages | `services/control-plane/bff/main.py:20996` |
| `GET /bff/agora/notes` | Market note list | `services/control-plane/bff/main.py:21278` |
| `GET /bff/agora/journal` | Decision journal entries | `services/control-plane/bff/main.py:21351` |
| `GET /bff/agora/insights` | Insight card list | `services/control-plane/bff/main.py:21457` |
| `GET /bff/agora/training-examples` | Training example list | `services/control-plane/bff/main.py:21616` |
| `GET /bff/agora/handoffs` | Handoff queue | `services/control-plane/bff/main.py:21776` |
| `GET /bff/agora/daily` | Daily Agora briefing | `services/control-plane/bff/main.py:20465` |
| `GET /bff/agora/memory` | Agora memory records | `services/control-plane/bff/main.py:21562` |

### 3.4 Existing Test Evidence

| Test | What it proves | Limitation for parent task |
|---|---|---|
| `services/control-plane/bff/test_bff_consol_009_fixture_pack_b.py` | Agora sessions and signals are listable and have required structure (session active with sse_topic, signals non-empty). | It proves fixture read, not dataset extraction worker liveness. |
| `services/control-plane/bff/test_pkt005_sse_substrate_contract.py` | SSE streams for Agora signals and sessions are present and well-formed. | It proves SSE shape, not extraction provenance. |
| `services/control-plane/bff/test_bff_mgmt_ai_persistence_2026_06_03.py` | Journal write route and persistence mechanics work correctly. | It proves journal write, not that journal entries flow into governed learning datasets. |
| `services/research/imitation/test_dataset_builder.py` | Imitation dataset builder validates feedback event ids. | It proves dataset builder shape for research layer, not BFF-visible extraction loop liveness. |

### 3.5 Evidence Caveat

None of the existing tests or fixtures demonstrate that captured Agora
interactions have been extracted into `Observe` or `Learn` datasets by a
governed background worker. All interaction records live in BFF read-store
overlays or seed fixture state. Treat all existing Agora BFF records as
`api-only` maturity evidence only — not as extraction-loop proof.

---

## 4. BFF Query Gap Matrix

| Query need | Current BFF visibility | Gap for `LOOP-AUTO-KNOW-004` |
|---|---|---|
| Interactions pending extraction | None. No BFF route lists interactions by extraction status. | Add a route or field that exposes which interaction records have not yet been consumed by the extraction worker. |
| Extraction job health | None. No route reports extraction worker liveness, last success, or last failure. | BFF or operator panel needs `last_extraction_success_at`, `last_extraction_failure_at`, `pending_backlog_count`, and `worker_alive` for `agora_interaction_evidence` controller. |
| Extraction provenance per record | None. Individual Agora records (training examples, feedback, insights) have no `extraction_status`, `dataset_class`, `dataset_id`, or `extracted_at` field. | Parent should add provenance fields to extracted interaction records so the operator can confirm route to Observe vs Learn. |
| Dataset class projection | None. The BFF exposes `Observe` and `Learn` as OODA stage labels (`services/control-plane/bff/main.py:49949`) but not as learning-dataset provenance for individual interaction extractions. | Extraction worker output should carry `dataset_class: observe \| learn` and the BFF should project it back to the operator. |
| Idempotency evidence | None. No route shows which `(interaction_id, extraction_kind, target_dataset)` tuples have been committed. | BFF should expose a query surface or extraction record list so duplicate-rejection can be verified by operator and tested in contract tests. |
| Loop controller health | None. `agora_interaction_evidence` loop has no `current_controller_owner` in the loop catalog, and no BFF projection for loop health is implemented (assigned to `LOOP-AUTO-BFF-001`). | Parent can emit loop-health fields; `LOOP-AUTO-BFF-001` will wire them into the operator loop board. |

---

## 5. Suggested Parent BFF Projection

This is a support recommendation, not canonical contract text. The parent owner
can use this shape directly or adapt it while preserving the extraction-status
split and the Observe/Learn provenance chain.

### 5.1 Interaction Evidence Extraction Status Field

Add `extraction` to each relevant Agora record type returned by the BFF. The
parent extraction worker writes this back through the Agora service store or
BFF read-model.

Suggested field fragment for a training example, feedback, insight, note, or
journal record:

```json
{
  "extraction": {
    "status": "pending | extracted | skipped | failed",
    "dataset_class": "observe | learn | null",
    "dataset_id": "dataset-...",
    "idempotency_key": "trn-agora-abc123::training_example::learn_dataset",
    "extracted_at": "2026-06-27T10:00:00Z",
    "failure_reason": null
  }
}
```

Derivation rules:

- `status = pending` when the extraction worker has not yet consumed this record.
- `status = extracted` when the worker has committed to a governed dataset and
  recorded the provenance.
- `status = skipped` when the record was evaluated but excluded from extraction
  (e.g. below quality threshold, duplicate, or filtered by policy).
- `status = failed` when extraction was attempted but a durable failure prevents retry.
- `dataset_class` must be `observe` or `learn`; never `runtime` or `live`.

### 5.2 Extraction Worker Health Projection

Add a compact extraction health field to the `agora_interaction_evidence` loop
health projection (wired into `LOOP-AUTO-BFF-001` operator panel later):

```json
{
  "loop_id": "agora_interaction_evidence",
  "controller_health": {
    "worker_alive": true,
    "last_success_at": "2026-06-27T09:58:00Z",
    "last_failure_at": null,
    "pending_backlog_count": 0,
    "extracted_count_24h": 12,
    "failure_reason": null
  }
}
```

Derivation rules:

- `worker_alive` must come from a supervised process heartbeat or health check,
  not from `last_success_at` recency alone.
- `pending_backlog_count` must reflect the actual queue of unextracted interaction
  records, not a cached counter.
- `extracted_count_24h` allows the operator to distinguish "worker idle" from
  "worker healthy with no new interactions".

### 5.3 Operator Route Target

Do not introduce a new BFF writer for this slice. Suggested route targets:

- Read: reuse `GET /bff/agora/training-examples`, `GET /bff/agora/insights`,
  `GET /bff/agora/journal` etc. — add `extraction` field to item payloads.
- Extraction health: new narrow read route
  `GET /bff/agora/interaction-evidence/health` or fold into
  `GET /api/v1/operator/loop-health?loop_id=agora_interaction_evidence` once
  `LOOP-AUTO-BFF-001` is implemented.
- Do not add a new write route from BFF to the extraction worker. The worker
  must consume interaction records from its own store or event queue, not from a
  BFF push command.

---

## 6. Operator Journey

1. Operator opens the Agora interaction evidence panel.
   - Query: `GET /bff/agora/training-examples` (and equivalents for
     insights, journal, feedback, notes).
   - Expected behavior: each record shows `extraction.status` and
     `extraction.dataset_class`.

2. Operator checks extraction worker health.
   - Query: `GET /bff/agora/interaction-evidence/health` (or equivalent
     loop health endpoint once `LOOP-AUTO-BFF-001` is live).
   - Expected: `worker_alive`, `pending_backlog_count`, `last_success_at`,
     `last_failure_at`.

3. Operator diagnoses extraction failure.
   - If `extraction.status = failed` on one or more records: surface
     `failure_reason` per record.
   - If `worker_alive = false`: treat as loop-controller outage — surface
     `last_failure_at` and direct operator to loop health board.
   - If `pending_backlog_count > 0` and `worker_alive = true`: worker is
     making progress; no action needed unless backlog grows unbounded.

4. Operator confirms dataset class assignment.
   - For each extracted record: confirm `extraction.dataset_class` is
     `observe` or `learn` and that no record shows `runtime`, `live`, or
     a null class when `status = extracted`.
   - The operator panel must not synthesize a green state when dataset
     class is unknown or null for an extracted record.

5. Operator confirms idempotency.
   - Re-submitting the same interaction (duplicate event) should not create
     a second extracted record. Operator panel should surface duplicate-
     rejection evidence (skipped status with `idempotency_key`).

6. Operator does not take runtime action from this panel.
   - The Agora interaction evidence panel has no CTA that touches runtime-
     manager, LEAN, or running persona bindings.
   - All extraction CTAs route to the governed dataset store only.

---

## 7. Frontend Handoff Rules

- Render the `extraction.status` badge on each Agora interaction record:
  pending (gray), extracted (green), skipped (yellow), failed (red).
- Render `extraction.dataset_class` as a chip: Observe or Learn.
- Do not infer extraction success from BFF write success. The BFF write
  routes (`POST /bff/agora/training-examples` etc.) record the interaction
  capture; extraction status is separate and driven by the worker.
- Do not show a single aggregate "Agora health" indicator that merges
  interaction capture, extraction, and dataset availability. Surface each
  stage separately.
- Disable "submit to learning" or equivalent CTAs if `extraction.status`
  is `failed` or `worker_alive` is false. Do not silently re-submit.
- Surface `failure_reason` and `extracted_at` in an expandable detail row,
  not only in a tooltip.
- The extraction health row must show a warning state when
  `pending_backlog_count > 0` for more than a configurable threshold
  (suggested: >50 records or >1h without a new `last_success_at`).
- If the BFF payload lacks an `extraction` field for a record,
  treat it as `status = pending` (not extracted) — do not synthesize
  a green state from absence.
- Do not route extraction failure recovery from the frontend. Direct
  operators to the loop health board and extraction worker logs.

---

## 8. Suggested Parent Verification

Focused parent tests should cover at least these cases:

| Case | Expected extraction state |
|---|---|
| New training example captured, extraction worker not yet run | `status = pending`, `dataset_class = null` |
| Extraction worker processes training example into learn dataset | `status = extracted`, `dataset_class = learn`, `dataset_id` set |
| Duplicate interaction event submitted | Second extraction attempt returns `status = skipped`, original `idempotency_key` preserved |
| Extraction fails (worker error) | `status = failed`, `failure_reason` set, record not counted as extracted |
| Signal feedback extracted into observe dataset | `status = extracted`, `dataset_class = observe` |
| Worker health check with no pending records | `worker_alive = true`, `pending_backlog_count = 0` |
| Worker down (crash) | `worker_alive = false`, `last_failure_at` set |
| Extracted record does not appear in runtime bindings or LEAN state | No `RuntimeBinding`, no LEAN mutation provenance on the record |

Suggested focused commands for the parent after implementation:

```bash
pytest services/control-plane/bff/ -k "agora_interaction_evidence or training_example or extraction"
pytest services/training-session/ -k "extraction or dataset"
```

Add a new parent-owned BFF contract test that:
1. writes a training example via `POST /bff/agora/training-examples`,
2. verifies `extraction.status = pending` on the BFF GET response,
3. simulates worker extraction and verifies `status = extracted` and
   `dataset_class` is `observe` or `learn`,
4. re-submits the same interaction and verifies `status = skipped`.

---

## 9. Non-Goals And Boundaries

- Do not edit L1 policy, canonical loop catalog, or `ai-status.json` task
  records from this sidecar.
- Do not make BFF the authoritative extraction-state writer. The extraction
  worker owns extraction state; BFF is a read projection.
- Do not route interaction evidence into runtime-manager, LEAN, or live
  deployment from BFF.
- Do not merge `dataset_class = observe | learn` with OODA stage labels
  (`observe`, `orient`, `decide`, `act`). These are distinct concepts.
- Do not mark `LOOP-AUTO-KNOW-004` complete from seed Agora data, static
  fixture training examples, or BFF write-route success alone.
- Do not treat `pending_backlog_count = 0` as proof the extraction worker
  has processed all records — verify via `extracted_count_24h` and actual
  dataset records.

---

## 10. Reviewer Checklist

| Check | Status |
|---|---|
| Support artifact only | PASS |
| Canonical truth untouched | PASS |
| BFF query gaps identified | PASS |
| Operator journey included | PASS |
| Frontend handoff included | PASS |
| Parent acceptance mapped | PASS |
| No runtime/registry/governance implementation changed | PASS |
| No direct LEAN or runtime mutation path introduced | PASS |

Suggested review command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/LOOP-AUTO-KNOW-004/LOOP-AUTO-KNOW-004-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF/frontend handoff packet approved: captures current Agora BFF surface, extraction query gaps, Observe/Learn dataset provenance, operator journey, frontend rules, and parent verification guidance without canonical truth or runtime changes." \
  ./scripts/ai-status.sh approve LOOP-AUTO-KNOW-004-SIDECAR-BFF-HANDOFF \
  "Support-only BFF/frontend handoff packet approved for parent owner absorption."
```

If factual correction is needed:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen LOOP-AUTO-KNOW-004-SIDECAR-BFF-HANDOFF \
  "Describe the missing source, incorrect stage mapping, or scope violation."
```

---

## 11. Handoff Status

Prepared by Claude2 for Claude review. Parent owner Copilot (with reviewer Codex)
can use this packet as a support-only starting point for `LOOP-AUTO-KNOW-004`;
parent absorption remains the parent owner's implementation decision.

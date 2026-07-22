# Evidence: LOOP-AUTO-BFF-004 — Cross-Loop Operator Drills

Task: `LOOP-AUTO-BFF-004`
Title: Run cross-loop operator drills
Owner: Claude
Reviewer: Claude2
Date: 2026-06-27
Branch: task/LOOP-AUTO-BFF-004

## Summary

Executed source-to-health and runtime-to-incident-to-evolution-proposal
cross-loop operator drills as autopilot Wave 7 wave closeout.

Both drills pass at service-level (unit + contract + cross-service chain).
Maturity reached: `reconciled` for both chains.
No full-stack Docker Compose or dev VM drill was run; `proven-live` is not
claimed.

---

## Drill 1: Source-to-Health Flow

### Chain Proven

```
SourceHealth connector record (source_ingestion service)
  → BFF _overlay_source_health_truth projection
  → persona panel health_source=source_ingest, live_ingestion_enabled=True
  → /bff/v5/loop-health/source_ingestion operator_truth=Reconciled live truth
```

### Test Evidence

File: `services/control-plane/bff/test_loop_auto_bff004_cross_loop_drill.py`

| Test | Assertion | Result |
|---|---|---|
| `test_source_health_connector_truth_projects_to_persona_panel` | `source_health_source == "source_ingest"` | PASS |
| `test_source_health_connector_truth_projects_to_persona_panel` | `live_ingestion_enabled is True` | PASS |
| `test_source_health_connector_truth_projects_to_persona_panel` | `provider_statuses.finmind == "read_ok"` | PASS |
| `test_source_health_connector_truth_projects_to_persona_panel` | binding `health_source == "source_ingest"` | PASS |
| `test_loop_health_endpoint_shows_reconciled_truth_label` | `highest_truth_level == "reconciled_live_proof"` | PASS |
| `test_loop_health_endpoint_shows_reconciled_truth_label` | `accepted_live_liveness is True` | PASS |
| `test_loop_health_endpoint_shows_reconciled_truth_label` | `operator_truth.label == "Reconciled live truth"` | PASS |
| `test_loop_health_endpoint_shows_reconciled_truth_label` | `is_live_truth is True` | PASS |

### Prior Task Evidence Consumed

- `docs/deployment/evidence/loop-auto-src-004/README.md`: SourceHealth truth
  wired into BFF persona panels (62 tests, 25 passed smoke).
- `docs/deployment/evidence/loop-auto-bff-003/README.md`: Seed/fixture/snapshot/
  registry/scheduled/live truth labels in operator panels (9 tests).

---

## Drill 2: Runtime-to-Incident-to-Evolution-Proposal Flow

### Chain Proven

```
heartbeat_loss threshold breach (incidents service)
  → IncidentCase opened (status=open, telemetry_event_ids=[tel-bff004-drill-hb-001])
  → incident resolved (status=resolved, resolved_at set)
  → Postmortem draft (postmortems service, incident_id linked, status=draft)
  → postmortem published (status=published, published_at set)
  → EvolutionDecision proposed (evolution service, decision_state=proposed,
    linked_postmortem_id and linked_incident_id set, is_active=True,
    approval_decision_id=None — governance gate required)
```

### Test Evidence

File: `services/control-plane/bff/test_loop_auto_bff004_cross_loop_drill.py`

| Test | Assertion | Result |
|---|---|---|
| `test_full_runtime_to_evolution_proposal_chain` | incident created with `status=open` | PASS |
| `test_full_runtime_to_evolution_proposal_chain` | `tel-bff004-drill-hb-001` in `telemetry_event_ids` | PASS |
| `test_full_runtime_to_evolution_proposal_chain` | incident resolved with `resolved_at` | PASS |
| `test_full_runtime_to_evolution_proposal_chain` | postmortem draft created, linked to incident | PASS |
| `test_full_runtime_to_evolution_proposal_chain` | postmortem published with `published_at` | PASS |
| `test_full_runtime_to_evolution_proposal_chain` | evolution proposal created (HTTP 201) | PASS |
| `test_full_runtime_to_evolution_proposal_chain` | `decision_state == "proposed"` (not auto-approved) | PASS |
| `test_full_runtime_to_evolution_proposal_chain` | `linked_postmortem_id` and `linked_incident_id` set | PASS |
| `test_full_runtime_to_evolution_proposal_chain` | `approval_decision_id is None` (gate not bypassed) | PASS |
| `test_chain_is_idempotent_on_duplicate_postmortem_publish` | duplicate publish → HTTP 200, same decision_id | PASS |
| `test_incident_without_resolution_blocks_postmortem_draft` | open incident → PostmortemDraftConsumerError | PASS |

### Prior Task Evidence Consumed

- `docs/deployment/evidence/loop-auto-rt-005/README.md`: Runtime fleet 114-test
  evidence packet (stack restart, kill-one-worker, retire binding, signal isolation).
- `docs/deployment/evidence/loop-auto-dep-004/README.md`: BFF stage truth split
  (approval/plan/saga/binding/runtime_fleet stages separately visible).
- `docs/deployment/evidence/loop-auto-tel-005/review-claude2-2026-06-27.md`:
  Telemetry incident replay suite (25 tests — order rejection spike, heartbeat loss,
  PnL drift, recovery, idempotency).
- `docs/deployment/evidence/loop-auto-evo-005/review-claude.md`: Evolution rollback
  follow-through 20-test suite (approved).
- `docs/deployment/evidence/loop-auto-know-006-consultation-workflow-executor.md`:
  Consultation workflow executor evidence.

---

## Verification Run

```bash
python3 -m pytest services/control-plane/bff/test_loop_auto_bff004_cross_loop_drill.py -v
```

Result: `5 passed, 4 warnings in 3.98s`

Regression check:

```bash
python3 -m pytest \
  services/control-plane/bff/test_loop_health_read_model_contract.py \
  services/control-plane/bff/test_loop_inventory_read_model_contract.py \
  services/control-plane/bff/test_loop_auto_dep004_stage_truth.py -q
```

Result: `12 passed, 12 warnings in 10.40s`

```bash
python3 -m pytest \
  services/incidents/tests/test_incident_replay_suite.py \
  services/postmortems/test_main_routes.py \
  services/evolution/test_evo_005_rollback_followthrough.py -q
```

Result: `64 passed in 10.91s`

Total: **81 passing tests** with no regressions.

---

## Maturity Assessment

| Loop | Chain | Maturity Reached | Evidence Basis |
|---|---|---|---|
| source_ingestion | source → health → operator panel | reconciled | service-level tests, cross-service chain test |
| capital_pool_execution | runtime → incident | reconciled | RT-005 fleet tests, drill chain test |
| telemetry_reconciliation | telemetry → incident | reconciled | TEL-005 replay suite |
| evolution | incident → postmortem → evolution | reconciled | drill chain test, EVO-005 |
| bff_health_monitoring | loop health truth labels | reconciled | BFF-003, drill endpoint test |

No loop is claimed at `proven-live` maturity. `proven-live` requires:
- Docker Compose full-stack restart drill or dev VM fleet drill
- Live telemetry events from a running paper runtime
- Not performed in this wave

---

## Remaining Blockers

1. **No Docker Compose or dev VM stack drill** — all chains are proven at
   service-level (in-process TestClient). A full-stack drill would require
   a running Docker Compose environment with all services wired and would be
   the next milestone for advancing to `proven-live`.

2. **Upstream dependency tasks still at `todo` in ai-status.json** —
   LOOP-AUTO-SRC-004, LOOP-AUTO-RT-005, LOOP-AUTO-DEP-004, LOOP-AUTO-TEL-005,
   LOOP-AUTO-EVO-005, LOOP-AUTO-KNOW-006, LOOP-AUTO-BFF-003 all have evidence
   packets but were not formally transitioned to `done` before this drill.
   Their implementations are present and tested; the workflow closure is pending.

3. **LOOP-AUTO-KNOW-004 and LOOP-AUTO-KNOW-005** — Agora interaction evidence
   extraction and human imitation/shadow evaluation scheduler are not in the
   drill scope (Wave 6 tasks, not Wave 7 BFF closure targets).

---

## Safety Boundary

- No live-capital execution.
- No approval gate bypass.
- No panel-only closure — drill evidence is in executed test code with assertions.
- No seed fixture promoted as live proof.
- Evolution proposal created with `decision_state=proposed`; approval gate required
  before any action is dispatched.

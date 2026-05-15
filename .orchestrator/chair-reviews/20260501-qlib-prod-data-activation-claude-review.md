# Review: P2-QLIB-PROD-DATA-ACTIVATION-001

Reviewer: Claude
Task: Qlib production data activation packet and real-backend smoke
Date: 2026-05-01
Verdict: **APPROVED**

---

## Acceptance Criteria Verification

### AC-1: Governed Qlib production dataset proof names provider entitlement freshness PIT and storage evidence

PASS.

`services/research/qlib/adapter/production_activation.py` → `validate_production_dataset_proof()` enforces all required fields before building any candidate handoff:

- **Provider**: name, source_class (must be `research_grade` or `internal_can`), dataset_id
- **Entitlement**: entitlement_ref or entitlement_tags, license_scope, allowed_use must include `research` and `model_training`, must not include order-capable targets
- **Freshness**: status must be `fresh`, as_of, last_ingested_at, positive freshness_sla_seconds
- **PIT**: point_in_time=True, event_time_field, available_time_field, source_watermark
- **Storage**: durable=True, backend, dataset_ref (must match a workflow source_dataset_ref), snapshot_ref, path, sha256-prefixed checksum
- **Audit**: ingest_run_id, normalization_run_id, evidence_bundle_ref, rate_limit_policy_ref
- **Controls**: no_order_route=True, execution_targets must not include order-capable targets

Tests in `test_production_activation.py` cover the happy path and negative cases (order-capable target rejection, missing entitlement/PIT rejection).

`integrations/qlib/activation_packet.md` documents the proof contract table and canonical command shape.

### AC-2: Qlib real backend path runs or returns explicit install/config error while stub CI remains deterministic

PASS.

- `worker.py` requires `QLIB_BACKEND=stub|real` explicitly (exits with code 3 otherwise).
- `production_activation_smoke.py` requires `--backend stub|real` argument.
- `QlibLightGBMBackend` returns an explicit `"Qlib backend unavailable. Install services/research/qlib/requirements.txt first."` error when upstream `qlib` is not installed — no silent fallback.
- `StubLightGBMBackend` is fully deterministic and used for CI.

### AC-3: Qlib candidate handoff is produced with artifact_state draft or candidate and no registry write or order-capable route

PASS.

`build_production_activation_packet()` always produces:
- `artifact_state = "draft"` (from registry entry)
- `requested_artifact_state = "candidate"` (from candidate packet)
- `deployment_stage = "none"` (deployment_summary.current_stage=none)
- `registry_write_authority = "registry_service_only"`
- `order_route = "none"`
- `safety_assertions`: no_registry_write, no_order_route, no_broker_session, no_capital_binding, deployment_stage_remains_none — all True

---

## Verification Run

```
python3 -m unittest discover -s services/research/qlib -p 'test_*.py'
→ Ran 32 tests in 3.362s — OK

python3 services/research/qlib/smoke_test.py
→ assertions: OK

python3 -m pytest -q services/research-worker-gateway/tests/test_research_worker_gateway_qlib_activation.py
→ 2 passed in 3.68s
```

---

## Additional Notes

- The activation_packet.md correctly documents remaining gaps (RS-003 StrategySpec ref, actual governed production dataset) as next-step blockers outside this task's scope.
- `OSS_INTEGRATION_CHECKLIST.md` Qlib row is updated with the production-data proof evidence summary and correctly remains `smoke-tested` until registry review admits a real production data packet.
- ACTIVATION_CRITERIA.md §1A (Gate 1A: Production Dataset Proof) is now backed by a real implementation.
- No registry writes, broker sessions, paper/canary/live routes, or capital bindings were introduced.

Review is complete. Returning to owner (Codex2) for finalization.

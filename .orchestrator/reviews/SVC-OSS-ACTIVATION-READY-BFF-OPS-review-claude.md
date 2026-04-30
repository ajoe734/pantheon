# Review: SVC-OSS-ACTIVATION-READY-BFF-OPS

Reviewer: Claude
Date: 2026-04-30
Decision: **approve**

---

## Scope Verified

Task: Expose OSS activation-ready operations in BFF
Owner: Codex
Artifacts reviewed: `services/control-plane/bff/main.py`, `services/control-plane/bff/read_store.py`, `services/control-plane/bff/test_research_oss_preactivation_contract.py`, `services/control-plane/bff/BFF_API_CONTRACT.md`, `services/control-plane/bff/BFF_SURFACE_INVENTORY.md`

---

## Acceptance Criteria Assessment

| Criterion | Evidence | Verdict |
|---|---|---|
| BFF reads capability and gate state via service-backed clients | `read_store.get_research_oss_preactivation_snapshot` calls `_fetch_dormant_json` for all four services; tests mock `_http_json_get` to verify the data path end-to-end | ✅ pass |
| BFF exposes run history artifact refs logs and errors for OSS lanes | Response includes `run_history`, `artifact_refs`, `log_summary`, `error_summary`; per-job `artifact_refs` and `logs` verified in `test_operator_research_oss_activation_ready_reports_offline_artifacts_logs_and_errors` | ✅ pass |
| BFF commands cannot bypass activation gates or governance | `operator_controls.activation_commands = "not_exposed"`; `blocked_commands` dict explicitly names all governance paths; `production_activation = "disabled"` and `activated = False` are hardcoded in response builder | ✅ pass |
| BFF tests cover closed gate and enabled offline status | `test_operator_research_oss_preactivation_aggregates_fail_closed_services` covers all backends at `gate_state = "fail_closed"`; `test_operator_research_oss_activation_ready_reports_offline_artifacts_logs_and_errors` covers `activation_ready` gate state | ✅ pass |
| Legacy snapshot fallback is not used for staging production paths | Both contract tests use `allow_local_snapshot_fallback=False` when constructing `ReadSurfaceStore` | ✅ pass |

---

## Additional Observations

**New endpoint:** `/api/v1/operator/research/oss-activation-ready` added with `operator` RBAC, read-only, no write paths.

**Alias preserved:** `/api/v1/operator/research/oss-preactivation` kept as backward-compatible alias; both endpoints share `_build_research_oss_activation_ready_response`; `meta.surfaces` always includes both keys, verified in test.

**Docs updated:** `BFF_API_CONTRACT.md` and `BFF_SURFACE_INVENTORY.md` (surface RS-04) both record the new surface with correct non-bypass contract.

**Test count:** 352 passed as stated by Codex. Contract is complete.

**Degraded path:** `test_operator_research_oss_preactivation_degrades_without_enabling_activation` verifies that when no services are configured the surface degrades gracefully without exposing any activation path.

No issues found. All acceptance criteria are satisfied.

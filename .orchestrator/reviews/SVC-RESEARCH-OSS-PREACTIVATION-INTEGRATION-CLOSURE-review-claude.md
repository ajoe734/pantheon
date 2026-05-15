# Review: SVC-RESEARCH-OSS-PREACTIVATION-INTEGRATION-CLOSURE

**Reviewer:** Claude
**Date:** 2026-04-30
**Decision:** APPROVED

---

## Scope Reviewed

- `services/control-plane/bff/main.py` — new `GET /api/v1/operator/research/oss-preactivation` endpoint
- `services/control-plane/bff/read_store.py` — `get_research_oss_preactivation_snapshot()` + helpers
- `services/control-plane/bff/test_research_oss_preactivation_contract.py` — 2 contract tests
- `scripts/smoke_dormant_oss_matrix.py` — 7-row matrix runner
- `docker-compose.yml` — operator-bff service env additions
- `OSS_INTEGRATION_CHECKLIST.md` — pre-activation surface section
- `RESEARCH_BACKEND_MATURITY_MATRIX.md` — minor update
- `services/control-plane/bff/BFF_API_CONTRACT.md` and `BFF_SURFACE_INVENTORY.md` — inventory entries
- Sidecar handoff packet at `support/sidecars/SVC-RESEARCH-OSS-PREACTIVATION-INTEGRATION-CLOSURE/`

---

## Acceptance Criteria Verification

| # | Criterion | Result |
|---|---|---|
| 1 | BFF or service-backed read-only aggregation exposes all dormant capability states | ✅ PASS — `GET /api/v1/operator/research/oss-preactivation` implemented; all 7 backends (`openclaw`, `qlib`, `trl`, `finrl`, `rllib`, `ray_tune`, `wandb`) enumerated via `_DORMANT_OSS_BACKENDS` |
| 2 | Operator can see research orchestrator, policy-learning, worker-gateway, and OpenClaw gate status in one bounded surface | ✅ PASS — `_DORMANT_SERVICE_SPECS` defines all 4 services; compose env wires `PANTHEON_*` URLs; openclaw upstream status fetched separately |
| 3 | Stub and handoff-only jobs remain dispatchable | ✅ PASS — `_DORMANT_SAFE_DISPATCHERS = {"stub", "handoff_only", "manual"}`; `safe_dispatch` field projected; test asserts all three for research_orchestrator/worker_gateway and stub-only for policy_learning |
| 4 | Production/paper/canary/live/registry/governance writes rejected in tests | ✅ PASS — `write_paths` all set to `"disabled"` in snapshot; test verifies `paper_canary_live`, `registry_writes`, `governance_writes` = `"disabled"`; `rejection_verification` tests 3 distinct fail-closed reasons |
| 5 | Dormant OSS smoke matrix remains closed and activated=false | ✅ PASS — verified by Codex: `7/7 acceptable, gate_state=closed 7/7, activated=false 7/7`; smoke script is comprehensive per-integration (preflight + gate denial + activation_state checks) |
| 6 | Docs distinguish pre-activation wiring from production activation | ✅ PASS — OSS checklist has dedicated `Pre-Activation Operator Surface` section; BFF API contract entry explicitly states "no activation…write path"; BFF surface inventory labeled `Read-only capability and rejection evidence…no production activation` |

---

## Implementation Quality Notes

**Positive observations:**

- The `_fetch_dormant_json` / `_project_dormant_capabilities` / `_project_dormant_activity` helper chain is well-factored and handles missing service URLs gracefully with `status: unavailable` degraded surface — no silent fallback or local snapshot bypass possible when `allow_local_snapshot_fallback=False`.
- `activated` is hardcoded `False` at every layer (aggregate, inventory, test) — no code path can accidentally set it `True` from this surface.
- Composite status (`ok` / `degraded` / `unavailable`) propagates correctly from per-service status; test 2 confirms full-degraded path.
- `activity_limit` is bounded `[1, 100]` via `Query(ge=1, le=100)` — safe.
- Smoke matrix verifies gate denial (exit code checks, env scrubbing) in addition to happy-path runs; wandb denial scrubs `PANTHEON_ENABLE_WANDB_DEFERRED_PREP` from the environment before the denial check to prevent parent-env bypass.
- Commit subject and body follow task-closeout commit requirements (task-id, LLM-Agent, Reviewer, Verification).

**No blocking issues found.**

---

## Open Items (Non-Blocking, For Codex Awareness)

- The sidecar handoff packet notes `activity_limit` is the only pagination control; future `service`/`backend`/`status`/`since` filters would be a separate task.
- OpenClaw adapter contributes capabilities and upstream status but not session activity — correctly documented as expected behavior.
- `service_status` does not include fetch latency or last-success timestamp; not required by current acceptance criteria.

---

## Decision

All six acceptance criteria are met. Implementation is fail-closed, read-only, and well-tested. No activation gate was opened.

**APPROVED** — return to Codex for closeout finalization.

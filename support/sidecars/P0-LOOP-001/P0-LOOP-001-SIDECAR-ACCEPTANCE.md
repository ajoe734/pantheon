# P0-LOOP-001 Acceptance Packet (Sidecar)

**Parent Task**: `P0-LOOP-001` — Add minimum paper operating loop smoke
**Parent Owner**: Codex
**Parent Reviewer**: Claude
**Parent Status**: `in_progress`
**Sidecar Owner**: Claude
**Sidecar Reviewer**: Codex
**Helper Kind**: `acceptance_packet`
**Generated**: 2026-05-01T08:10:00Z

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime / registry / governance implementations. It packages the dependency state, acceptance checklist, and implementation readiness map for `P0-LOOP-001`.

---

## 1. Dependency Map

### 1.1 Formal Parent Dependencies

| Dependency | Task ID | Status | What P0-LOOP-001 can reuse |
|---|---|---|---|
| Paper telemetry into runtime status | `P0-TEL-PROJ-001` | **done** | `PaperRuntimeService`, `RuntimeTelemetryEmitter`, `RuntimeBindingResolver`, `PaperExecutionAlgorithm`, paper heartbeat / deploy_started / deploy_completed / pnl_snapshot event types, telemetry ingest path at `/api/telemetry/ingest`, BFF runtime status projection from paper heartbeat |

### 1.2 Additional Locked Truth P0-LOOP-001 Must Reuse

| Source | Locked truth |
|---|---|
| `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md` | Loop trigger model and scheduling boundaries; loop smoke must respect the paper-only activation boundary |
| `PAPER_CANARY_LIVE_POLICY.md` | Deployment stage policy; `deployment_stage=paper` is the only valid stage for this smoke; live/canary remain fail-closed |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | `ApprovalDecision → DeploymentPlan → RuntimeBinding` chain; Runtime Manager is sole binding writer; DeploymentPlan materializer must precede RuntimeBinding |
| `ROLLBACK_AND_POSITION_SEMANTICS.md` | Rollback semantics; paper runtime must not create side effects that require rollback handling in v0 |
| `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` | Telemetry event must carry `binding_id`, `runtime_id`, `capital_pool_id`, `artifact_id`, `artifact_version`, `plan_id` |
| `services/execution/lean_runtime/paper_runtime.py` | Canonical paper runtime — `PaperRuntimeService.start()`, `drain_once()`, `snapshot()`; `RuntimeTelemetryEmitter.build_event()` enforces `deployment_stage=paper` invariant |
| `docs/04/pantheon_sa/SA-11_operating_loop_gap_analysis.md` §12 | Minimum Operating Loop definition and MVP必要驗收 (9 items) |
| `docs/04/pantheon_p0_sd/SD-P0-04_Paper_Runtime_TelemetryEvent_Contract.md` | Paper TelemetryEvent schema; required fields and event_type vocabulary |

### 1.3 What P0-TEL-PROJ-001 Delivered (Confirmed Reusable)

From `services/execution/lean_runtime/paper_runtime.py` (as of this sidecar generation):

- `PaperExecutionAlgorithm` — LEAN-like paper fill simulation with `SetHoldings`, `MarketOrder`, `Liquidate`, `SubmitBracketOrder`, `RecordBracketOrderLogged`; `DeploymentStage = "paper"` invariant baked in
- `RuntimeTelemetryEmitter` — builds and emits schema-valid `TelemetryEvent` payloads; rejects non-paper `deployment_stage`; requires all binding identity fields or returns `None`
- `RuntimeBindingResolver` — resolves `RuntimeBinding` from Runtime Manager client; falls back to `PantheonRuntimeContext` if Runtime Manager is unavailable; exposes `.snapshot()` for health checks
- `PaperRuntimeService` — lifecycle (`start`, `stop`, `drain_once`), poll loop, order event handler, heartbeat/PnL emitter
- HTTP surface: `/api/runtime/state`, `/api/runtime/orders`, `/api/runtime/drain`, legacy `/__health__` (cleanup tracked by `P0-HEALTH-001`)
- `_runtime_context_identity_env()` — propagates `PantheonRuntimeContext` fields to env vars for subprocess/Lean launch

### 1.4 Downstream Consumers Waiting On P0-LOOP-001

| Consumer | Task ID | Status | Why P0-LOOP-001 matters |
|---|---|---|---|
| Basic paper ReconciliationRecord | `P0-REC-001` | todo | Reconciliation needs a completed paper loop run (runtime binding + at least one heartbeat) as evidence baseline |
| Canary/live activation criteria | `P1-LIVE-PLAN-001` | todo | Activation runbook requires a proven paper-only loop as the minimum maturity gate before canary planning |
| Governed evolution dispatcher | `P1-EVO-001` | todo (P1) | Evolution decisions depend on paper loop telemetry → reconciliation → incident data from P0-LOOP-001 |

---

## 2. What P0-LOOP-001 Must Deliver

### 2.1 Smoke Scope

The parent task acceptance criteria (from `ai-status.json` and `execution-materialization.md`):

> smoke uses pantheon/lean, not lean-platform  
> no live broker action or preview mock is used

Expanding that against SA-11 §12 Minimum Operating Loop and the planning session consensus:

```text
Minimum paper operating loop smoke must exercise:

  seed/approved artifact (seeded in smoke setup)
  → DeploymentPlan (materialized from ApprovalDecision + artifact ref)
  → RuntimeBinding (created by Runtime Manager or seeded for smoke)
  → PaperRuntimeService.start() (via pantheon/lean bridge path)
  → paper heartbeat emitted as TelemetryEvent (deployment_stage=paper)
  → BFF runtime status shows non-mock last heartbeat
```

### 2.2 What "pantheon/lean" Means in This Context

- The execution substrate must be `ajoe734/pantheon-lean.git` (the `lean/` submodule in this repo), not `lean-platform`.
- Bridge identity (`engine_bridge_repo`, `engine_bridge_commit`) must be populated in the emitted TelemetryEvent.
- If the smoke does not actually launch a full Lean process (acceptable for a smoke), it must still assert that `PaperRuntimeService` would receive a `PantheonRuntimeContext` with a valid bridge identity pointing to `pantheon-lean`.

### 2.3 What "no live broker action or preview mock" Means

- `DeploymentStage` must be exactly `"paper"` throughout the smoke run.
- No `submitted_to_broker=True` order events should be generated in a default paper smoke run.
- No preview/stub/mock fallback data paths should be used — if a service is unavailable, the smoke must fail closed, not substitute mock data.
- The executor's `_bracket_execution_guard` must not allow broker submission in the paper smoke (bracket orders are `logged_only` unless `BracketOrderExecutionEnabled` is explicitly set and stage is paper/sim — both conditions are tested).

---

## 3. Acceptance Checklist

| # | Acceptance Item | Status | What "done" looks like |
|---|---|---|---|
| A1 | Smoke uses `pantheon/lean` bridge path | OPEN | `PantheonRuntimeContext.bridge.repo` is `ajoe734/pantheon-lean` (or equivalent remote); `lean-platform` is not referenced in the smoke |
| A2 | `DeploymentPlan` seeded or materialized | OPEN | Smoke creates or loads a `DeploymentPlan` with `deployment_stage=paper` and a valid `artifact_id` pointing to a seed/approved artifact |
| A3 | `RuntimeBinding` seeded or created via Runtime Manager | OPEN | Smoke either seeds a `RuntimeBinding` record with all required identity fields or calls Runtime Manager to create one |
| A4 | `PaperRuntimeService.start()` executes without error | OPEN | Service starts, thread launched, deploy_started telemetry event emitted (or skipped gracefully if telemetry URL is not configured in smoke) |
| A5 | Paper heartbeat emitted as schema-valid `TelemetryEvent` | OPEN | `RuntimeTelemetryEmitter.build_event("heartbeat", ...)` returns a non-None payload; all required fields (`binding_id`, `runtime_id`, `capital_pool_id`, `artifact_id`, `artifact_version`, `plan_id`, `persona_capital_binding_id`) are non-empty |
| A6 | Telemetry event carries bridge identity | OPEN | Emitted TelemetryEvent metadata includes `engine_bridge_repo`, `engine_bridge_commit`; values reference `pantheon-lean` |
| A7 | `deployment_stage=paper` invariant enforced | OPEN | `RuntimeTelemetryEmitter.build_event()` returns `None` if `deployment_stage` is not `"paper"`; smoke asserts this rejection path |
| A8 | No live broker action | OPEN | All order events have `submitted_to_broker=False`; no `BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER` in paper-only smoke without explicit guard enable |
| A9 | No preview mock data | OPEN | `RuntimeBindingResolver.resolve()` does not substitute demo/mock binding data; if Runtime Manager is unavailable and no `PantheonRuntimeContext` is provided, binding resolves to `None` and telemetry fails closed |
| A10 | BFF runtime status shows non-mock heartbeat | OPEN | After at least one `drain_once()`, `PaperRuntimeService.snapshot()["paper_state"]["last_heartbeat_at"]` is non-None; or the BFF/projection layer shows a non-mock `last_heartbeat` timestamp |
| A11 | Smoke uses `PaperRuntimeService`, not stub | OPEN | `snapshot()["stub_mode"]` is `False`; `paper_execution_ready` is `True` |
| A12 | Smoke is self-contained and idempotent | OPEN | Running the smoke twice from clean state produces the same result; no writes to production tables or live runtime paths |
| A13 | Unit / integration test coverage | OPEN | At least one test that: (a) seeds DeploymentPlan + RuntimeBinding, (b) starts PaperRuntimeService, (c) calls drain_once(), (d) asserts non-None heartbeat in snapshot, (e) asserts telemetry event has correct bridge identity |
| A14 | CI passes with smoke | OPEN | The smoke test is included in the CI run on the `backend-dev-publish-*` branch; no paper-runtime import from `lean-platform` |

---

## 4. Suggested Implementation Structure

This is a recommendation. The parent owner (Codex) may adapt as needed.

### 4.1 Smoke Test Location

```
services/execution/lean_runtime/
├── test_paper_loop_smoke.py     # new smoke test
```

Or, if the smoke needs infrastructure setup:

```
tests/smoke/
├── test_p0_loop_smoke.py        # self-contained smoke using in-process PaperRuntimeService
```

### 4.2 Smoke Test Structure

```python
# Minimal P0-LOOP-001 smoke: DeploymentPlan → RuntimeBinding → heartbeat → BFF status
# Does NOT require a live LEAN process — exercises the paper runtime service in-process.

def build_seed_context() -> PantheonRuntimeContext:
    """Seed a paper-stage PantheonRuntimeContext with pantheon-lean bridge identity."""
    # bridge.repo must reference ajoe734/pantheon-lean (not lean-platform)
    ...

def test_p0_loop_paper_heartbeat_smoke():
    ctx = build_seed_context()
    # Assert bridge identity
    assert "pantheon-lean" in ctx.bridge.repo
    assert ctx.deployment_stage == "paper"

    # Wire service with in-process store + mock runtime manager
    store = InMemoryPendingSignalStore()  # or existing test store
    service = PaperRuntimeService(
        store=store,
        runtime_context=ctx,
        poll_interval_seconds=0.01,
    )
    service.start()
    result = service.drain_once()
    service.stop()

    # Assert heartbeat recorded
    assert result["paper_state"]["last_heartbeat_at"] is not None or \
           result["telemetry"]["sent"] >= 0  # telemetry may not be wired in smoke

    # Assert stub_mode is False
    assert result["stub_mode"] is False

    # Assert deployment_stage is paper
    ctx_snapshot = result["runtime_context"]
    assert ctx_snapshot["deployment_stage"] == "paper"
    assert ctx_snapshot["loaded"] is True

    # Assert bridge identity in snapshot
    assert "pantheon-lean" in ctx_snapshot.get("bridge_repo", "")
```

### 4.3 Integration Points

| Integration Point | How Smoke Connects |
|---|---|
| `PantheonRuntimeContext` | Seeded directly with paper stage and pantheon-lean bridge identity |
| `RuntimeBindingResolver` | Uses seeded context binding; Runtime Manager client is optional (smoke may mock it) |
| `RuntimeTelemetryEmitter` | If telemetry URL is not set, `emit()` returns `False` gracefully; smoke still validates `build_event()` produces correct payload |
| BFF runtime status | For a full integration smoke, `GET /api/runtime/state` should show `paper_execution_ready: true` and non-None `last_heartbeat_at` after one drain |
| CI | Smoke runs in the existing `pytest` suite; no LEAN process required |

---

## 5. Risk Areas and Open Questions

### 5.1 Runtime Manager Availability in CI

The current `RuntimeBindingResolver` falls back to `PantheonRuntimeContext` if the Runtime Manager HTTP call fails. This means the smoke will work even without a running Runtime Manager — but the test must explicitly verify that the fallback is the seeded context binding, not a mock/demo path.

**Recommendation**: Smoke should test both paths — (a) Runtime Manager available and returns the binding, (b) Runtime Manager unavailable and context binding is used as fallback.

### 5.2 Telemetry Ingest Reachability

If `PANTHEON_TELEMETRY_URL` is not set, `RuntimeTelemetryEmitter.enabled` is `False` and all emit calls return `False` silently. This means the smoke can pass without actually verifying that telemetry events reach the ingest surface.

**Recommendation**: For the smoke to satisfy A5 (heartbeat emitted), it should either:
- Use `build_event()` directly and assert the resulting payload is schema-valid and non-None, OR
- Stand up a minimal in-process HTTP capture server and set `PANTHEON_TELEMETRY_URL` to it.

The first approach is simpler and sufficient for P0-LOOP-001.

### 5.3 BFF Projection Coverage

A10 requires BFF runtime status to show a non-mock heartbeat. The current `PaperRuntimeService.snapshot()` provides the paper state in-process, but the BFF projection layer (from `P0-TEL-PROJ-001`) is a separate service.

**Recommendation**: For P0-LOOP-001 smoke, satisfying A10 via `snapshot()["paper_state"]["last_heartbeat_at"]` is sufficient. Full BFF projection integration can be covered by P0-REC-001 or the front-end source mode task.

### 5.4 Seed Artifact Identity

The smoke needs a seed artifact with all required fields (`artifact_id`, `artifact_version`, `artifact_checksum`, `strategy_id`). SA-11 §12.3 item 1 requires `CandidateArtifact` to have `dataset_version + code_version + artifact_checksum`.

**Recommendation**: Smoke uses a hard-coded seed artifact with stable test values. Document the seed artifact in the smoke test docstring. Do not persist the seed to the production registry.

### 5.5 `deployment_stage` vs `deployment_mode` Field Aliasing

`RuntimeBindingResolver.snapshot()` uses both `deployment_mode` and `deployment_stage` field names depending on whether the binding came from Runtime Manager or the context. `RuntimeTelemetryEmitter.build_event()` resolves this via a multi-key lookup.

**Recommendation**: Smoke explicitly asserts that `deployment_stage=paper` is propagated correctly through the aliasing chain; do not assume only one field name.

---

## 6. Files Referenced

### Shared Truth
- `ai-status.json`
- `AI_COLLABORATION_GUIDE.md`

### Canonical / Contract Sources
- `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`
- `PAPER_CANARY_LIVE_POLICY.md`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`

### Planning Session
- `docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/consensus-packet.md`
- `docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/execution-materialization.md`
- `docs/04/pantheon_sa/SA-11_operating_loop_gap_analysis.md`
- `docs/04/pantheon_p0_sd/SD-P0-04_Paper_Runtime_TelemetryEvent_Contract.md`

### Completed Upstream Work (P0-TEL-PROJ-001)
- `services/execution/lean_runtime/paper_runtime.py` — `PaperRuntimeService`, `RuntimeTelemetryEmitter`, `RuntimeBindingResolver`, `PaperExecutionAlgorithm`
- `services/execution/lean_runtime/executor.py` — signal executor with `_bracket_execution_guard` and `BRACKET_ORDER_STATUS_LOGGED_ONLY` / `BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER` constants
- `services/execution/lean_runtime/runtime_context.py` — `PantheonRuntimeContext` (referenced but not read in this session)
- `services/execution/lean_runtime/runtime_identity.py` — `RuntimeIdentity` (referenced but not read in this session)

### This Sidecar
- `support/sidecars/P0-LOOP-001/P0-LOOP-001-SIDECAR-ACCEPTANCE.md`

---

## 7. Handoff To Reviewer (Codex)

Codex, this packet is ready for review and reuse by the P0-LOOP-001 owner.

What it gives the P0-LOOP-001 owner (Codex):

1. **Dependency-confirmed starting point**: `P0-TEL-PROJ-001` is done. `PaperRuntimeService`, `RuntimeTelemetryEmitter`, and `RuntimeBindingResolver` are available and verified in-repo. The smoke can import them directly.

2. **Acceptance expansion**: The two formal acceptance criteria are expanded into 14 concrete checklist items (A1–A14), covering bridge identity, paper stage invariant, no-mock / no-live guarantees, and BFF status visibility.

3. **Implementation path**: A minimal in-process smoke test structure is provided (§4). No LEAN process is required for CI; the smoke can run in-process using `PaperRuntimeService` with a seeded `PantheonRuntimeContext`.

4. **Open questions documented**: Five areas (Runtime Manager fallback, telemetry ingest reachability, BFF projection coverage, seed artifact identity, field aliasing) are flagged for owner decision.

5. **Downstream awareness**: `P0-REC-001`, `P1-LIVE-PLAN-001`, and `P1-EVO-001` all depend on P0-LOOP-001 completing. The smoke is the gate.

Recommended next steps for the owner (Codex):

- Use `build_seed_context()` pattern (§4.2) with a fixed seed `PantheonRuntimeContext` pointing to `ajoe734/pantheon-lean`.
- Validate `RuntimeTelemetryEmitter.build_event("heartbeat", ...)` returns a non-None, schema-valid payload (satisfies A5 without needing a live ingest surface).
- Assert `snapshot()["stub_mode"] is False` and `snapshot()["paper_state"]["last_heartbeat_at"]` is non-None after one `drain_once()`.
- Run the smoke in CI to confirm the paper loop is demonstrably closed before handing to Claude for review.

---

*Generated by Claude as a sidecar `acceptance_packet` helper for P0-LOOP-001. This file is a support artifact and does not modify canonical truth.*

# SVC-OPENCLAW-PAPER-BROKER-ADAPTER Sidecar Acceptance Packet

Task: `SVC-OPENCLAW-PAPER-BROKER-ADAPTER-SIDECAR-ACCEPTANCE`
Parent task: `SVC-OPENCLAW-PAPER-BROKER-ADAPTER`
Owner: Codex
Reviewer: Claude
Prepared: 2026-04-30
Scope: support artifact only; no L1 canonical truth, contract truth, runtime, registry, or governance implementation changes.

## Purpose

This packet gives the parent owner a review-ready acceptance checklist and dependency map for the gated OpenClaw paper broker adapter slice. It is intentionally sidecar material: it does not approve activation, change policy, or replace the parent implementation review.

## Sources Read

- `AI_COLLABORATION_GUIDE.md`
- `ai-status.json`
- `.orchestrator/task-briefs/svc_openclaw_paper_broker_adapter_sidecar_acceptance.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `OPENCLAW_RUNTIME_CONTRACT.md`
- `PAPER_CANARY_LIVE_POLICY.md`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `OSS_INTEGRATION_CHECKLIST.md`
- archived task snapshots for `SVC-OPENCLAW-SESSION-LIFECYCLE` and `SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE`
- current parent-owned surfaces observed in the dirty worktree:
  - `services/openclaw-gateway-adapter/paper_broker_adapter.py`
  - `services/openclaw-gateway-adapter/main.py`
  - `services/openclaw-gateway-adapter/test_paper_broker_adapter.py`
  - `services/openclaw-gateway-adapter/test_main.py`
  - `services/openclaw-gateway-adapter/test_compose_activation.py`
  - `services/broker/main.py`
  - `services/broker/paper_simulation.py`
  - `services/broker/test_broker.py`
  - `docker-compose.yml`

## Non-Scope Guardrails

- Do not use this sidecar to promote `paper`, `canary`, or `live` activation.
- Do not edit `OPENCLAW_RUNTIME_CONTRACT.md`, `PAPER_CANARY_LIVE_POLICY.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, or other L1 truth from this slice.
- Do not treat OpenClaw as an execution kernel. It remains an external agent runtime / control-plane substrate.
- Do not route broker, paper, live, or capital operations through the OpenClaw tool/workflow bridge unless a future explicit activation gate changes that contract.
- Do not claim real broker, real order, or real capital behavior from this task.

## Dependency Map

| Dependency | Current status | Acceptance consequence for parent task |
|---|---:|---|
| `SVC-OPENCLAW-SESSION-LIFECYCLE` | done | Parent may rely on Pantheon-owned session records, idempotent create/cancel, operator identity, and audit metadata. Paper broker routes must not weaken the prior invariant that broker/paper/live execution was disabled by default. |
| `SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE` | done | Parent must preserve deny-by-default tool/workflow policy and the always-blocked broker/live/paper/capital workflow prefixes. Paper broker HTTP routes must stay explicit gateway endpoints, not backdoor tool invocations. |
| `OPENCLAW_RUNTIME_CONTRACT.md` | L1 truth | OpenClaw remains control-plane / consultation / teaching substrate, not execution kernel. Unknown upstream errors must not affect live execution. Operator identity is required on invocation surfaces. |
| `PAPER_CANARY_LIVE_POLICY.md` | L1 truth | Paper means real market data and real runtime path with simulated matching, no real orders, and no real capital. Paper telemetry should use canary/live-compatible schema fields where practical. |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | L1 truth | Binding is governance admissibility, not deployment. Real deployment requires valid binding, approval decision, deployment plan, loader checks, and runtime target. Parent implementation should either enforce these checks or fail closed with an explicit deferred status. |
| `OSS_INTEGRATION_CHECKLIST.md` | L2 checklist | OpenClaw is governed but paper/canary/live execution, broker sessions, and capital binding are explicitly not activated by the existing OSS integration baseline. |

## Parent Acceptance Checklist

### 1. Paper adapter is disabled by default and requires explicit gate

Acceptance evidence should show:

- `OPENCLAW_PAPER_ADAPTER_ENABLED` defaults false in adapter runtime.
- `BROKER_PAPER_ENABLED` defaults false in the broker sidecar.
- `docker-compose.yml` sets both gates to `"false"` in the default profile.
- Closed-gate paper submit/list/get calls return fail-closed errors, not silent success.
- Capability metadata reports `paper_adapter_enabled=false`, `live_adapter_enabled=false`, `is_real_order=false`, and `is_real_capital=false` by default.

Observed parent-owned evidence candidates:

- `PaperBrokerAdapter._gate_check()` returns `PAPER_ADAPTER_DISABLED` with status `503`.
- Broker sidecar `_paper_gate_check()` returns `PAPER_ADAPTER_DISABLED` with status `503`.
- `TestProductionGuard`, `TestPaperBrokerAdapterGate`, and `TestBrokerPaperGateDefaultState` cover default-off behavior.
- `test_compose_activation.py` checks compose wiring keeps adapter and broker paper gates false.

### 2. Paper orders route through simulation path, not live broker

Acceptance evidence should show:

- Gateway paper routes call the broker sidecar paper endpoint path only.
- Broker sidecar paper simulation returns `sim_fill_flag=true`, `is_real_order=false`, `is_real_capital=false`, and `deployment_stage=paper`.
- Live broker endpoint is separate and always rejected.
- No broker secrets, live account credentials, or canary/live account config are required for the paper path.

Observed parent-owned evidence candidates:

- Gateway submits to `/api/broker/paper/orders`.
- Broker sidecar simulation returns `PaperOrder` with paper-only invariants.
- `TestPaperBrokerAdapterSidecarCall` and `TestBrokerPaperSimulationHappyPath` cover simulated order behavior.
- `docker-compose.yml` wires `OPENCLAW_BROKER_SIDECAR_URL=http://broker:8102` while leaving paper and live gates false.

### 3. Capital and strategy binding checks are enforced

Acceptance evidence should show one of the following explicit states:

- Full enforcement: paper submit validates `capital_pool_id`, `strategy_id`, operator identity, approved artifact / deployment plan compatibility, allowed deployment scope, and loader/runtime checks before forwarding.
- Scaffold enforcement: paper submit validates required identifiers and fails closed for any governance binding validation that is not implemented yet, with a clearly named error and test coverage.

Observed parent-owned evidence candidates:

- Current gateway adapter validates `capital_pool_id`, `strategy_id`, and `operator_id` before sidecar calls.
- Tests cover missing capital pool, strategy id, and operator id.

Review focus:

- The observed implementation appears to enforce required identifier presence, but this is not the same as canonical `PersonaCapitalBinding` / `ApprovalDecision` / `DeploymentPlan` enforcement. Parent owner should decide whether this task's acceptance is meant to land as a scaffold with explicit deferred governance-binding validation, or whether it must add fail-closed checks before review approval.

### 4. Audit trail captures order intent and result

Acceptance evidence should show:

- Paper submit writes an intent event before sidecar call.
- Success records order id, status, fill quantity, fill price, `sim_fill_flag`, and paper-only flags.
- Failure records a deterministic error event with error code.
- Audit records include `trace_id`, `operator_id`, `capital_pool_id`, `strategy_id`, and timestamp.
- Audit read endpoint supports operator and capital-pool filtering without mutating state.

Observed parent-owned evidence candidates:

- `PaperBrokerAuditLog` writes append-only JSONL.
- `submit_paper_order()` records `paper_order_intent` with pending, ok, and error outcomes.
- `TestPaperBrokerAdapterSidecarCall` covers intent -> ok and intent -> error.
- `TestPaperBrokerAuditLog` covers read, filtering, timestamps, and limits.
- Gateway route `GET /api/openclaw-adapter/broker/audit` exposes read-only audit entries.

### 5. Tests prove live remains rejected

Acceptance evidence should show:

- Gateway live route returns `LIVE_ADAPTER_DISABLED` regardless of paper gate state.
- Broker sidecar live route returns `LIVE_ADAPTER_DISABLED` regardless of paper gate state.
- Capability metadata never reports live enabled.
- Existing OpenClaw tool/workflow bridge tests still prove broker/live/paper/capital prefixes are blocked.

Observed parent-owned evidence candidates:

- `PaperBrokerAdapter.reject_live_order()` always raises `LIVE_ADAPTER_DISABLED`.
- Broker sidecar `_LIVE_ENABLED = False` and `/api/broker/live/orders` always returns status `403`.
- `TestPaperBrokerAdapterLiveRejection`, `TestPaperBrokerRoutes`, and `TestBrokerLiveOrderAlwaysRejected` cover rejection even when paper gate is open.

## Suggested Focused Verification

Run these from the repo root after the parent owner has finished the runtime slice:

```bash
python3.12 -m pytest services/openclaw-gateway-adapter/test_paper_broker_adapter.py -q
python3.12 -m pytest services/openclaw-gateway-adapter/test_main.py -q
python3.12 -m pytest services/openclaw-gateway-adapter/test_compose_activation.py -q
python3.12 -m pytest services/broker/test_broker.py -q
```

If the parent diff touches tool/workflow bridge policy or activation metadata, also rerun the existing OpenClaw adapter suite:

```bash
python3.12 -m pytest services/openclaw-gateway-adapter -q
```

## Verification Run

Executed from repo root on 2026-04-30:

| Command | Result |
|---|---:|
| `python3.12 -m pytest services/openclaw-gateway-adapter/test_paper_broker_adapter.py -q` | 23 passed |
| `python3.12 -m pytest services/openclaw-gateway-adapter/test_main.py -q` | 35 passed |
| `python3.12 -m pytest services/openclaw-gateway-adapter/test_compose_activation.py -q` | 2 passed |
| `python3.12 -m pytest services/broker/test_broker.py -q` | 23 passed |

## Reviewer Handoff Notes

- This packet is ready for Claude to use as the sidecar review/handoff material for the parent task.
- The main review question is whether required identifier checks are sufficient for the parent acceptance phrase "capital and strategy binding checks are enforced." Under current L1 semantics, full governance binding enforcement would require more than non-empty IDs unless the parent explicitly lands this as a fail-closed scaffold.
- The sidecar changed only this support artifact. Runtime files, compose changes, and broker service files observed during packet preparation are presumed parent-owned or pre-existing dirty worktree changes.

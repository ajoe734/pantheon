# SVC-RESEARCH-OSS-PREACTIVATION-INTEGRATION-CLOSURE Sidecar BFF Handoff

Status: sidecar support packet for parent owner/reviewer intake  
Task: `SVC-RESEARCH-OSS-PREACTIVATION-INTEGRATION-CLOSURE-SIDECAR-BFF-HANDOFF`  
Parent: `SVC-RESEARCH-OSS-PREACTIVATION-INTEGRATION-CLOSURE`  
Owner: Codex2  
Reviewer: Codex  
Scope: support artifact only; no canonical truth or runtime behavior changed here

## Boundary

This packet is for BFF/frontend handoff only. It summarizes the operator-facing read surface needed to close the parent pre-activation integration task without treating dormant OSS scaffolds as production activation.

Do not use this packet to authorize:

- Qlib, TRL, RL, W&B, or OpenClaw production activation
- paper/canary/live execution
- broker execution or capital binding
- direct registry writes
- direct governance writes

## Observed BFF Surface

Current worktree evidence shows an operator read route:

- BFF route: `GET /api/v1/operator/research/oss-preactivation`
- Query: `activity_limit`, integer, default `20`, min `1`, max `100`
- Auth: existing BFF read role via `Authorization`
- Aggregated services:
  - `research_orchestrator`
  - `policy_learning`
  - `research_worker_gateway`
  - `openclaw_gateway_adapter`
- Upstream service envs already recognized by the BFF read store:
  - `PANTHEON_RESEARCH_ORCHESTRATOR_API_URL`, `PANTHEON_RESEARCH_ORCHESTRATOR_URL`, `RESEARCH_ORCHESTRATOR_URL`
  - `PANTHEON_POLICY_LEARNING_API_URL`, `PANTHEON_POLICY_LEARNING_URL`, `POLICY_LEARNING_URL`
  - `PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL`, `PANTHEON_RESEARCH_WORKER_GATEWAY_URL`, `RESEARCH_WORKER_GATEWAY_URL`
  - `PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL`, `PANTHEON_OPENCLAW_ADAPTER_URL`, `OPENCLAW_GATEWAY_ADAPTER_URL`

Current operator-bff compose wiring points to the `PANTHEON_*` service URLs, while the read store also supports the legacy aliases listed above:

- `PANTHEON_POLICY_LEARNING_API_URL=http://policy-learning-svc:8100`
- `PANTHEON_RESEARCH_ORCHESTRATOR_API_URL=http://research-orchestrator-svc:8101`
- `PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL=http://research-worker-gateway-svc:8103`
- `PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL=http://openclaw-gateway-adapter:8104`

## Expected Response Shape

Frontend should treat the route as a bounded read model:

```json
{
  "data": {
    "surface": "research_oss_preactivation",
    "activation_state": "preactivation_only",
    "production_activation": "disabled",
    "activated": false,
    "allowed_scope": "capability_metadata_read_only",
    "write_paths": {
      "training_dispatch": "disabled",
      "paper_canary_live": "disabled",
      "registry_writes": "disabled",
      "governance_writes": "disabled",
      "broker_execution": "disabled",
      "capital_binding": "disabled"
    },
    "backend_inventory": [],
    "safe_dispatch": {},
    "activity": [],
    "rejection_verification": {},
    "service_status": {}
  },
  "meta": {
    "snapshot_at": "ISO-8601",
    "surfaces": {
      "research_oss_preactivation": {
        "status": "ok | degraded | unavailable",
        "source": "service_client"
      }
    }
  }
}
```

## Backend Inventory Rows

Expected `backend_inventory` members:

- `openclaw`
- `qlib`
- `trl`
- `finrl`
- `rllib`
- `ray_tune`
- `wandb`

Frontend display contract per backend:

- Show `activated=false` and `production_activation=disabled` as hard status, not warning copy.
- Show `gate_state=fail_closed` and `allowed_scope=capability_metadata_read_only` when service evidence exists.
- Show `service_count` and per-service entries so operators can identify whether evidence came from orchestrator, worker gateway, policy-learning, or OpenClaw adapter.
- If `gate_state` or `allowed_scope` is `unknown`, render it as missing evidence, not activation readiness.

OpenClaw-specific fields may appear under `backend_inventory[].services.openclaw_gateway_adapter`:

- `activation_state=facade_only`
- `broker_execution=deferred`
- `paper_adapter=deferred`
- `live_adapter=deferred`
- `capital_binding=deferred`
- `fail_closed=true`

## Activity Rows

Expected activity projection fields:

- `service`
- `object_type`
- `object_id`
- `backend`
- `status`
- `requested_mode`
- `dispatch_mode`
- `production_activation`
- `rejection_reason`
- `fail_closed_rejection`
- `updated_at`

Frontend behavior:

- Highlight `status=rejected` with `fail_closed_rejection=true` as successful guardrail evidence.
- Do not present rejected activation attempts as incidents unless another incident surface says so.
- Queue/running rows are safe only when `backend` is `stub`, `handoff_only`, or `manual`, and `production_activation=disabled`.
- Sort by `updated_at` descending when using the BFF order is sufficient; keep pagination local until the parent task defines service-side pagination.

## Operator Journey

The intended operator flow is:

1. Open the research OSS pre-activation panel from the operator surface.
2. Confirm the composite surface status:
   - `ok`: services are reachable and the read model is populated.
   - `degraded`: at least one service is unavailable; do not infer activation readiness.
   - `unavailable`: all service-backed surfaces are missing or unreachable.
3. Scan backend inventory for all seven dormant backends.
4. Confirm every backend remains `activated=false`, `production_activation=disabled`, and fail-closed where evidence exists.
5. Review `safe_dispatch` to see which services still allow stub/handoff-only/manual record paths.
6. Review activity for recent denied attempts and verify `all_observed_rejections_fail_closed=true` when rejections are present.
7. Use links to underlying research/workbench routes only for read-through or stub handoff work; do not expose activation commands from this surface.

## Frontend Components To Build

Recommended first-pass frontend decomposition:

- Summary strip: composite status, `activated=false`, `production_activation=disabled`, `allowed_scope`.
- Write-path guard grid: six disabled paths from `write_paths`.
- Backend matrix: one row per backend with gate state, allowed scope, service count, activation gate, and service evidence drawer.
- Safe dispatch panel: service to safe dispatcher chips.
- Activity table: service, backend, requested mode, dispatch mode, status, rejection reason, fail-closed result, timestamp.
- Service health drawer: raw `service_status` by service, including `activity_status`, `upstream_status`, and `upstream_reachable` when present.

UI copy should avoid "ready", "enabled", "active", or "can run" for dormant OSS backends. Prefer labels like "pre-activation only", "fail-closed", "metadata only", "deferred", and "guardrail observed".

## Query Gaps For Parent Owner

These are handoff gaps, not sidecar changes:

- The route exposes `activity_limit` only. If the activity feed grows, parent may want `service`, `backend`, `status`, and `since` filters.
- The activity model has no stable deep links to source service details yet. Frontend can render object IDs as text until parent defines links.
- `service_status` does not currently include elapsed latency or last successful fetch time. Degraded UX can still work with status/reason only.
- OpenClaw adapter contributes capability/upstream status, but not session activity. That is correct for fail-closed pre-activation; frontend should not invent session history.
- Missing services result in `unknown` backend evidence. Frontend must distinguish "unknown evidence" from "open gate".

## Suggested Acceptance Checks

For parent/reviewer intake:

- `GET /api/v1/operator/research/oss-preactivation` returns all seven dormant backends when service capabilities are reachable.
- Response always reports `activated=false` and `production_activation=disabled`.
- Write paths for paper/canary/live, registry, governance, broker execution, and capital binding remain disabled.
- Rejected service activity projects `fail_closed_rejection=true` for expected guardrail reasons.
- Missing upstream service URLs degrade the read surface without enabling activation.
- Frontend renders unavailable/unknown evidence conservatively and exposes no activation action.

## Evidence References

Scoped files inspected for this packet:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_research_oss_preactivation_contract.py`
- `services/research/main.py`
- `services/policy-learning/main.py`
- `services/research-worker-gateway/main.py`
- `services/openclaw-gateway-adapter/main.py`
- `docker-compose.yml`
- `RESEARCH_BACKEND_MATURITY_MATRIX.md`
- `OSS_INTEGRATION_CHECKLIST.md`
- `scripts/smoke_dormant_oss_matrix.py`
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/planning-session.json`

Focused verification for this sidecar packet:

```bash
python3 -m pytest services/control-plane/bff/test_research_oss_preactivation_contract.py
```

Result: passed locally, 2 tests.

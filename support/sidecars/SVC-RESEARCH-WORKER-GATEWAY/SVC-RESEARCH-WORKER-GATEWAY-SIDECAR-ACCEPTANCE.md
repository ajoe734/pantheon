# SVC-RESEARCH-WORKER-GATEWAY Acceptance Packet and Dependency Map

**Sidecar Task ID**: `SVC-RESEARCH-WORKER-GATEWAY-SIDECAR-ACCEPTANCE`
**Parent Task**: `SVC-RESEARCH-WORKER-GATEWAY`
**Parent Owner**: `Gemini`
**Parent Reviewer**: `Codex`
**Parent Status at Packet Time**: `todo`
**Parent Status at Last Revalidation**: `done` (archived; service materialized at `services/research-worker-gateway/` with `Dockerfile`, `main.py`, `store.py`, and the three focused test files listed in Section 8)
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Claude` (auto-reassigned 2026-04-29 after repeated Copilot quota terminals; full review chain: Claude -> Claude2 -> Codex2 -> Copilot -> Codex2 -> Copilot -> Claude)
**Helper Kind**: `acceptance_packet`
**Date**: 2026-04-28 (revalidated for reviewer reassignment 2026-04-29)

> This is a support artifact only. It does not modify L1 canonical truth,
> service contract truth, adapter activation policy, registry truth,
> governance implementation, runtime implementation, or compose wiring. The
> parent owner decides whether and how to absorb this packet into the main
> `SVC-RESEARCH-WORKER-GATEWAY` implementation.

---

## 1. Scope Snapshot

`SVC-RESEARCH-WORKER-GATEWAY` should materialize a bounded
`research-worker-gateway` service that fronts repo-local research worker
entrypoints with guarded dispatch, replayable status, cancellation records, and
capability discovery.

At original packet time, the parent task was not implemented and
`ai-status.json` listed the parent as `todo`, owned by Gemini and reviewed by
Codex. At last revalidation, the parent had been archived as `done`; this
packet remains support evidence and does not become implementation proof by
itself. The expected parent artifact paths were:

| Artifact | Expected role |
|---|---|
| `services/research-worker-gateway/` | New gateway service, storage, HTTP API, Dockerfile, and tests. |
| `services/research/` | Existing research adapters/workers and orchestrator service consumed by the gateway. |
| `docker-compose.yml` | Service, port, healthcheck, storage volume, env contract, and smoke-stack wiring. |

The gateway is a support/runtime wrapper around already-governed research
workers. It is not an approval to activate production learning, paper/canary
execution, EP5, LEAN writes, registry promotion, or governance stage changes.

---

## 2. Parent Acceptance Checklist

| Parent acceptance item | Concrete checks for owner/reviewer | Expected disposition |
|---|---|---|
| `research-worker-gateway` exposes capability list, dispatch, status, cancel, and health APIs. | Verify routes equivalent to `GET /health`, `GET /api/research-worker-gateway/capabilities`, `POST /api/research-worker-gateway/jobs`, `GET /api/research-worker-gateway/jobs`, `GET /api/research-worker-gateway/jobs/{job_id}`, `GET /api/research-worker-gateway/jobs/{job_id}/status`, and `POST /api/research-worker-gateway/jobs/{job_id}/cancel`. | Required. |
| Dockerfile, env storage, and compose wiring are added. | Verify a dedicated Dockerfile, durable data directory env, bounded active-job env, production adapter gate env defaulting false, a named data volume, stable container port, host port override, and healthcheck. | Required. |
| Gateway rejects unapproved production learning or live execution adapters by default. | Verify requests for `production`, `paper`, `canary`, `live`, `ep5`, LEAN, SignalStore, direct registry write, or unapproved real backends are recorded as rejected and never executed. | Required. |
| Tests cover dispatch idempotency, rejection policy, status replay, and compose config. | Verify focused tests cover idempotent create/dispatch replay, unsupported adapter/mode rejection, active-job bound, cancel transitions, status persistence across service reload, capabilities surface, and compose wiring. | Required. |
| Sidecar support scope is preserved. | This packet adds only `support/sidecars/SVC-RESEARCH-WORKER-GATEWAY/SVC-RESEARCH-WORKER-GATEWAY-SIDECAR-ACCEPTANCE.md`; any status handoff uses normal L0 coordination state. | Satisfied by this sidecar. |

---

## 3. Proposed Gateway API Surface

| Route | Purpose | Acceptance notes |
|---|---|---|
| `GET /health` | Return service name, data dir, job counts, active-job count, max active jobs, production-adapter flag, and dependency health projection. | Health only; no execution authority. |
| `GET /api/research-worker-gateway/capabilities` | Return allowed worker families, default dispatch mode, disabled production families, required gates, and per-worker input hints. | Must make disabled/guarded state visible. |
| `POST /api/research-worker-gateway/jobs` | Create/dispatch a bounded worker job from a worker family, mode, input refs, parameters, actor, and idempotency key. | Must be idempotent by key and reject unsafe modes before any process/container spawn. |
| `GET /api/research-worker-gateway/jobs` | List jobs with optional `worker`, `status`, `task_id`, or `run_id` filters. | Replayable read over durable store. |
| `GET /api/research-worker-gateway/jobs/{job_id}` | Return one persisted job record. | 404 for unknown job. |
| `GET /api/research-worker-gateway/jobs/{job_id}/status` | Return concise status, rejection/cancel reason, timestamps, output refs, exit code, and event sequence. | Used by orchestration and smoke consumers. |
| `POST /api/research-worker-gateway/jobs/{job_id}/cancel` | Mark a queued/running job as cancellation requested or canceled; terminal jobs remain immutable. | Must be idempotent and append an event. |

Suggested statuses:

| Status | Meaning |
|---|---|
| `queued` | Accepted within bounds; not yet run or represented by a stub dispatch. |
| `running` | Safe stub/local worker has started. |
| `completed` | Worker finished and emitted output refs. |
| `failed` | Worker failed inside allowed boundary. |
| `cancel_requested` | Cancellation accepted for an active job. |
| `canceled` | Job canceled before completion. |
| `rejected` | Request violated adapter, mode, gate, or safety policy; no execution occurred. |

---

## 4. Capability Registry Boundary

The gateway should expose a local worker capability registry, but that registry
must be operational metadata for dispatch only. It must not become canonical
registry truth.

| Worker family | Existing repo surface | Default gateway disposition |
|---|---|---|
| `stub` / `handoff_only` / `manual` | `services/research/main.py` already accepts stub/handoff-only orchestration modes. | Available for safe dispatch/status replay. |
| `qlib` | `services/research/qlib/worker.py`, `services/research/qlib/adapter/qlib_adapter.py`, `services/research/qlib/requirements.txt`. | Disabled for production by default; only explicitly safe stub/local smoke mode may be exposed. |
| `finrl` | `services/research/finrl/worker.py`, `services/research/finrl/adapter/finrl_adapter.py`, `services/research/finrl/README.md`. | Deferred-prep only; requires explicit non-default prep gate and remains non-production. |
| `rllib` | `services/research/rllib/worker.py`, `services/research/rllib/adapter/rllib_adapter.py`, `services/research/rllib/README.md`. | Deferred-prep only; requires explicit non-default prep gate and remains non-production. |
| `ray_tune` | `services/research/rllib/ray_tune_worker.py`, `services/research/rllib/adapter/ray_tune_adapter.py`. | Deferred-prep/search artifact only; requires explicit non-default prep gate and remains non-production. |
| `vectorbt` | `services/research/vectorbt/worker.py`, `services/research/vectorbt/ACTIVATION_CRITERIA.md`. | Research-plane backtest artifact only; real backend remains env-gated. |
| `statsmodels` | `services/research/statsmodels/worker.py`, `services/research/statsmodels/ACTIVATION_CRITERIA.md`. | Research-plane analytics artifact only; real backend remains env-gated. |
| `quantlib` | `services/research/quantlib/worker.py`, `services/research/quantlib/ACTIVATION_CRITERIA.md`. | Research-plane pricing/risk artifact only; real backend remains env-gated. |

Implementation guidance:

| Concern | Required behavior |
|---|---|
| Local capability registry | Store static worker metadata in gateway code/config or JSON under `services/research-worker-gateway/`; do not write to canonical registry. |
| Real backend flags | Preserve each worker's existing explicit env gate, such as `PANTHEON_FINRL_PREP_ENABLED`, `PANTHEON_RLLIB_PREP_ENABLED`, `PANTHEON_RAYTUNE_PREP_ENABLED`, `PANTHEON_VECTORBT_BACKEND=real`, `PANTHEON_STATSMODELS_BACKEND=real`, and `PANTHEON_QUANTLIB_BACKEND=real`. |
| Default backend | Default to stub/handoff-only or rejection. Never infer approval from package availability. |
| Output envelope | Persist output refs and summary metadata only. Registry promotion remains a downstream handoff, not a gateway write. |

---

## 5. Rejection Policy

The gateway should reject unsafe work before any worker execution path runs.

| Request condition | Expected outcome |
|---|---|
| `requested_mode` in `production`, `paper`, `canary`, or `live` while production gate is false. | `status=rejected`, reason `production_adapter_disabled`. |
| Adapter requests direct LEAN, SignalStore, execution-plane, or live trading influence. | `status=rejected`, reason `execution_path_disabled`. |
| Adapter requests direct registry write or governance stage transition. | `status=rejected`, reason `registry_write_disabled` or `governance_write_disabled`. |
| RL/learning request attempts EP5 activation or production training. | `status=rejected`, reason `learning_activation_disabled`. |
| Worker family is unknown or not listed by capabilities. | `status=rejected`, reason `unknown_worker`. |
| Dispatch mode is not one of explicitly allowed safe modes. | `status=rejected`, reason `dispatch_mode_disabled`. |
| Active safe jobs exceed the configured max. | HTTP `429` or rejected/queued-denied record with reason `active_job_bound_exceeded`; no worker starts. |

Rejected jobs should be persisted and replayable so operators can audit why a
dispatch did not run.

---

## 6. Storage, Compose, and Smoke Map

Suggested names follow the already-materialized `research-orchestrator-svc`
pattern.

| Concern | Suggested contract |
|---|---|
| Service name | `research-worker-gateway-svc` |
| Container port | `8103` unless the parent owner chooses another unused service-layer port. |
| Host port env | `${RESEARCH_WORKER_GATEWAY_PORT:-18103}:8103` |
| Data dir env | `RESEARCH_WORKER_GATEWAY_DATA_DIR=/data/research-worker-gateway` |
| Active bound env | `RESEARCH_WORKER_GATEWAY_MAX_ACTIVE_JOBS=${RESEARCH_WORKER_GATEWAY_MAX_ACTIVE_JOBS:-4}` |
| Production gate env | `RESEARCH_WORKER_GATEWAY_ENABLE_PRODUCTION_ADAPTERS=false` |
| Durable volume | `research-worker-gateway-data:/data/research-worker-gateway` |
| Healthcheck | `GET /health` using the service `PORT` env. |
| Smoke env | `RESEARCH_WORKER_GATEWAY_URL=http://research-worker-gateway-svc:8103` |
| Upstream dependency | `RESEARCH_ORCHESTRATOR_URL=http://research-orchestrator-svc:8101` if the gateway links jobs to orchestrator task/run refs. |

Suggested durable records under the data dir:

| File | Purpose |
|---|---|
| `worker_jobs.json` | Durable job records keyed by `job_id`. |
| `worker_events.jsonl` | Append-only event sequence for queued, rejected, running, completed, failed, cancel, and replay events. |
| `worker_outputs.json` | Optional output refs emitted by allowed stub/local worker paths. |

Smoke path should prove:

1. `GET /health` and `GET /capabilities` return production disabled.
2. A safe stub/handoff job can be created with an idempotency key.
3. Replaying the same key returns the original job.
4. `GET /status` returns a stable event sequence.
5. Cancel on queued/running job records a cancel event.
6. Production or live adapter request is rejected and persisted.
7. `docker compose config --quiet` passes with the service and smoke dependency.

---

## 7. Dependency Map

### Direct prerequisite

| Dependency | Status | Why it matters |
|---|---|---|
| `SVC-RESEARCH-ORCHESTRATOR-SERVICE` | `done`, archived 2026-04-28T19:18:50Z | Provides the bounded research task/run lifecycle, artifact/proposal handoff, production-adapter rejection pattern, Docker/compose pattern, and status surface that the worker gateway should integrate with or mirror. |

### Adjacent service materialization

| Task/service | Relationship |
|---|---|
| `research-orchestrator-svc` | Upstream task/run owner. Worker gateway jobs should reference orchestrator task/run IDs when dispatching work for an orchestrated run. |
| `SVC-POLICY-LEARNING-BOUNDARY` | Defines/guards learning activation boundaries. Worker gateway must not weaken those gates. |
| `SVC-TRAINING-SESSION-SERVICE` | Sibling training-session wrapper. Gateway should not silently become a training session write owner. |
| `SVC-COMPOSE` | Existing single-VM compose service patterns should be followed for port/env/volume/health/smoke wiring. |
| `SVC-SURFACES` / future BFF surfaces | Downstream consumers may read job status through service clients after gateway materialization. |

### Existing research worker inventory

| Path | Gateway relevance |
|---|---|
| `services/research/qlib/worker.py` | Candidate worker entrypoint; keep production disabled by default. |
| `services/research/finrl/worker.py` | Deferred-prep candidate; requires explicit prep env. |
| `services/research/rllib/worker.py` | Deferred-prep candidate; requires explicit prep env. |
| `services/research/rllib/ray_tune_worker.py` | Deferred-prep/search candidate; requires explicit prep env. |
| `services/research/vectorbt/worker.py` | Research backtest candidate; real backend env-gated. |
| `services/research/statsmodels/worker.py` | Research analytics candidate; real backend env-gated. |
| `services/research/quantlib/worker.py` | Research pricing/risk candidate; real backend env-gated. |

---

## 8. Focused Test Matrix

| Test file | Required coverage |
|---|---|
| `services/research-worker-gateway/tests/test_research_worker_gateway_http_service.py` | Health, capabilities, idempotent job creation, safe dispatch, status replay, cancel, not-found behavior. |
| `services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py` | Production/paper/canary/live rejection, EP5/live execution rejection, registry/governance write rejection, unknown worker rejection, active-job bound. |
| `services/research-worker-gateway/tests/test_research_worker_gateway_compose_activation.py` | Dockerfile path, env contract, volume, ports, healthcheck, smoke-stack URL and `depends_on`. |
| Optional smoke extension | Add a `scripts/smoke_honest_stack.py` step that creates a safe gateway job, reads status, cancels/replays as appropriate, and verifies unsafe adapter rejection. |

Minimum verification commands for parent review:

```bash
pytest services/research-worker-gateway/tests/test_research_worker_gateway_http_service.py \
  services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py \
  services/research-worker-gateway/tests/test_research_worker_gateway_compose_activation.py
docker compose config --quiet
```

If the parent owner chooses a different test layout, the same behavioral
coverage should still be visible in review.

---

## 9. Boundary and Non-Activation Notes

| Concern | Required disposition |
|---|---|
| Qlib, FinRL, RLlib, Ray Tune, vectorbt, statsmodels, or QuantLib production execution | Deferred unless a separate approved activation task enables it. |
| EP5 / production learning | Not activated by this gateway. Unsafe requests are rejected and persisted. |
| Registry canonical truth | Gateway may emit refs/projections only; registry writes stay outside this service. |
| Governance approval and deployment stages | Gateway must not approve, promote, or change deployment stage. |
| LEAN/live trading influence | Disabled. Outputs must remain research-plane artifacts or status records. |
| Source ingest/search execution | Owned by their services; gateway may consume refs, not bypass their services. |

---

## 10. Reviewer Checklist for Claude

| Check | Expected answer |
|---|---|
| Did this sidecar avoid L1 canonical truth, contract truth, registry truth, governance, runtime implementation, and compose edits? | Yes. It only adds this support packet; status handoff uses normal L0 coordination. |
| Does the packet reflect the parent task's state history? | Yes. It distinguishes the original `todo` packet-time state from the last-revalidated archived `done` parent state, so the sidecar remains support evidence rather than implementation evidence. |
| Is the dependency map complete for the parent task's declared dependency? | Yes. `SVC-RESEARCH-ORCHESTRATOR-SERVICE` is mapped as the direct satisfied prerequisite. |
| Does the packet preserve non-activation boundaries for production learning/live execution? | Yes. Unsafe adapter, mode, EP5, registry, governance, and live execution paths are explicitly rejected. |
| Is there a concrete acceptance/test packet Gemini can use? | Yes. Sections 2, 3, 6, and 8 define the parent review checklist and minimum verification surface. |

---

## 11. Handoff

**To**: `Codex` (owner — for finalization to done)
**From**: `Claude` (final reviewer)
**Review outcome**: APPROVED (2026-04-29)

Reviewer reassignment history (2026-04-29): review chain was Claude → Claude2
→ Codex2 → Copilot → Codex2 → Copilot → Claude. Sidecar scope, contents, and
the support-only boundary have not changed across reassignments.

Claude review verification (2026-04-29):
- All 7 required API routes (`/health`, `/capabilities`, `/jobs`, `/jobs/{id}`,
  `/jobs/{id}/status`, `/jobs/{id}/cancel`) confirmed present in
  `services/research-worker-gateway/main.py`.
- `docker-compose.yml` has `research-worker-gateway-svc` with `RESEARCH_WORKER_GATEWAY_DATA_DIR`,
  `research-worker-gateway-data` volume, port wiring, and smoke-stack URL env.
- All 3 focused test files from Section 8 are present.
- Rejection functions (`_rejection_for`, `_production_adapters_allowed`) exist in `main.py`.
- `docker compose config --quiet` passes (confirmed per prior owner verification 2026-04-29).
- Sidecar artifact is limited to this single file under
  `support/sidecars/SVC-RESEARCH-WORKER-GATEWAY/`; no L1 canonical truth,
  contract truth, runtime, registry, governance, or compose implementation was
  changed.

Post-packet parent state note: the parent `SVC-RESEARCH-WORKER-GATEWAY` task
has since been archived as `done`. The materialized service at
`services/research-worker-gateway/` (Dockerfile, `main.py`, `store.py`, and
the three focused test files in Section 8) tracks this packet's proposed
HTTP surface, storage layout, rejection policy, and compose/smoke map. This
sidecar continues to read as forward-looking acceptance support; nothing in
this packet was promoted into canonical truth.

Recommended parent-owner use:

1. Use Sections 2 and 8 as the parent implementation/review checklist.
2. Use Sections 3 and 6 as the proposed HTTP, storage, compose, and smoke map.
3. Keep Sections 5 and 9 intact when implementing the gateway: the task is a
   bounded dispatch wrapper, not production learning activation, EP5 activation,
   registry promotion, governance write ownership, or live execution.

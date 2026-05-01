# Research OSS Production Data Posture And Activation Notes

Status: task evidence for `P2-OSS-ACTIVATE-001`
Date: 2026-05-01
Owner: Codex2
Reviewer: Codex

## Scope

These notes define the current activation posture for research OSS components
that depend on production data, source ingestion, governed search, or OpenClaw
handoff paths.

This is not a blanket live-data ban. Production research data may be ingested
and used when durable storage, entitlement, license/PIT, rate-limit, freshness,
and audit posture are complete. What remains closed is any direct path from OSS,
external source feeds, OpenClaw tools, or search results into order-capable
execution, broker sessions, paper/canary/live deployment, position changes, or
capital movement.

## Control Surface Read

| Control | Current enforcement | Activation consequence |
|---|---|---|
| Source records | `services/source_ingestion/external_sources.py` requires entitlement, license scope, access scope, event time, available time, PIT validation, content hash, and a `SourceRecord/EvidenceBundle` governance sink for news/social/alpha DB records. It rejects direct Lean, broker, order-routing, runtime, and execution metadata routes. | External production feeds are allowed only as governed source evidence. Raw feed injection is not an activation path. |
| Evidence bundles | `EvidenceBundleBuilder` and the search gateway require result objects to carry `evidence_bundle_id` and citation refs before surfacing governed search results. | OSS consumers receive evidence refs, not untraced blobs. |
| Search ACL/license | `services/search/gateway.py` applies `SearchAccessContext.permits()` before ranking and enforces environment, access scope, license scope, persona scope, workspace scope, citations, and `available_time <= now`. | OpenClaw/research retrieval cannot bypass ACL/license/PIT freshness by querying the search gateway. |
| OpenClaw search facade | `integrations/openclaw/search_gateway.py` requires persona and workspace context and returns only `evidence_bundle_id`, citation pack, relevance score, rejected count, and filters. | OpenClaw gets sanitized evidence references only; no raw payload, no answer-context blob, and no execution handoff. |
| Tool/workflow bridge | `services/openclaw-gateway-adapter/tool_workflow_bridge.py` is deny-first and always blocks broker, live, paper, canary, Lean, and capital-prefixed tools/workflows even if allowlisted. | OpenClaw tool activation cannot become order-capable execution. |
| Broker adapters | `services/openclaw-gateway-adapter/paper_broker_adapter.py` is disabled unless `OPENCLAW_PAPER_ADAPTER_ENABLED=true`; live order submission is always rejected. `live_gate_adapter.py` supports validation and dry handoff only, with all gates closed by default. | Research OSS activation does not enable broker orders or real-capital side effects. |
| Source/search production posture | `PANTHEON_SOURCE_SEARCH_POSTURE=production` requires Postgres evidence/index backends, durable-index-only search, and object-store env before readiness. | Production source/search activation is a durability/audit gate, not a live-execution gate. |

## Component Posture

| Component | Current status | Explicit gate | Production activation read |
|---|---|---|---|
| Qlib | `smoke-tested`; activation-ready offline handoff only | Worker requires `PANTHEON_QLIB_ACTIVATION_READY_ENABLED=1` and explicit `QLIB_BACKEND=stub|real`; gateway dispatch requires `PANTHEON_OFFLINE_GATE_ENABLED=true` | Still blocked on a named RS-003 candidate artifact, governed >=50 instrument / >=2 year OHLCV dataset proof, target StrategySpec binding, and production credential/storage evidence for the selected data provider. Output remains `artifact_state=draft`, `deployment_summary.current_stage=none`, with a non-writing candidate packet. |
| TRL | `smoke-tested`; follow-up review-approved | Worker requires `PANTHEON_TRL_ACTIVATION_READY_ENABLED=1`; activation evidence harness requires `--enable-activation-ready` | `P2-TRL-RUNTIME-DATA-ACTIVATION-001` produced bounded FB-002 evidence (240 events/pairs, 3 strategy families, all approve/edit/reject actions), evaluator/registry/candidate handoff packets, and explicit real-backend dependency/config evidence (`No module named 'trl'`, `silent_stub_fallback=false`). No direct governance write or order route opens. |
| FinRL | `criteria-defined`; deferred prep only | Worker requires `PANTHEON_FINRL_PREP_ENABLED=1`; smoke requires `--enable-deferred-prep` | Production RL activation remains closed until the RL path approval gate reopens after Qlib supervised alpha proof. No paper/canary/live, registry write, or broker route is open. |
| RLlib / Ray Tune | `version-pinned`; deferred prep only | Workers require `PANTHEON_RLLIB_PREP_ENABLED=1` or `PANTHEON_RAYTUNE_PREP_ENABLED=1`; smokes require `--enable-deferred-prep` | Same RL gate as FinRL. Prep outputs are draft/none only and cannot dispatch production train/eval loops. |
| W&B | `criteria-defined`; offline local-store only | `EXPERIMENT_BACKEND=wandb` requires `PANTHEON_ENABLE_WANDB_OFFLINE_STORE=1` or legacy deferred-prep flag | SDK-backed/networked W&B activation remains blocked on the MLflow operational-history gate, operator preference, SDK pin, network readiness, and re-entry approval. |
| OpenClaw | `governed`; search/session/tool bridge active under policy | Runtime profile and adapter env are opt-in; broker paper path requires `OPENCLAW_PAPER_ADAPTER_ENABLED=true`; live order path is hard rejected | OpenClaw can request governed research context and session/tool metadata, but cannot bypass SearchGateway controls or open broker/capital paths. |

## Remaining Production Prerequisites

Before any research OSS component can consume production external data, the
owner must attach an evidence packet with:

1. production credential reference IDs only, never raw secrets
2. entitlement tags and entitlement refs for the vendor/data scope
3. license policy and allowed-use terms that exclude direct execution and order routing
4. PIT fields: event time, available time, source watermark, and freshness SLA
5. durable storage proof: Postgres source/evidence/search stores plus object store
6. rate-limit policy and bounded fetch configuration
7. audit path for ingest, normalization, index refresh, search query, and replay
8. downstream consumer proof showing the artifact remains research/evaluation scoped

For Qlib, the first concrete production data packet must also name the RS-003
candidate, target StrategySpec, vendor/dataset manifest, universe size, history
window, and label definition.

For TRL, the first concrete runtime-data packet must also include the FB-002
event snapshot, preference-pair construction summary, approved imitation
artifact refs, baseline-model metrics, and selected downstream consumer.
The bounded task packet for `P2-TRL-RUNTIME-DATA-ACTIVATION-001` now lives in
`support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/`; production FB-002 store
volume and production registry refs remain separate runtime gates.

## Disposition

`OSS_INTEGRATION_CHECKLIST.md` should keep Qlib and TRL at `smoke-tested`.
Their repo-local adapters are runnable and guarded, but the production evidence
bundles are not present. Source/search production posture is activation-ready
only for governed read-only ingestion and retrieval once durable storage and
object-store dependencies are configured. No OSS path is authorized to route
directly to broker, Lean, paper/canary/live deployment, capital binding, or
order-capable execution.

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
| Qlib | `smoke-tested`; follow-up active | Worker requires `PANTHEON_QLIB_ACTIVATION_READY_ENABLED=1` and explicit `QLIB_BACKEND=stub|real`; gateway dispatch requires `PANTHEON_OFFLINE_GATE_ENABLED=true` | `P2-QLIB-PROD-DATA-ACTIVATION-001` now owns the production-data proof and real/stub-selectable backend smoke. Output must remain research/model artifact handoff only with no order-capable route. |
| TRL | `smoke-tested`; follow-up review-approved | Worker requires `PANTHEON_TRL_ACTIVATION_READY_ENABLED=1`; activation evidence harness requires `--enable-activation-ready` | `P2-TRL-RUNTIME-DATA-ACTIVATION-001` produced bounded FB-002 evidence (240 events/pairs, 3 strategy families, all approve/edit/reject actions), evaluator/registry/candidate handoff packets, and explicit real-backend dependency/config evidence (`No module named 'trl'`, `silent_stub_fallback=false`). No direct governance write or order route opens. |
| FinRL | `smoke-tested`; task done | Worker requires `PANTHEON_FINRL_PREP_ENABLED=1`; activation smoke uses `--enable-activation-ready --backend real`; explicit `ModuleNotFoundError` recorded with `silent_stub_fallback=false` | `P2-RL-UPSTREAM-RUNTIME-SMOKE-001` done — bounded governed runtime smoke completed; checksum-bearing artifact bundle, evaluator packet, registry entry, and candidate packet in `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/`; no paper/canary/live, registry write, broker route, or capital binding. |
| RLlib / Ray Tune | `smoke-tested`; task done | Workers require `PANTHEON_RLLIB_PREP_ENABLED=1` or `PANTHEON_RAYTUNE_PREP_ENABLED=1`; activation smokes use `--enable-activation-ready --backend real`; explicit `ModuleNotFoundError` recorded with `silent_stub_fallback=false` | `P2-RL-UPSTREAM-RUNTIME-SMOKE-001` done — bounded train/search runtime smoke completed as research-only artifact output; evidence in `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/`. |
| W&B | `criteria-defined`; online sync follow-up active | Offline store uses `PANTHEON_ENABLE_WANDB_OFFLINE_STORE=1`; online sync must use explicit test project/API-key env gates | `P2-WANDB-ONLINE-SYNC-001` now owns SDK pinning, online metrics/artifact upload, and readback smoke. Broker/order/capital paths remain out of scope. |
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

`OSS_INTEGRATION_CHECKLIST.md` should keep Qlib and TRL at `smoke-tested` until
their active follow-ups produce the required production-data/runtime-data
evidence. W&B, FinRL, RLlib, and Ray Tune are no longer blocked by a blanket
live/production ban; their follow-ups should complete bounded runtime or online
sync smoke where safe. Source/search production posture is activation-ready only
for governed read-only ingestion and retrieval once durable storage and object
store dependencies are configured. No OSS path is authorized to route directly
to broker, Lean, paper/canary/live deployment, capital binding, or order-capable
execution.

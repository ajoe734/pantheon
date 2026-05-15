# External Data Integration Materialization Audit

Last updated: 2026-05-02.

Scope: mainline external-data, source/search, broker/venue, and OSS/upstream
integration tasks. Sidecar, review-only, handoff-only, and acceptance-only tasks
are excluded.

Legend:

- `Done`: code, docs, or intended evidence landed for the task scope.
- `Evidence/gate`: the task was intentionally a decision, packet, preflight, or
  gate, not a runnable integration.
- `Repo proof`: unit tests, smoke scripts, compose checks, static checks, or
  closeout evidence recorded in the task.
- `External proof`: credentialed provider/runtime smoke evidence. `Not recorded`
  means the task archive does not prove a credentialed external run.
- `Pending`: active follow-up owns the missing proof.

## Current Active Gap Tasks

| Task | Status | Owner | What it must close |
|---|---|---|---|
| P2-BROKER-SANDBOX-ORDER-001 | review_approved | Codex2 | Broker paper/sandbox/test-key order API place/cancel/readback/reconcile smoke. This is the only area where production real-capital side effects stay hard fail-closed. |
| P2-MARKETDATA-CREDENTIAL-SMOKE-001 | todo | Codex2 | Credentialed read-only market-data smoke for Massive/Polygon, TWSE/TPEx/MOPS/TEJ, CoinGecko/Kraken market data, and IBKR/Shioaji quote/read-only lanes. |
| P2-WANDB-ONLINE-SYNC-001 | todo | Codex | W&B SDK-backed online sync with test project/API-key metrics/artifact upload and readback. |
| P2-QLIB-PROD-DATA-ACTIVATION-001 | todo | Codex2 | Qlib governed production-data proof and real/stub-selectable backend smoke. |
| P2-TRL-RUNTIME-DATA-ACTIVATION-001 | todo | Codex2 | TRL FB-002 runtime preference-pair activation and real DPO smoke or explicit dependency/config evidence. |
| P2-RL-UPSTREAM-RUNTIME-SMOKE-001 | todo | Codex | FinRL, RLlib, and Ray Tune bounded governed train/search runtime smoke as research-only artifact output. |

## Market Data, Broker, And Venue

| Task | Developed/materialized? | Repo proof | External proof | Gap/next |
|---|---|---|---|---|
| APP-003-DATASOURCE-US-001 | Done: IBKR execution boundary and Massive/Polygon US data helpers landed. | Targeted IBKR adapter tests, data-plane schema suite, and data-plane smoke recorded. | Not recorded as credentialed provider smoke. | Read-only provider smoke is now P2-MARKETDATA-CREDENTIAL-SMOKE-001; broker order smoke is P2-BROKER-SANDBOX-ORDER-001. |
| APP-003-DATASOURCE-US-002 | Done: Massive/Polygon promoted as primary US research market-data path and IBKR market-data boundary tightened. | Focused IBKR adapter and data-plane schema tests re-ran cleanly. | Not recorded as credentialed Massive/Polygon or IBKR quote smoke. | P2-MARKETDATA-CREDENTIAL-SMOKE-001. |
| APP-003-DATASOURCE-TW-001 | Done: Shioaji, TWSE, TPEx, MOPS, and TEJ integration/boundaries landed or contract-complete. | Targeted execution, data-plane, and research verification recorded. | Not recorded as credentialed provider smoke for every TW provider. | P2-MARKETDATA-CREDENTIAL-SMOKE-001; Shioaji order smoke remains P2-BROKER-SANDBOX-ORDER-001. |
| APP-003-DATASOURCE-TW-002 | Done: Taiwan normalization pipeline and replay-clean joins landed. | Reviewer-verified canonical market-segment normalization and replay-clean join checks. | N/A for credential smoke by itself; it normalizes provider outputs. | Provider read smoke is covered by P2-MARKETDATA-CREDENTIAL-SMOKE-001. |
| APP-003-DATASOURCE-CRYPTO-001 | Done: Kraken execution/market-data and CoinGecko reference path landed. | Kraken/research/data-plane unit tests and 47/47 data-plane smoke recorded. | Not recorded as credentialed Kraken/CoinGecko production smoke. | P2-MARKETDATA-CREDENTIAL-SMOKE-001; Kraken order smoke remains P2-BROKER-SANDBOX-ORDER-001. |
| APP-003-DATASOURCE-CRYPTO-002 | Done: Kraken WebSocket realtime and execution-sync integration landed. | Closeout says WebSocket execution-sync task approved and closed. | Not recorded as credentialed WebSocket runtime smoke. | P2-MARKETDATA-CREDENTIAL-SMOKE-001. |
| APP-003-DATASOURCE-OPS-001 | Done: governed provider secrets/env/runbook/smoke automation landed. | EP5 readiness tests, canary datasource smoke, operator checklist, and prod-example smoke are recorded in review evidence. | Env/example/operator smoke only; actual credentialed provider smoke is not proven for every provider. | P2-MARKETDATA-CREDENTIAL-SMOKE-001. |
| EP5-001 | Evidence/gate: canary-ready entry bundle and rollback/operator harness prepared. | Runnable dry-run checklist/plan/rollback harness recorded. | No live broker order proof claimed. | P2-BROKER-SANDBOX-ORDER-001. |
| EP5-002-PACKET-PREP-001 | Evidence/gate: EP5 proof packet prepared. | Packet validator boundary approved. | No live broker order authorized. | P2-BROKER-SANDBOX-ORDER-001. |
| P2-LIVE-KERNEL-001 | Evidence/gate: LEAN launcher and broker SDK readiness plan corrected. | `git diff --check`, acceptance scans, and secret scan recorded. | Broker sandbox/test-key order smoke explicitly not completed here. | P2-BROKER-SANDBOX-ORDER-001. |

## Source And Search Ingestion

| Task | Developed/materialized? | Repo proof | External proof | Gap/next |
|---|---|---|---|---|
| SVC-SERVICE-DISPOSITION | Evidence/gate: service disposition classified. | Focused tests and compose disposition review recorded. | N/A. | No runtime connector proof; later tasks own implementation. |
| SVC-SOURCE-INGEST-SERVICE | Done: source-ingest HTTP/job service, Dockerfile, health, storage/env, compose/smoke wiring. | 11 focused tests and compose config recorded. | Local/compose smoke only. | P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001. |
| SVC-SEARCH-SERVICE | Done: governed search HTTP service, Dockerfile, health, BFF client, compose/smoke wiring. | Focused pytest and compose config recorded. | Local/compose smoke only. | P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001. |
| SVC-SOURCE-INGEST-AUTONOMOUS-PIPELINE | Done: configured connector fetch jobs, durable refs, watermarks, audit, DLQ replay. | 17 focused tests, py_compile, and compose config recorded. | Not recorded as credentialed external connector smoke. | P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001. |
| SVC-SEARCH-AUTONOMOUS-INDEX-PIPELINE | Done: durable server-side index and no-caller-document query path. | Search/source/BFF focused tests and compile check recorded. | N/A for external credentials; depends on ingested records. | P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001 for end-to-end live connector proof. |
| SVC-SOURCE-INGEST-EXTERNAL-FETCH-BASELINE | Done: bounded HTTP/file external feed mode with allowlist, timeout, byte/record caps, DLQ replay. | 17 source-ingest tests, compose activation, py_compile, diff check, compose config recorded. | Bounded external mode tested, but no credentialed live provider proof recorded. | P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001. |
| SVC-SOURCE-SEARCH-AUTONOMOUS-PIPELINE-SMOKE | Done: end-to-end source-to-search autonomous smoke. | 21 focused tests and 34 broader source/search tests recorded. | Local/bounded smoke only. | P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001. |
| SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT | Done: optional Postgres source/search stores with JSONL default preserved. | 16 Postgres pilot tests, 28 JSONL/default tests, compose config recorded. | No production DB credential proof; optional store path only. | Production stack proof belongs to source/search live connector posture tasking. |
| SVC-SOURCE-SEARCH-AUTONOMOUS-CONNECTOR-INDEXER | Done: scheduled bounded connector execution and materialized index refresh. | 64 source/search tests and compose config recorded. | Not recorded as credentialed connector smoke. | P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001. |
| SVC-SOURCE-CONNECTOR-FRAMEWORK | Done: connector contract with auth, secrets, rate limits, license, metadata, registry/BFF status. | py_compile, 37 source-ingestion tests, and BFF client tests recorded. | Framework/provider example only; no live credential proof. | P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001. |
| SVC-SOURCE-EVIDENCE-NORMALIZATION | Done: SourceRecord/EvidenceItem/KnowledgeObject normalization and ownership refs. | 54 focused evidence/source/consultation tests recorded. | N/A for external credentials. | Covered by live connector follow-up for runtime path. |
| SVC-SOURCE-CRAWL-FRONTIER-SCHEDULER | Done: persistent crawl frontier, scheduler, bounded worker, DLQ/frontier replay. | 43 source-ingestion tests, compileall, compose config recorded. | No unrestricted/live crawler credential proof; bounded scheduler only. | P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001. |
| SVC-SEARCH-INDEXING-PIPELINE | Done: incremental indexing, retained snapshots, freshness API, source-ingest notification. | 44 search/index tests, 26 source-ingestion tests, py_compile, compose config recorded. | N/A for provider credentials. | P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001 for external end-to-end proof. |
| SVC-SOURCE-SEARCH-OPS-BFF | Done: source/search operator BFF endpoints and idempotent commands. | 23 BFF tests and AST parse checks recorded. | N/A for provider credentials. | Live connector task must prove BFF readback. |
| SVC-SEARCH-RETRIEVAL-AND-CUTOFF | Done: durable-index-only normal path, request-document compat quarantine, ranking/filter/citation contract. | 40 focused cutoff tests and 78 search tests recorded. | N/A for provider credentials. | Live connector task must prove durable readback with external evidence. |
| SVC-SOURCE-SEARCH-PROD-HARDENING | Done: production posture, Postgres/object-store enforcement, health/metrics, idempotency, smoke script. | 72 tests plus py_compile and diff check recorded. | Prod posture smoke script exists, but live target stack credential run is not proven. | P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001 (in review). |
| SVC-SOURCE-SEARCH-TEST-CLOSURE | Done: index/contract closure and posture checks. | 27 index/contract tests, 9 posture tests, compose config recorded. | Live source-search prod posture smoke explicitly not run because no target stack was active. | P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001. |
| P0-CI-BOUNDED-001 | Done: bounded source/search and fail-closed adapter CI. | Adapter runner, bounded tests, posture/compose slice, OSS matrix, OpenClaw smoke, and source-search-bounded compose smoke recorded. | Compose/local CI smoke only. | P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001. |
| P1-SEARCH-001 | Done: OpenClaw governed SearchGateway integration. | 46 search tests and 103 OpenClaw/search/tool-bridge tests recorded. | No credentialed external search provider proof; OpenClaw receives governed evidence refs only. | P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001 for live source/search readback. |
| P1-SOURCE-001 | Done: news/social/alpha DB connector expansion with entitlement/PIT/available_time. | 52 source-ingestion tests and 61 evidence/search tests recorded. | No live/test credential connector proof recorded. | P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001 (in_progress). |
| P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001 | Done: bounded live connector smoke with end-to-end integration test. Codex review approved. | test_source_search_end_to_end_durable_readback: bounded external feed connector → SourceRecord/EvidenceBundle with PIT/entitlement/governance → shared durable evidence store → search index refresh → SearchGateway durable query with citation refs. 140 source+search tests pass (Codex verification). Governance enforcement (no Lean/broker/execution routes) confirmed. BFF/OpenClaw-adjacent slice: 86 tests pass. | dependency_missing evidence written for live feed target (no credentialed vendor feed configured); bounded in-process smoke via integration test and scripts/run_source_search_live_connector_smoke.py harness. | Complete. |

## OSS And Upstream Research Integration

| Task | Developed/materialized? | Repo proof | External proof | Gap/next |
|---|---|---|---|---|
| SPIKE-OC-001 | Evidence/gate: OpenClaw spike. | Spike evidence archived. | N/A. | Superseded by OpenClaw adapter/governed tasks. |
| SPIKE-DSPY-001 | Evidence/gate: DSPy spike. | Spike evidence archived. | N/A. | Superseded by DSPy governed path. |
| SPIKE-IMIT-001 | Evidence/gate: imitation spike. | Spike evidence archived. | N/A. | Superseded by imitation governed path. |
| SPIKE-EXP-001 | Evidence/gate: experiment tracking spike. | Spike evidence archived. | N/A. | Superseded by MLflow/W&B tasks. |
| SPIKE-QLIB-001 | Evidence/gate: Qlib spike. | Spike evidence archived. | N/A. | Superseded by Qlib adapter/activation tasks. |
| OSS-001 | Evidence/gate: OpenClaw source pin/evidence. | Source/evidence docs archived. | N/A. | Superseded by OpenClaw runtime adapter tasks. |
| OSS-001A | Evidence/gate: OpenClaw follow-up source/evidence hardening. | Evidence docs archived. | N/A. | Superseded by OpenClaw runtime adapter tasks. |
| OSS-002 | Evidence/gate: DSPy, imitation, MLflow regrade. | Regrade evidence archived. | N/A. | Later governed adapter tasks own runnable proof. |
| OSS-003 | Evidence/gate: activation criteria for Qlib, TRL, RL paths. | Criteria docs archived. | N/A. | Active follow-ups now own runtime activation proof. |
| BP5-OSS-001 | Evidence/gate: OpenClaw source and governed boundary pinned. | Boundary evidence archived. | N/A. | Superseded by BP5-OSS-002 and P1-SEARCH-001. |
| BP5-OSS-002 | Done: OpenClaw runtime adapter and smoke-tested path. | OpenClaw gateway adapter smoke recorded. | Real upstream gateway/container smoke recorded, but no broker/order/capital path. | Governed; no direct trading route. |
| BP5-OSS-003 | Mixed: DSPy/imitation/MLflow runnable adapters or defer proofs. | Adapter/defer evidence archived. | Local package/runtime proof only. | Already governed or handled by W&B follow-up. |
| BP5-OSS-004 | Evidence/gate: executable activation path for Qlib, TRL, RL, W&B. | Activation map archived. | N/A. | Active follow-ups now replace indefinite defer. |
| OSS-NEXT-001 | Done: Qlib governed adapter and smoke. | Qlib unit/smoke evidence archived. | No production-data credential proof. | P2-QLIB-PROD-DATA-ACTIVATION-001. |
| OSS-NEXT-002 | Done: TRL activation baseline and smoke. | TRL unit/smoke evidence archived. | No runtime FB-002 production-data proof. | P2-TRL-RUNTIME-DATA-ACTIVATION-001. |
| OSS-NEXT-003 | Evidence/gate: RL path activation decision. | Decision evidence archived. | N/A. | P2-RL-UPSTREAM-RUNTIME-SMOKE-001 owns bounded runtime smoke. |
| OSS-NEXT-004 | Evidence/gate: W&B backend parity/defer decision. | Decision/gate evidence archived. | No online W&B proof. | P2-WANDB-ONLINE-SYNC-001. |
| OSS-NEXT-005 | Evidence/gate: vectorbt task family definition. | Task-family evidence archived. | N/A. | Superseded by OSS-IMPL-003 and EXEC-OSS-VECTORBT-001. |
| OSS-NEXT-006 | Evidence/gate: statsmodels task family definition. | Task-family evidence archived. | N/A. | Superseded by OSS-IMPL-001 and EXEC-OSS-STATSMODELS-001. |
| OSS-NEXT-007 | Evidence/gate: QuantLib task family definition. | Task-family evidence archived. | N/A. | Superseded by OSS-IMPL-002 and EXEC-OSS-QUANTLIB-001. |
| OSS-NEXT-008 | Done: refresh governed path smoke/regression for OpenClaw, DSPy, imitation, MLflow. | Refreshed smoke/regression evidence archived. | Local/upstream runtime proof only; no broker/order path. | Governed baseline maintained. |
| OSS-IMPL-001 | Done: statsmodels governed adapter and smoke. | 20 unit tests and smoke recorded. | N/A external service. | Complete for research-only local package scope. |
| OSS-IMPL-002 | Done: QuantLib governed adapter and smoke. | Unit tests and smoke recorded. | N/A external service. | Complete for research-only local package scope. |
| OSS-IMPL-003 | Done: vectorbt governed adapter and smoke. | 28 unit tests and smoke recorded. | N/A external service. | Complete for research-only local package scope. |
| OSS-GATE2-001 | Evidence/gate: Gate 2 evidence packs for statsmodels, QuantLib, vectorbt. | Evidence packs archived. | N/A. | Superseded by execution-ready proof tasks. |
| EXEC-OSS-STATSMODELS-001 | Done: statsmodels execution-ready proof. | Execution-ready local proof archived. | N/A external service. | Complete for research-only local package scope. |
| EXEC-OSS-VECTORBT-001 | Done: vectorbt execution-ready proof. | Execution-ready local proof archived. | N/A external service. | Complete for research-only local package scope. |
| EXEC-OSS-RL-001 | Evidence/gate: RL upstream execution-ready proof. | Proof/gate evidence archived. | No bounded real runtime smoke completed for full RL stack. | P2-RL-UPSTREAM-RUNTIME-SMOKE-001. |
| EXEC-OSS-WANDB-001 | Evidence/gate: W&B execution-ready ambiguity closed. | Offline/defer proof archived. | No online W&B SDK-backed proof. | P2-WANDB-ONLINE-SYNC-001. |
| EXEC-OSS-QUANTLIB-001 | Done: QuantLib execution-ready proof. | Execution-ready local proof archived. | N/A external service. | Complete for research-only local package scope. |
| APP-003-TRL-ACTIVATION-001 | Done: TRL activation path. | Unit/smoke activation evidence archived. | No runtime FB-002 production-data proof. | P2-TRL-RUNTIME-DATA-ACTIVATION-001. |
| APP-003-QLIB-ACTIVATION-001 | Done: Qlib activation path. | Unit/smoke activation evidence archived. | No production-data credential proof. | P2-QLIB-PROD-DATA-ACTIVATION-001. |
| APP-003-FINRL-DEFERRED-PREP-001 | Done: FinRL deferred prep scaffold. | Offline workflow and smoke tests archived. | Offline/prep only. | P2-RL-UPSTREAM-RUNTIME-SMOKE-001. |
| APP-003-WANDB-DEFERRED-PREP-001 | Done: W&B deferred prep scaffold. | Unit and offline smoke coverage archived. | Offline/local only; no SDK-backed online sync. | P2-WANDB-ONLINE-SYNC-001. |
| APP-003-RLLIB-DEFERRED-PREP-001 | Done: RLlib deferred prep scaffold. | Pytest/smoke/worker evidence archived. | Offline/prep only. | P2-RL-UPSTREAM-RUNTIME-SMOKE-001. |
| APP-003-RAYTUNE-DEFERRED-PREP-001 | Done: Ray Tune deferred prep scaffold. | Offline tuning fixtures and smoke evidence archived. | Offline/prep only. | P2-RL-UPSTREAM-RUNTIME-SMOKE-001. |
| SVC-QLIB-GATED-PREACTIVATION-PREFLIGHT | Done: Qlib fail-closed preflight. | 19 Qlib unit tests and smoke_test.py recorded. | Offline/non-writing only. | P2-QLIB-PROD-DATA-ACTIVATION-001. |
| SVC-TRL-GATED-PREACTIVATION-PREFLIGHT | Done: TRL fail-closed preflight. | 29 TRL tests and smoke_test.py recorded. | Offline/non-writing only. | P2-TRL-RUNTIME-DATA-ACTIVATION-001. |
| SVC-FINRL-DORMANT-SCAFFOLD-CLOSEOUT | Done: FinRL dormant scaffold hardening. | Fail-closed smoke/worker gates and draft/none output evidence archived. | Offline/dormant only. | P2-RL-UPSTREAM-RUNTIME-SMOKE-001. |
| SVC-RLLIB-RAYTUNE-DORMANT-SCAFFOLD-CLOSEOUT | Done: RLlib/Ray Tune dormant scaffold hardening. | 29 tests and fail-closed worker/smoke gate evidence archived. | Offline/dormant only. | P2-RL-UPSTREAM-RUNTIME-SMOKE-001. |
| SVC-WANDB-OFFLINE-PREP-SCAFFOLD-CLOSEOUT | Done: W&B offline prep hardening. | Unit tests, memory smoke, W&B offline smoke, py_compile, and no-SDK-pin scan recorded. | Offline/local only; no SDK/network call. | P2-WANDB-ONLINE-SYNC-001. |
| SVC-OSS-DORMANT-COMPOSE-PROFILES | Done: dormant OSS compose profiles. | Compose default/profile checks, six-image dormant build, and six offline smoke runs recorded. | Offline/local only. | Follow-ups own runtime/online proof. |
| SVC-OSS-DORMANT-SMOKE-MATRIX | Done: dormant OSS smoke matrix. | 7/7 rows acceptable with gate_state=closed and activated=false. | Offline/local only; no registry/network/live writes. | Follow-ups own runtime/online proof. |
| SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC | Evidence/gate: docs synced to landed dormant scaffolds without activation overclaim. | Dormant OSS matrix 7/7 and stale wording scan recorded. | Offline/local only. | Follow-ups own runtime/online proof. |
| SVC-RESEARCH-OSS-PREACTIVATION-INTEGRATION-CLOSURE | Done: read-only BFF/service-backed preactivation integration wiring. | BFF contract tests, cross-service OSS tests, compose config, and dormant matrix recorded. | Offline/local only; production writes rejected. | Follow-ups own runtime/online proof. |
| SVC-QLIB-ACTIVATION-READY-ADAPTER | Done: Qlib activation-ready adapter behind gate. | 28 Qlib tests, Qlib smoke, 11 gateway activation tests, 9 rejection/http tests recorded. | Offline-gated; no production-data credential proof. | P2-QLIB-PROD-DATA-ACTIVATION-001. |
| SVC-TRL-ACTIVATION-READY-ADAPTER | Done: TRL activation-ready adapter behind gate. | 46 focused tests and enabled worker smoke recorded. | Offline/runtime-data gated; no production FB-002 proof. | P2-TRL-RUNTIME-DATA-ACTIVATION-001. |
| SVC-WANDB-OFFLINE-GATED-SYNC-ADAPTER | Done: W&B offline local run store and gated sync adapter. | 14+28+7+2 focused tests plus W&B smoke recorded. | Offline/gated only; online sync requires separate gate. | P2-WANDB-ONLINE-SYNC-001. |
| SVC-RL-FINRL-RLLIB-RAYTUNE-ACTIVATION-READY | Done: RL adapters activation-ready behind gate. | 16 FinRL, 33 RLlib/Ray Tune, and 25 gateway tests recorded. | Offline/bounded only; no online production activation. | P2-RL-UPSTREAM-RUNTIME-SMOKE-001. |
| SVC-OSS-ACTIVATION-READY-BFF-OPS | Done: OSS activation-ready BFF ops read-only surface. | 3 BFF contract tests recorded. | N/A external credential; ops surface only. | Follow-ups own runtime/online proof. |
| SVC-OSS-ACTIVATION-READY-SMOKE-MATRIX | Done: activation-ready OSS smoke matrix. | 16/16 matrix rows passed; forbidden writes false. | Offline/local matrix only. | Follow-ups own runtime/online proof. |
| SVC-OSS-WANDB-DORMANT-MATRIX-ALIGN | Done: W&B dormant matrix alignment. | Dormant matrix, offline-store gate, deferred-prep alias, and offline W&B smoke recorded. | No SDK-backed or online activation. | P2-WANDB-ONLINE-SYNC-001. |
| P2-OSS-ACTIVATE-001 | Evidence/gate: production research data posture corrected. | 111 focused tests and diff check recorded. | No online external service proof; it explicitly identified remaining credentials/storage/entitlement prerequisites. | Spawned active follow-ups for Qlib, TRL, RL, W&B, market-data, and source/search connector smoke. |

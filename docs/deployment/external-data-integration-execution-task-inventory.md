# External Data Integration Execution Task Inventory

Last updated: 2026-05-01.

This inventory lists mainline execution tasks related to external data,
upstream connectors, broker/venue integration, and governed source/search
ingestion. Sidecar, review-only, acceptance-only, and handoff tasks are not
listed as primary work items because they do not own the canonical integration
surface.

See `docs/deployment/external-data-integration-materialization-audit.md` for
the per-task development, repo-test, external-smoke, and gap audit.

## Materialization Status Snapshot

As of 2026-05-01, the inventory contains 90 mainline tasks: 83 archived with
terminal outcome `completed`, and 7 still active. The task records reference
377 artifact paths, and all 377 currently exist in the repo.

That means the task artifacts have materialized at the repository level. It
does not mean every external service has been exercised with production
credentials, nor that every upstream integration is production-active.

| Area | Materialization status |
| --- | --- |
| Market data / broker / venue data adapters | Repo-level adapters, config boundaries, docs, and tests are materialized for the completed datasource tasks. External production credential smoke is not implied by archive status. |
| Source/search ingestion | Service wrappers, bounded fetch, connector/index pipelines, durable store pilots, BFF ops, CI, and governed search/source connector artifacts are materialized. Production posture is allowed for non-ordering external reads when entitlement, rate limit, provenance, and audit controls pass. |
| OSS/upstream research tooling | Mixed by design: some adapters are materialized and smoke-tested; spike, decision, preflight, deferred, dormant, and offline-gated tasks are materialized as evidence, gates, scaffolds, or activation-ready lanes, not as unconditional production activation. The adjustable deferred/offline rows now have active follow-up tasks to finish runtime development and credentialed smoke where appropriate. |
| Broker order APIs | Not fully materialized. The missing task is P2-BROKER-SANDBOX-ORDER-001: paper/sandbox/test-key place/cancel/readback/reconcile smoke evidence still needs to be produced before any real-capital production order path can open. |

## Development And Test Status

| Category | Developed? | Repo-level tested? | External credential/runtime smoke? |
| --- | --- | --- | --- |
| Active | Partially. P2-BROKER-SANDBOX-ORDER-001 plus six P2 Wave 8 external activation follow-ups are now active work. | Not yet for the new follow-ups. Their acceptance requires repo tests plus runtime or credentialed smoke where applicable. | Not complete. Broker order smoke, market-data read smoke, source/search live connector smoke, and W&B/Qlib/TRL/RL runtime smoke are now explicit active work. |
| Market data / broker / venue | Yes for the archived task scope: adapters, normalization, provider boundaries, env/runbook material, and execution-readiness packets landed. | Partially to yes at repo level. The task closeouts record targeted adapter, data-plane, schema, normalization, and operator-smoke verification, but not every provider has evidence of credentialed external production smoke. | Not complete. Data-source live reads may be enabled under controls, but archive status does not prove every provider was exercised with live credentials. EP5 packet tasks explicitly do not authorize live broker orders. |
| Source / search ingestion | Yes. Service wrappers, bounded external fetch, autonomous pipeline, connector/index refresh, durable store pilot, BFF ops, retrieval cutoff, and CI materialized. | Yes at repo level. All 20 archived source/search tasks include pytest evidence or closeout verification, and their artifacts exist. | Partial. The platform supports bounded external fetch and production posture, but task archive completion is not the same as proving every external source is live-credential smoked in production. |
| OSS / upstream research integration | Mixed. Adapter/smoke-tested lanes are developed; spike, decision, deferred, dormant, preflight, and offline-gated lanes are intentionally not unconditional production integrations. | Mixed to yes for the intended gated scope. Many tasks record smoke or focused pytest; several are evidence/preflight/deferred tasks where the deliverable is a gate or activation map, not a running online integration. | Mostly no by design. W&B online sync, RL/TRL activation, and similar upstream lanes remain gated/offline unless explicitly activated. |

## Active Tasks

| Task | Status | Owner | Reviewer | Scope |
| --- | --- | --- | --- | --- |
| P2-BROKER-SANDBOX-ORDER-001 | review_approved | Codex2 | Codex | Broker paper/sandbox/test-key order API smoke for IBKR, Shioaji, and Kraken order flows. This is the corrected place for broker order API live-like validation; production real-capital order placement remains fail-closed. |
| P2-MARKETDATA-CREDENTIAL-SMOKE-001 | todo | Codex2 | Codex | Credentialed read-only market-data smoke for Massive/Polygon, TWSE/TPEx/MOPS/TEJ, CoinGecko/Kraken market data, and IBKR/Shioaji quote/read-only lanes; no order/capital side effects. |
| P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001 | in_progress | Gemini2 | Codex | Bounded live/test source connector smoke through SourceRecord/EvidenceBundle, durable search index, and BFF/SearchGateway readback; no broker/Lean/order route. |
| P2-WANDB-ONLINE-SYNC-001 | review | Codex | Claude | SDK-backed W&B online backend, SDK pin, structured smoke harness, BFF/evaluator W&B ref preservation, and gateway no-dispatch note are implemented; local credentialed smoke is skipped until a test W&B project/API key and SDK install are available; no broker, order, or capital path. |
| P2-QLIB-PROD-DATA-ACTIVATION-001 | in_progress | Codex2 | Claude | Move Qlib from activation-ready offline handoff to governed production-data activation packet and real/stub-selectable backend smoke; no order-capable route. Production proof contract and packet smoke are implemented; review handoff pending. |
| P2-TRL-RUNTIME-DATA-ACTIVATION-001 | review_approved | Codex2 | Codex | TRL activation evidence harness, evaluator packet persistence, and bounded FB-002 runtime-data smoke are implemented and reviewer-approved. Evidence: 240 governed events/pairs, 3 strategy families, all approve/edit/reject actions, checksum-bearing artifact bundle, evaluator packet, registry candidate packet, no order route, and explicit real-backend dependency/config evidence (`No module named 'trl'`, `silent_stub_fallback=false`). Owner closeout and done finalization pending. |
| P2-RL-UPSTREAM-RUNTIME-SMOKE-001 | todo | Codex | Codex2 | Move FinRL, RLlib, and Ray Tune from dormant/deferred prep to governed bounded train/search runtime smoke; still research artifacts only and no broker route. |

## Market Data, Broker, And Venue Tasks

| Task | Archived At | Scope |
| --- | --- | --- |
| APP-003-DATASOURCE-US-001 | 2026-04-24T16:31:12Z | Implement IBKR and Massive/Polygon US source integration. |
| APP-003-DATASOURCE-US-002 | 2026-04-24T16:46:14Z | Promote Massive/Polygon to primary US market data and tighten the IBKR boundary. |
| APP-003-DATASOURCE-TW-001 | 2026-04-24T16:36:50Z | Implement Shioaji, TWSE, TPEx, MOPS, and TEJ Taiwan integration. |
| APP-003-DATASOURCE-TW-002 | 2026-04-24T16:49:36Z | Build Taiwan normalization pipeline for Shioaji, TWSE, TPEx, MOPS, and TEJ. |
| APP-003-DATASOURCE-CRYPTO-001 | 2026-04-24T17:32:37Z | Implement Kraken and CoinGecko crypto integration. |
| APP-003-DATASOURCE-CRYPTO-002 | 2026-04-24T18:21:57Z | Add Kraken WebSocket realtime and execution-sync integration. |
| APP-003-DATASOURCE-OPS-001 | 2026-04-24T18:01:32Z | Provision governed data-source secrets, env, and smoke automation. |
| EP5-001 | 2026-04-22T15:18:25Z | Prepare canary-ready execution path. Related broker execution readiness. |
| EP5-002-PACKET-PREP-001 | 2026-04-28T00:26:26Z | Prepare runtime-manager-originated EP5 live canary proof packet. Related broker execution evidence packaging. |
| P2-LIVE-KERNEL-001 | 2026-05-01T15:34:04Z | Full LEAN launcher and broker SDK readiness plan. Related broker readiness, but broker order API smoke is split to P2-BROKER-SANDBOX-ORDER-001. |

## Source And Search Ingestion Tasks

| Task | Archived At | Scope |
| --- | --- | --- |
| SVC-SERVICE-DISPOSITION | 2026-04-28T13:41:44Z | Decide consultation, source-ingest, and search service disposition. |
| SVC-SOURCE-INGEST-SERVICE | 2026-04-28T17:59:46Z | Wrap source_ingestion as deployable source-ingest service. |
| SVC-SEARCH-SERVICE | 2026-04-28T18:38:30Z | Wrap governed search as deployable search service. |
| SVC-SOURCE-INGEST-AUTONOMOUS-PIPELINE | 2026-04-29T05:55:45Z | Advance source-ingest to autonomous durable ingest pipeline. |
| SVC-SEARCH-AUTONOMOUS-INDEX-PIPELINE | 2026-04-29T06:37:26Z | Advance search-svc to autonomous durable index pipeline. |
| SVC-SOURCE-INGEST-EXTERNAL-FETCH-BASELINE | 2026-04-29T16:07:06Z | Add bounded external fetch mode for source-ingest. |
| SVC-SOURCE-SEARCH-AUTONOMOUS-PIPELINE-SMOKE | 2026-04-29T16:59:15Z | Add end-to-end bounded autonomous source-to-search smoke. |
| SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT | 2026-04-29T18:46:47Z | Add optional Postgres store pilot for source-ingest and search. |
| SVC-SOURCE-SEARCH-AUTONOMOUS-CONNECTOR-INDEXER | 2026-04-30T02:53:59Z | Add autonomous connector and index refresh baseline. |
| SVC-SOURCE-CONNECTOR-FRAMEWORK | 2026-04-30T05:18:53Z | Build source connector framework. |
| SVC-SOURCE-EVIDENCE-NORMALIZATION | 2026-04-30T05:33:04Z | Normalize source evidence ownership. |
| SVC-SOURCE-CRAWL-FRONTIER-SCHEDULER | 2026-04-30T06:53:58Z | Add persistent source crawl frontier and scheduler. |
| SVC-SEARCH-INDEXING-PIPELINE | 2026-04-30T07:39:22Z | Build incremental search indexing pipeline. |
| SVC-SOURCE-SEARCH-OPS-BFF | 2026-04-30T08:23:40Z | Expose source/search ops in BFF. |
| SVC-SEARCH-RETRIEVAL-AND-CUTOFF | 2026-04-30T08:42:53Z | Harden search retrieval and cut off request-document normal path. |
| SVC-SOURCE-SEARCH-PROD-HARDENING | 2026-04-30T09:10:51Z | Harden source/search production posture. |
| SVC-SOURCE-SEARCH-TEST-CLOSURE | 2026-04-30T13:46:36Z | Source-search pipeline and SD-03 contract closure. |
| P0-CI-BOUNDED-001 | 2026-05-01T08:00:56Z | Add source/search bounded and adapter CI. |
| P1-SEARCH-001 | 2026-05-01T08:17:37Z | OpenClaw governed SearchGateway integration. |
| P1-SOURCE-001 | 2026-05-01T14:03:05Z | News, social, and alpha DB connector expansion. |

## OSS And Upstream Research Integration Tasks

| Task | Archived At | Scope |
| --- | --- | --- |
| SPIKE-OC-001 | 2026-04-14T05:34:18Z | OpenClaw upstream spike. |
| SPIKE-DSPY-001 | 2026-04-14T05:34:18Z | DSPy upstream spike. |
| SPIKE-IMIT-001 | 2026-04-14T05:34:18Z | Imitation learning upstream spike. |
| SPIKE-EXP-001 | 2026-04-14T05:34:18Z | Experiment tracking upstream spike. |
| SPIKE-QLIB-001 | 2026-04-14T05:34:18Z | Qlib upstream spike. |
| OSS-001 | 2026-04-14T05:34:18Z | Pin OpenClaw source and evidence. |
| OSS-001A | 2026-04-14T05:34:18Z | Follow-up OpenClaw source/evidence hardening. |
| OSS-002 | 2026-04-14T05:34:18Z | Regrade DSPy, imitation, and MLflow. |
| OSS-003 | 2026-04-14T05:34:18Z | Define activation criteria for Qlib, TRL, and RL paths. |
| BP5-OSS-001 | 2026-04-15T17:51:50Z | Pin OpenClaw source and governed boundary. |
| BP5-OSS-002 | 2026-04-16T18:56:12Z | Realize OpenClaw runtime adapter and smoke-tested path. |
| BP5-OSS-003 | 2026-04-15T19:21:33Z | DSPy, imitation, and MLflow runnable adapters or defer proofs. |
| BP5-OSS-004 | 2026-04-16T01:25:45Z | Executable activation path for Qlib, TRL, RL, and W&B. |
| OSS-NEXT-001 | 2026-04-17T03:01:15Z | Qlib governed adapter and smoke. |
| OSS-NEXT-002 | 2026-04-17T03:04:17Z | TRL activation baseline and smoke. |
| OSS-NEXT-003 | 2026-04-17T03:25:28Z | RL path activation decision. |
| OSS-NEXT-004 | 2026-04-17T06:10:39Z | W&B backend parity or defer. |
| OSS-NEXT-005 | 2026-04-17T04:38:11Z | vectorbt named execution-ready task family. |
| OSS-NEXT-006 | 2026-04-17T03:03:59Z | statsmodels named execution-ready task family. |
| OSS-NEXT-007 | 2026-04-17T03:06:23Z | QuantLib named execution-ready task family. |
| OSS-NEXT-008 | 2026-04-17T03:49:33Z | Refresh governed path smoke/regression for OpenClaw, DSPy, imitation, and MLflow. |
| OSS-IMPL-001 | 2026-04-17T18:48:02Z | statsmodels governed adapter and smoke. |
| OSS-IMPL-002 | 2026-04-17T19:15:15Z | QuantLib governed adapter and smoke. |
| OSS-IMPL-003 | 2026-04-17T22:47:14Z | vectorbt governed adapter and smoke. |
| OSS-GATE2-001 | 2026-04-18T06:00:10Z | Gate 2 evidence packs for statsmodels, QuantLib, and vectorbt. |
| EXEC-OSS-STATSMODELS-001 | 2026-04-21T02:02:16Z | statsmodels execution-ready integration proof. |
| EXEC-OSS-VECTORBT-001 | 2026-04-21T02:06:38Z | vectorbt execution-ready integration proof. |
| EXEC-OSS-RL-001 | 2026-04-21T03:05:38Z | RL upstream execution-ready integration proof. |
| EXEC-OSS-WANDB-001 | 2026-04-21T20:02:51Z | W&B upstream execution-ready integration proof. |
| EXEC-OSS-QUANTLIB-001 | 2026-04-21T20:04:03Z | QuantLib execution-ready integration proof. |
| APP-003-TRL-ACTIVATION-001 | 2026-04-24T17:17:55Z | TRL activation path. |
| APP-003-QLIB-ACTIVATION-001 | 2026-04-24T19:34:17Z | Qlib activation path. |
| APP-003-FINRL-DEFERRED-PREP-001 | 2026-04-25T05:00:33Z | FinRL deferred activation preparation. |
| APP-003-WANDB-DEFERRED-PREP-001 | 2026-04-25T06:17:21Z | W&B deferred activation preparation. |
| APP-003-RLLIB-DEFERRED-PREP-001 | 2026-04-25T09:44:20Z | RLlib deferred activation preparation. |
| APP-003-RAYTUNE-DEFERRED-PREP-001 | 2026-04-25T11:23:33Z | Ray Tune deferred activation preparation. |
| SVC-QLIB-GATED-PREACTIVATION-PREFLIGHT | 2026-04-29T16:39:04Z | Qlib gated preactivation preflight. |
| SVC-TRL-GATED-PREACTIVATION-PREFLIGHT | 2026-04-29T17:05:08Z | TRL gated preactivation preflight. |
| SVC-FINRL-DORMANT-SCAFFOLD-CLOSEOUT | 2026-04-29T17:13:00Z | FinRL dormant scaffold closeout. |
| SVC-RLLIB-RAYTUNE-DORMANT-SCAFFOLD-CLOSEOUT | 2026-04-29T17:20:47Z | RLlib/Ray Tune dormant scaffold closeout. |
| SVC-WANDB-OFFLINE-PREP-SCAFFOLD-CLOSEOUT | 2026-04-29T19:14:05Z | W&B offline prep scaffold closeout. |
| SVC-OSS-DORMANT-COMPOSE-PROFILES | 2026-04-29T19:53:14Z | OSS dormant compose profiles. |
| SVC-OSS-DORMANT-SMOKE-MATRIX | 2026-04-29T20:50:12Z | OSS dormant smoke matrix. |
| SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC | 2026-04-29T22:17:54Z | OSS activation gated truth sync. |
| SVC-RESEARCH-OSS-PREACTIVATION-INTEGRATION-CLOSURE | 2026-04-30T01:52:52Z | Research OSS preactivation integration closure. |
| SVC-QLIB-ACTIVATION-READY-ADAPTER | 2026-04-30T04:44:29Z | Qlib activation-ready adapter. |
| SVC-TRL-ACTIVATION-READY-ADAPTER | 2026-04-30T04:29:15Z | TRL activation-ready adapter. |
| SVC-WANDB-OFFLINE-GATED-SYNC-ADAPTER | 2026-04-30T05:18:04Z | W&B offline gated sync adapter. |
| SVC-RL-FINRL-RLLIB-RAYTUNE-ACTIVATION-READY | 2026-04-30T05:38:03Z | RL, FinRL, RLlib, and Ray Tune activation-ready surface. |
| SVC-OSS-ACTIVATION-READY-BFF-OPS | 2026-04-30T06:39:17Z | OSS activation-ready BFF ops. |
| SVC-OSS-ACTIVATION-READY-SMOKE-MATRIX | 2026-04-30T07:40:12Z | OSS activation-ready smoke matrix. |
| SVC-OSS-WANDB-DORMANT-MATRIX-ALIGN | 2026-04-30T14:12:20Z | Align W&B dormant matrix truth. |
| P2-OSS-ACTIVATE-001 | 2026-05-01T15:33:19Z | Research OSS production data posture and activation. Closed the blanket live-data-ban correction and spawned P2 Wave 8 follow-ups for adjustable deferred/offline rows. |

## Practical Status Read

- The market-data and non-trading external-source side should not be blocked by
  a blanket "avoid live/production" rule. Production/live reads are acceptable
  when secrets, rate limits, provenance, bounded fetches, and audit evidence are
  present.
- The corrected fail-closed boundary is production-live real-capital side
  effects, especially broker order placement/cancel/modify. Broker order APIs
  should still be integrated and smoked with paper, sandbox, or test-key
  credentials before any real-capital production path is opened.
- The missing high-value next execution task is P2-BROKER-SANDBOX-ORDER-001:
  perform paper/sandbox/test-key broker order API smoke and attach evidence.

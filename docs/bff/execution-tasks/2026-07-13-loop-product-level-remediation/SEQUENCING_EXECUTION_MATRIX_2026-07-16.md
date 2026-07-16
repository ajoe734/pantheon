# Loop Remediation Sequencing & Execution Matrix (2026-07-16)

Status: derived human-readable view of `docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/sequencing-overlay-2026-07-16.json`. The schema-v2 overlay is the machine authority; if this document diverges from it, the overlay wins and this view must be regenerated.

This reconciliation describes all 48 immutable catalog tasks exactly once. It does not rewrite catalog acceptance clauses, materialize tasks, mark tasks done, or use a wave-number shortcut as the release decision.

## 1. Authority and invariants

| Authority | Exact value |
|---|---|
| Original 48-task catalog | `docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/tasks.json` |
| Catalog SHA-256 | `44a893162da5779fc64292a70ba59fb7237eb4102ffb65f8e3ad3b64a8f31357` |
| Sequencing addendum | `docs/04/pantheon_loop_product_level_remediation_2026-07-13/REMEDIATION_SEQUENCING_ADDENDUM_2026-07-16.md` |
| Addendum SHA-256 | `9a3b735ac161b612e35a1d0e313cc7037da444f8b0311c623d27396a06d4b519` |
| Merged addendum authority | PR #3737 merge `a4b5df9a51bc3da6df0d39d422d9db4edc553aba` |
| Rejected interim delivery | PR #3746 head `5f51574df2791d7cb1c4551e46571ae5f06ea71a`, merged as `aae333959e0566759a4e7eb955f860d280fa5e3d`; retained as failed evidence, never as release authority |
| Overlay schema | `schema_version: 2` |
| Canonical overlay raw SHA-256 | `e506f62930bf0cb4f8cf6c3d1661b07ed638ad0903b8e640df3e178d7e9e7602` |

The overlay and this derived view enforce these invariants:

- The catalog and overlay contain the same 48 unique IDs, with no missing, extra, or duplicate task IDs.
- Every structured overlay entry has exactly `wave`, `classification`, `rationale`, `original_depends_on`, and `amended_depends_on`.
- The allowed classification vocabulary is exactly: `deferred strict-auth/security/governance work`; `final verification/closeout after the appropriate gate`; `part of the G2 proof path`; `permitted before the paper-trade proof`.
- `original_depends_on` is copied from the immutable catalog. Dispatch maps the fully validated `amended_depends_on` graph to the runtime dependency list only after complete-set, hash, cycle, and wave-order validation.
- External dependency IDs remain explicit. Internal amended dependencies cannot point forward to a later wave.

### Classification and wave totals

| Classification | Count |
|---|---:|
| `permitted before the paper-trade proof` | 21 |
| `part of the G2 proof path` | 8 |
| `deferred strict-auth/security/governance work` | 11 |
| `final verification/closeout after the appropriate gate` | 8 |
| **Total** | **48** |

| Wave | Count | Posture |
|---:|---:|---|
| 0 | 4 | Functional substrate under permissive dev auth |
| 1 | 16 | Canonical loops and side effects under permissive dev auth |
| 2 | 6 | Cross-loop paths and the G2 execution verifier |
| 3 | 3 | Permitted convergence work |
| 5 | 11 | Deferred strict-auth/security/governance hardening |
| 6 | 4 | Post-G2 verification |
| 7 | 1 | Post-G2 Management AI closeout |
| 8 | 1 | Post-G2 global product closeout |
| 9 | 1 | Protected Human/Ops sign-off |
| 10 | 1 | Final primary-catalog closeout |

Wave 4 is intentionally empty. That gap is descriptive only: release is controlled by the versioned task-set predicate below, never by `wave >= N`. Ungated Wave 0-3 work may proceed under permissive dev auth; every gated task remains parked until G2 validates.

## 2. Versioned release gate

| Field | Exact overlay value |
|---|---|
| `version` | `1` |
| `gate_id` | `hardening-after-g2-paper-trade-v1` |
| `release_predicate` | `g2_evidence_contract_v4_valid` |
| `pre_gate_action` | `park_new_and_existing_gated_tasks_allow_ungated` |
| `post_gate_action` | `allow_dependency_governed_materialization` |
| `gated_classifications` | `deferred strict-auth/security/governance work`; `final verification/closeout after the appropriate gate` |

The exact gated task set is:

- `LOOP-PROD-MAI-001`
- `LOOP-PROD-AUTH-001`
- `LOOP-PROD-FE-001`
- `LOOP-PROD-DELIVERY-001`
- `LOOP-PROD-AUTH-BOOT-001`
- `LOOP-PROD-WORKER-001`
- `LOOP-PROD-LEASE-001`
- `LOOP-PROD-FLEET-001`
- `LOOP-PROD-ATTEST-001`
- `LOOP-PROD-AUTH-OPS-001`
- `LOOP-PROD-BROWSER-AUTH-001`
- `LOOP-PROD-MAI-002`
- `LOOP-PROD-VERIFY-OODA-001`
- `LOOP-PROD-FE-EVID-001`
- `LOOP-PROD-FE-BUILD-001`
- `LOOP-PROD-MAI-003`
- `LOOP-PROD-CLOSE-001`
- `LOOP-PROD-SIGNOFF-001`
- `LOOP-PROD-CLOSE-002`

Before `g2_evidence_contract_v4_valid` is true, the dispatcher parks new and existing members of that exact 19-task set while continuing eligible ungated work. Once the predicate is true, normal amended-dependency admission governs them. Classification or membership, not a hard-coded wave comparison, determines release.

### Sequencing epoch and release binding

- Every overlay installation writes a schema-v2 sequencing epoch. A base migration embeds each exact pristine pre-overlay task snapshot; a fresh materialization embeds `null`. Canonical hashes bind the preimage, its contract and source reference, and the resulting post-overlay task.
- Epoch validation reconstructs every post-overlay task from its embedded preimage and rejects missing, extra, reordered, changed, non-pristine, or temporally invalid transitions. It never consults mutable runtime state as the historical preimage authority.
- A schema-v2 release record binds the canonical SHA-256 of the exact validated epoch. Rehashing or replacing an epoch after release therefore invalidates the release admission and keeps all 19 tasks parked.
- The release is also content-addressed in the program activity audit; consumer paths fail closed unless the epoch, release, per-task admission digest, and audit event all agree.

## 3. Pre-G2 acceptance deferral

| Field | Exact overlay value |
|---|---|
| `version` | `1` |
| `policy_id` | `pre-g2-strict-only-acceptance-deferral-v1` |
| `release_gate_id` | `hardening-after-g2-paper-trade-v1` |
| `catalog_acceptance_immutable` | `true` |
| `applies_to_classifications` | `permitted before the paper-trade proof`; `part of the G2 proof path` |
| `deferred_dimensions` | `strict_auth`, `browser_dev_bearer_removal`, `mfa`, `two_person`, `negative_identity` |
| `retained_dimensions` | `tenant_isolation`, `environment_binding`, `paper_execution`, `no_live_capital` |
| `materialized_acceptance_action` | `preserve_catalog_acceptance_unchanged` |

The policy applies to this exact 29-task set:

- `LOOP-PROD-000`
- `LOOP-PROD-001`
- `LOOP-PROD-002`
- `LOOP-PROD-REC-001`
- `LOOP-PROD-SRC-001`
- `LOOP-PROD-DIST-001`
- `LOOP-PROD-ALPHA-001`
- `LOOP-PROD-TEACH-001`
- `LOOP-PROD-AGORA-001`
- `LOOP-PROD-CONS-001`
- `LOOP-PROD-AGORA-002`
- `LOOP-PROD-AGORA-003`
- `LOOP-PROD-IMIT-001`
- `LOOP-PROD-DEP-001`
- `LOOP-PROD-CAP-001`
- `LOOP-PROD-TEL-001`
- `LOOP-PROD-TEL-002`
- `LOOP-PROD-EVO-001`
- `LOOP-PROD-BFF-001`
- `LOOP-PROD-OODA-001`
- `LOOP-PROD-PER-001`
- `LOOP-PROD-TJ-001`
- `LOOP-PROD-TJ-002`
- `LOOP-PROD-VERIFY-KNOW-001`
- `LOOP-PROD-VERIFY-EXEC-001`
- `LOOP-PROD-VERIFY-HUMAN-001`
- `LOOP-PROD-PPL-001`
- `LOOP-PROD-TJ-003`
- `LOOP-PROD-PINT-001`

This is a sequencing/admission policy, not an acceptance-clause rewrite. Before G2, it defers only strict auth, browser dev-bearer removal, MFA, two-person, and negative-identity proof. Tenant isolation, environment binding, paper execution, and no-live-capital controls remain mandatory.

## 4. G2 evidence contract v4

The Hardening Wave opens only when the exact target `LOOP-PROD-VERIFY-EXEC-001` has accepted closeout truth and its canonical paper-trade chain validates. A bare `done` status, digest-shaped strings, self-linked identifiers, or a minimal archive snapshot is not evidence.

### Authority, target, and files

| Field | Exact contract value |
|---|---|
| `version` | `4` |
| `target_task` | `LOOP-PROD-VERIFY-EXEC-001` |
| `target_task_original_contract_sha256` | `71ecc377427ef5ff539dff896e243bf9ac4a4017bd8554378fcf8ee8856e9235` |
| `target_task_amended_contract_sha256` | `71ecc377427ef5ff539dff896e243bf9ac4a4017bd8554378fcf8ee8856e9235` |
| `tasks_catalog_sha256` | `44a893162da5779fc64292a70ba59fb7237eb4102ffb65f8e3ad3b64a8f31357` |
| `sequencing_addendum_sha256` | `9a3b735ac161b612e35a1d0e313cc7037da444f8b0311c623d27396a06d4b519` |
| `merge_pr_3737_sha` | `a4b5df9a51bc3da6df0d39d422d9db4edc553aba` |
| `evidence_path` | `docs/deployment/evidence/loop-product-level/LOOP-PROD-VERIFY-EXEC-001/g2-paper-trade-chain.v4.json` |
| `closeout_manifest_path` | `docs/deployment/evidence/loop-product-level/LOOP-PROD-VERIFY-EXEC-001/evidence.json` |
| `hosted_probe_path` | `docs/deployment/evidence/loop-product-level/LOOP-PROD-VERIFY-EXEC-001/hosted-lifecycle-proof.v1.json` |
| `canonical_record_bundle_path` | `docs/deployment/evidence/loop-product-level/LOOP-PROD-VERIFY-EXEC-001/g2-canonical-records.v4.json` |
| `canonical_source_resolution` | `live_read_only_canonical_identity_and_projection_generation_v2` |
| Canonical database identity | database `pantheon`; role `pantheon_app`; schema/table `public.telemetry_events` |
| Canonical projection root | `/data/bff/lifecycle-projection` |
| `artifact_commit_binding` | `reviewer_and_github_bound_git_tree_v2` |
| Authoritative Git ref | `https://github.com/ajoe734/pantheon.git` at `refs/heads/dev` |
| Authoritative GitHub repository | API `https://api.github.com`; repository `ajoe734/pantheon` |
| `review_binding_schema` | `pantheon.g2-review-binding.v1` |
| `bundle_digest_algorithm` | `sha256(bytes)` |
| `record_digest_algorithm` | `sha256(canonical-json)` |

### Canonical-record resolution and linked chain

- The record bundle must use `pantheon.g2-canonical-record-bundle.v4`; its byte digest is recomputed with `sha256(bytes)`. Each referenced signal, order, fill, telemetry, and loop-run record digest is independently recomputed with `sha256(canonical-json)`.
- Signal, order, fill, and telemetry IDs must resolve in a read-only repeatable-read transaction against the pinned `pantheon` / `pantheon_app` / `public.telemetry_events` identity. The query is scoped by the full stable natural identity and returns the complete lifecycle chain, not caller-selected evidence IDs. The loop-run ID resolves from one immutable, regular-file projection generation captured under the pinned root.
- Event order is exactly prefix `signal_generation`, `trade_decision`, then at least 1 occurrence of repeat group `order_submitted`, `paper_fill_simulated`, `position_snapshot`, then suffix `reconciliation_completed`. The evidence roles bind signal=`signal_generation`, order=`order_submitted`, fill=`paper_fill_simulated`, telemetry=`reconciliation_completed`.
- Event sequence numbers, ingestion timestamps, creation timestamps, causality links, deterministic event identities, projection source offsets, and source high-water marks must agree. Reordered events or a projection ahead of/behind its claimed chain fail closed.
- Every record and projection must agree on all stable identity fields: `tenant_id`, `environment`, `journey_id`, `run_id`, `loop_run_id`, `signal_id`, `strategy_id`, `runtime_id`, `binding_id`, `capital_pool_id`, `persona_id`, `persona_capital_binding_id`, `artifact_id`, `artifact_version`, `plan_id`, `trace_id`. This binds tenant, paper environment, journey/run/loop-run, signal, strategy/runtime/binding/capital/persona/artifact/plan, and trace identity.
- The durable source attestation binds the database identity, projection root, source high-water mark, captured/current generation names, current projection checkpoint, and canonical row/projection digests into the release admission.

### Environment, projection, and freshness

| Requirement | Exact contract value |
|---|---|
| Target closeout environment | `dev` |
| Canonical record environment | `paper` |
| Execution mode | `paper` |
| Source mode | `live` |
| Projection stage status | `succeeded` |
| Required loop-run status | `completed` |
| Maximum evidence age | `86400 seconds` |
| Maximum chain span | `3600 seconds` |
| Maximum future skew | `300 seconds` |

The hosted proof must use `pantheon.loop-prod-tel-002-hosted-proof.v1`. Projection artifacts must use `pantheon.lifecycle-projection-bundle.v1`, `pantheon.trade-journey-projection.v1`, and `pantheon.loop-run-projection.v1`. The projection controller must be exactly `mode=live`, `accepted_live=true`, `truth_level=canonical_live`, `status=ready`, `backlog=0`; its current checkpoint must cover the live database high-water mark. Bundle capture, evidence issue/expiry, event creation/ingestion, hosted observation, and projection checkpoint timestamps/offsets must be fresh and monotonically ordered within these limits.

Operationally, validation requires read-only access to the pinned telemetry database identity, regular-file access beneath the pinned projection root, network access to the authoritative Git remote and GitHub API, and a caught-up live projector. Missing credentials, unavailable authority, shallow/replaced/alternate Git object state, symlinked projection artifacts, or stale controller state fail closed; no evidence is fabricated or inferred.

### Accepted closeout-truth admission

The verifier admits the target only when all of the following are true:

- The active or safely archived task is terminal `done` with a completed outcome, exact task/program/source/addendum/PR/overlay/task-contract provenance, and a delivery commit merged through the exact GitHub pull request to the authoritative `dev` ref. Merge parents, remote ancestry, successful GitHub check run, artifact blobs, and product manifest must all agree.
- The full product `evidence.json` validates against the repository product-evidence schema, its companion SHA-256 sidecar matches the exact bytes, and the task snapshot digest matches the admitted manifest.
- The assigned owner and reviewer are distinct. The reviewer approval is captured atomically as `pantheon.g2-review-binding.v1` and as a content-addressed program audit event; both bind the exact artifact head, five artifact digests, and implementation PR number/head/merge. The formal verdict is positive and digest-bound, every acceptance row is pass/not-applicable, and no blocking residual risk remains.
- A false-closed active task, a minimal archived `done` record, stale evidence, wrong source authority, wrong tenant/environment/run/status, or any chain/projection mismatch keeps the gate closed.

## 5. Detailed 48-task derived matrix

### Wave 0 — Functional substrate (permissive dev auth)

#### LOOP-PROD-000: Canonical loop inventory and OODA overlay truth

- **Wave**: 0
- **Summary**: 校正 loop catalog、BFF inventory 與 verification index：維持 12 個 L1 canonical loops，新增 per_persona_ooda 為 composite_overlay 並宣告 composed_of；archived task 不得被投影成 live maturity。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Define the 12 loops and composite OODA overlay.
- **Original Dependencies**: None
- **Amended Dependencies**: None

#### LOOP-PROD-001: Durable controller truth substrate

- **Wave**: 0
- **Summary**: 建立 tenant/environment scoped 的 durable controller record store、writer SDK 與 projector，記錄 desired/actual query、lease、dedupe、heartbeat/tick/success/failure/repair、backlog、deployment SHA 與 evidence truth level。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Set up tenant/env-scoped controller records.
- **Original Dependencies**: `LOOP-PROD-000`
- **Amended Dependencies**: `LOOP-PROD-000`

#### LOOP-PROD-002: Product evidence schema and anti-false-close gate

- **Wave**: 0
- **Summary**: 建立 machine-readable product evidence schema 與 supervisor closeout guard，拒絕 phantom cross-repo delivery、mock-only live claim、缺 terminal readback/restart/hosted/security/reviewer 或 unsupported maturity。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Define structured evidence schema rules.
- **Original Dependencies**: `LOOP-PROD-000`, `LOOP-PROD-001`
- **Amended Dependencies**: `LOOP-PROD-000`, `LOOP-PROD-001`

#### LOOP-PROD-REC-001: Full-stack loop recovery and fault-injection harness

- **Wave**: 0
- **Summary**: 建立可重複的 target-dev recovery harness，在 outbox、downstream mutation、receipt、projection 各切點注入故障，並驗證 duplicate、lease expiry、timeout、worker/BFF/DB/full-stack restart。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Set up full-stack restart/recovery & fault injection.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`


---

### Wave 1 — Canonical loops and side effects (permissive dev auth)

#### LOOP-PROD-SRC-001: Source requirement reconciler and default scheduler

- **Wave**: 1
- **Summary**: 把 persona/data requirement 當 desired state，預設 supervised reconciler/scheduler 持續建立與修復 connector、schedule，產生真實且有 provenance 的 normalized SourceRecord 與 SourceHealth。
- **Classification**: part of the G2 proof path
- **Rationale**: Permissive auth cutover enables default scheduler/reconciler.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-AUTH-001`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`

#### LOOP-PROD-DIST-001: Durable Strategy Distillation event consumer

- **Wave**: 1
- **Summary**: 以 durable SourceRecord inbox/outbox 與預設 worker 擁有 distillation，僅更新 mutable StrategySpec draft head；支援 catch-up、out-of-order、duplicate、DLQ、restart 並保護 approved immutable artifact。
- **Classification**: part of the G2 proof path
- **Rationale**: Distillation queue handler.
- **Original Dependencies**: `LOOP-PROD-SRC-001`
- **Amended Dependencies**: `LOOP-PROD-SRC-001`

#### LOOP-PROD-ALPHA-001: Durable Alpha Replication and revalidation worker

- **Wave**: 1
- **Summary**: 讓 reviewed immutable StrategySpec 進 durable replication queue，由預設 scheduled/command worker 執行非 stub revalidation，寫入真實 ExperimentRun 與 evidence lineage；production activation 保持 gate-closed。
- **Classification**: part of the G2 proof path
- **Rationale**: Strategy replication processor.
- **Original Dependencies**: `LOOP-PROD-DIST-001`
- **Amended Dependencies**: `LOOP-PROD-DIST-001`

#### LOOP-PROD-TEACH-001: Fail-closed Persona Teaching on authoritative data

- **Wave**: 1
- **Summary**: 移除 STUB1/STUB2、stub-ref 與 unconditional passed proof；evaluation 讀 canonical versioned dataset、freshness、threshold policy，資料不足即 fail closed，preview worker 預設啟動。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Persona teaching processor on authoritative data.
- **Original Dependencies**: `LOOP-PROD-SRC-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-ALPHA-001`
- **Amended Dependencies**: `LOOP-PROD-SRC-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-ALPHA-001`

#### LOOP-PROD-AGORA-001: Durable Agora evidence, dataset, and handoff worker

- **Wave**: 1
- **Summary**: 將 interaction、feedback、note、journal、insight 事件送入 tenant-scoped durable inbox，由預設 worker 產生 versioned dataset 與 evidence handoff；只可供 Observe/Learn，不得直接 deploy/trade。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Agora handoff and dataset worker.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-TEACH-001`, `LOOP-PROD-AUTH-001`, `AG-GAP-014`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-TEACH-001`, `AG-GAP-014`

#### LOOP-PROD-CONS-001: Real-participant Consultation workflow

- **Wave**: 1
- **Summary**: 移除自動製造 committee/memo/recommendation 的成功路徑；只有合格真實 participant/provider-authored transcript、memo 與 review evidence 才能 publish/handoff，provider 不可用時必須 waiting/blocked。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Reconcile participant consultation steps.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-AGORA-001`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-AGORA-001`

#### LOOP-PROD-AGORA-002: Implement six deferred Strategy Workshop operations

- **Wave**: 1
- **Summary**: 實作 v1.5 六個目前故意 501 的 operations：GET/POST versions、select version、POST research-runs、POST consultations、POST conclude；全部走 canonical store/command 並更新 OpenAPI/bundle/compat manifest。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Implement deferred workshop operations.
- **Original Dependencies**: `LOOP-PROD-CONS-001`, `LOOP-PROD-ALPHA-001`, `AG-GAP-005`, `LOOP-PROD-ATTEST-001`
- **Amended Dependencies**: `LOOP-PROD-CONS-001`, `LOOP-PROD-ALPHA-001`, `AG-GAP-005`

#### LOOP-PROD-AGORA-003: Hosted Strategy Workshop generated client and actions

- **Wave**: 1
- **Summary**: 在 execute-plans 更新 exact contract digest/generated client，完成六個 Strategy Workshop actions 的 terminal receipt/refetch、strict-auth desktop/mobile、degraded/error 與 compatibility gate。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Expose workshop controls on execute-plans.
- **Original Dependencies**: `LOOP-PROD-AGORA-002`, `LOOP-PROD-FE-001`, `AG-GAP-013`, `AG-GAP-014`, `OPS-EP-DEV-MAIN-RECONCILE-001`
- **Amended Dependencies**: `LOOP-PROD-AGORA-002`, `AG-GAP-013`, `AG-GAP-014`, `OPS-EP-DEV-MAIN-RECONCILE-001`

#### LOOP-PROD-IMIT-001: Default Human Imitation and shadow evaluation chain

- **Wave**: 1
- **Summary**: 讓 scheduler 自動發現合格 governed datasets，執行真實 shadow/OOS evaluator metrics，持久化 immutable candidate 與 lineage；不得靠 empty body，也不得繞過 experiment→approval→deployment。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Human imitation shadow evaluation algorithm.
- **Original Dependencies**: `LOOP-PROD-AGORA-001`, `LOOP-PROD-TEACH-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-CONS-001`, `LOOP-PROD-AGORA-002`
- **Amended Dependencies**: `LOOP-PROD-AGORA-001`, `LOOP-PROD-TEACH-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-CONS-001`, `LOOP-PROD-AGORA-002`

#### LOOP-PROD-DEP-001: Canonical deployment dispatcher and RuntimeBinding readback

- **Wave**: 1
- **Summary**: 讓 deployment outbox worker 真正呼叫 runtime_manager_dispatch_adapter/canonical runtime authority，而非 receipt-only；成功前讀回 RuntimeBinding/post-state，支援 retry/DLQ/replay/restart/compensation/kill-wins。
- **Classification**: part of the G2 proof path
- **Rationale**: Core dispatcher with RuntimeBinding.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-IMIT-001`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-IMIT-001`

#### LOOP-PROD-CAP-001: First-class bounded paper DecisionSignalProducer

- **Wave**: 1
- **Summary**: 建立非 SmokeStrategy/手工 script 的 first-class DecisionSignalProducer，發現 eligible paper bindings，攜帶 tenant/persona/binding/runtime/pool identities，確保 exactly-one worker、queue isolation、stop/restart，live broker/capital fail closed。
- **Classification**: part of the G2 proof path
- **Rationale**: Bounded paper order placement.
- **Original Dependencies**: `LOOP-PROD-DEP-001`, `LOOP-PROD-REC-001`, `TJ-E2E-014`
- **Amended Dependencies**: `LOOP-PROD-DEP-001`, `LOOP-PROD-REC-001`, `TJ-E2E-014`

#### LOOP-PROD-TEL-001: Default telemetry reconciliation and incident chain

- **Wave**: 1
- **Summary**: 預設啟動 scheduler、consumer、incident listener，以真實 runtime/operator telemetry 比對 authoritative actual state，產生 DriftReport 與 dedup Incident；不可 empty-green/fixture，需 retry/DLQ/replay/restart/lag truth。
- **Classification**: part of the G2 proof path
- **Rationale**: Telemetry capture & incident pipeline.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-CAP-001`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-CAP-001`

#### LOOP-PROD-TEL-002: Canonical loop-run and Trade Journey lifecycle projector

- **Wave**: 1
- **Summary**: 從真實 signal/decision/order/fill/position/reconciliation append events 投影 canonical loop-run 與 Trade Journey；維持單一 identity chain，manual/cron rebuild 只能標示 backfill，不能成為 live truth。
- **Classification**: part of the G2 proof path
- **Rationale**: Loop-run/Trade Journey lifecycle projection.
- **Original Dependencies**: `LOOP-PROD-CAP-001`, `LOOP-PROD-TEL-001`, `TJ-E2E-014`
- **Amended Dependencies**: `LOOP-PROD-CAP-001`, `LOOP-PROD-TEL-001`, `TJ-E2E-014`

#### LOOP-PROD-EVO-001: Real Evolution target-plane dispatcher

- **Wave**: 1
- **Summary**: approved EvolutionDecision 必須呼叫 canonical governance/deployment/runtime target plane，不得 synthetic SUBMITTED；預設 worker 持久化 terminal receipt、target post-state 與 formal journal link。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Triggers evolution rounds.
- **Original Dependencies**: `EVOCHAIN-011`, `LOOP-PROD-DEP-001`, `LOOP-PROD-TEL-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-TEL-002`
- **Amended Dependencies**: `EVOCHAIN-011`, `LOOP-PROD-DEP-001`, `LOOP-PROD-TEL-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-TEL-002`

#### LOOP-PROD-BFF-001: Authoritative BFF health monitoring and loop-health projection

- **Wave**: 1
- **Summary**: BFF monitor 使用各 downstream 真實 readiness contract，telemetry 具 canonical identities 並持久化 incident/recovery；/bff/v5/loop-health 讀 controller snapshots，不再 registry-only，清楚區分 stale/snapshot/scheduled/reconciled/live。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Exposes loop metrics & health.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-TEL-001`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-EVO-001`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-TEL-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-EVO-001`

#### LOOP-PROD-OODA-001: Per-Persona OODA schedule reconciliation and product proof

- **Wave**: 1
- **Summary**: 在既有 cron→provider turn→packet 基礎上，將 eligible persona desired state reconcile 成 exact canonical schedules，修 missing/orphan jobs；一次 run 對應一次 real turn 與一個 packet/terminal failure，Act 只能 proposal。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Multi-persona reconciliation schedule.
- **Original Dependencies**: `LOOP-PROD-000`, `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `OPENCLAW-CRON-WRITE-SCOPE`, `OPENCLAW-PERSONA-CRON-BACKFILL`, `OPENCLAW-OODA-PACKET-CLOSURE`, `LOOP-PROD-BFF-001`
- **Amended Dependencies**: `LOOP-PROD-000`, `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `OPENCLAW-CRON-WRITE-SCOPE`, `OPENCLAW-PERSONA-CRON-BACKFILL`, `OPENCLAW-OODA-PACKET-CLOSURE`, `LOOP-PROD-BFF-001`


---

### Wave 2 — Cross-loop paths and G2 execution verifier (permissive dev auth)

#### LOOP-PROD-PER-001: Persona provisioning through binding and first-evaluation readback

- **Wave**: 2
- **Summary**: persona create 必須保持 provisioning，直到 canonical RuntimeBinding、paper worker 與 first evaluation schedule 全部 read back；使用真實 persona identity，不可 seed binding，失敗需 terminal/restart-safe。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Binding and evaluation loop setup.
- **Original Dependencies**: `LOOP-PROD-DEP-001`, `LOOP-PROD-CAP-001`, `LOOP-PROD-OODA-001`, `PPL-ALLOC-010`, `PPL-ALLOC-011`
- **Amended Dependencies**: `LOOP-PROD-DEP-001`, `LOOP-PROD-CAP-001`, `LOOP-PROD-OODA-001`, `PPL-ALLOC-010`, `PPL-ALLOC-011`

#### LOOP-PROD-TJ-001: Canonical Trade Journey governed action backend

- **Wave**: 2
- **Summary**: 取代 default ACTION_DISPATCH_UNAVAILABLE；pause/cancel/escalate/retry/ack 依 action type 呼叫 canonical authorities，回傳 terminal receipt/refetch，保留 RBAC/MFA/idempotency/stale/live gate/partial failure。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Journey-tracking API.
- **Original Dependencies**: `LOOP-PROD-DEP-001`, `LOOP-PROD-CAP-001`, `LOOP-PROD-TEL-002`, `LOOP-PROD-EVO-001`, `LOOP-PROD-AUTH-001`, `TJ-E2E-014`, `LOOP-PROD-PER-001`
- **Amended Dependencies**: `LOOP-PROD-DEP-001`, `LOOP-PROD-CAP-001`, `LOOP-PROD-TEL-002`, `LOOP-PROD-EVO-001`, `TJ-E2E-014`, `LOOP-PROD-PER-001`

#### LOOP-PROD-TJ-002: Hosted Trade Journey action controls

- **Wave**: 2
- **Summary**: 在 execute-plans 加入 action availability、confirmation、terminal receipt/refetch、authenticated SSE reconnect，以及 partial-source/degraded/error UX；完成 desktop/mobile/a11y/strict auth evidence。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Expose journey controls on FE.
- **Original Dependencies**: `LOOP-PROD-TJ-001`, `LOOP-PROD-FE-001`, `MGMT-SSE-001`, `OPS-EP-DEV-MAIN-RECONCILE-001`, `LOOP-PROD-AGORA-003`
- **Amended Dependencies**: `LOOP-PROD-TJ-001`, `MGMT-SSE-001`, `OPS-EP-DEV-MAIN-RECONCILE-001`, `LOOP-PROD-AGORA-003`

#### LOOP-PROD-VERIFY-KNOW-001: Target-dev Knowledge spine product verifier

- **Wave**: 2
- **Summary**: 在 clean target-dev 驗證 Scenario A：real source→schedule→SourceRecord→distillation draft→reviewed replication→ExperimentRun→teaching/consultation/evidence handoff，含 duplicate/source/provider failure/restart/immutable protection。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Verify knowledge routing.
- **Original Dependencies**: `LOOP-PROD-SRC-001`, `LOOP-PROD-DIST-001`, `LOOP-PROD-ALPHA-001`, `LOOP-PROD-TEACH-001`, `LOOP-PROD-AGORA-001`, `LOOP-PROD-AGORA-002`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-CONS-001`, `LOOP-PROD-IMIT-001`, `LOOP-PROD-BFF-001`
- **Amended Dependencies**: `LOOP-PROD-SRC-001`, `LOOP-PROD-DIST-001`, `LOOP-PROD-ALPHA-001`, `LOOP-PROD-TEACH-001`, `LOOP-PROD-AGORA-001`, `LOOP-PROD-AGORA-002`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-CONS-001`, `LOOP-PROD-IMIT-001`, `LOOP-PROD-BFF-001`

#### LOOP-PROD-VERIFY-EXEC-001: Target-dev Execution spine product verifier

- **Wave**: 2
- **Summary**: 在 clean target-dev 驗證 Scenario B：create→plan→binding→worker→signal/decision/order/fill/position→telemetry/Journey→incident/postmortem/evolution→real target command，含 stack restart/isolation/rollback/no-live-capital。
- **Classification**: part of the G2 proof path
- **Rationale**: Verify the authoritative signal-to-order-to-fill-to-telemetry-to-loop-run execution chain.
- **Original Dependencies**: `LOOP-PROD-PER-001`, `LOOP-PROD-DEP-001`, `LOOP-PROD-CAP-001`, `LOOP-PROD-TEL-001`, `LOOP-PROD-TEL-002`, `LOOP-PROD-EVO-001`, `LOOP-PROD-BFF-001`, `PPL-ALLOC-012`
- **Amended Dependencies**: `LOOP-PROD-PER-001`, `LOOP-PROD-DEP-001`, `LOOP-PROD-CAP-001`, `LOOP-PROD-TEL-001`, `LOOP-PROD-TEL-002`, `LOOP-PROD-EVO-001`, `LOOP-PROD-BFF-001`, `PPL-ALLOC-012`

#### LOOP-PROD-VERIFY-HUMAN-001: Target-dev Human interaction and learning verifier

- **Wave**: 2
- **Summary**: 驗證 Scenario C：interaction→durable evidence→dataset/handoff→consultation或shadow evaluation→human decision→journal/OODA Learn；涵蓋 tenant/provider/unauthorized/rejected proposal 與 no direct deploy/trade。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Verify human action/learning.
- **Original Dependencies**: `LOOP-PROD-TEACH-001`, `LOOP-PROD-AGORA-001`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-CONS-001`, `LOOP-PROD-IMIT-001`, `PINT-010-R2`
- **Amended Dependencies**: `LOOP-PROD-TEACH-001`, `LOOP-PROD-AGORA-001`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-CONS-001`, `LOOP-PROD-IMIT-001`, `PINT-010-R2`


---

### Wave 3 — Permitted convergence (permissive dev auth)

#### LOOP-PROD-PPL-001: Persona promotion and allocation product closeout

- **Wave**: 3
- **Summary**: 消費既有 PPL-ALLOC-009..013，不重建；彙整 real attribution、ranking snapshot join、terminal allocation與 authoritative weights/restart、first evaluation schedule、legitimate two-man containment post-state、hosted create/paper/review/allocation UX。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Closeout allocation/promotion goals.
- **Original Dependencies**: `PPL-ALLOC-009`, `PPL-ALLOC-010`, `PPL-ALLOC-011`, `PPL-ALLOC-012`, `PPL-ALLOC-013`, `LOOP-PROD-PER-001`, `LOOP-PROD-VERIFY-EXEC-001`, `LOOP-PROD-FE-001`
- **Amended Dependencies**: `PPL-ALLOC-009`, `PPL-ALLOC-010`, `PPL-ALLOC-011`, `PPL-ALLOC-012`, `PPL-ALLOC-013`, `LOOP-PROD-PER-001`, `LOOP-PROD-VERIFY-EXEC-001`

#### LOOP-PROD-TJ-003: Trade Journey superseding product closeout

- **Wave**: 3
- **Summary**: 保留已完成 TJ-E2E-012 為舊 evidence，不重開；以 TJ-E2E-014、新 canonical projector/actions/UI 與 execution verifier 產生新的 product closeout。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Reconcile trade journey e2e.
- **Original Dependencies**: `TJ-E2E-012`, `TJ-E2E-014`, `LOOP-PROD-TJ-001`, `LOOP-PROD-TJ-002`, `LOOP-PROD-VERIFY-EXEC-001`
- **Amended Dependencies**: `TJ-E2E-012`, `TJ-E2E-014`, `LOOP-PROD-TJ-001`, `LOOP-PROD-TJ-002`, `LOOP-PROD-VERIFY-EXEC-001`

#### LOOP-PROD-PINT-001: Persona Interaction reconciled hosted product closeout

- **Wave**: 3
- **Summary**: 只透過 OPS-EP-DEV-MAIN-RECONCILE-001→PINT-010-R2 收斂；以 exact reconciled/deployed commit、green integration gate，驗證 consultation/disagreement/revision/paper validation/journal/audit/degraded+rollback desktop/mobile。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Closeout PINT requirements.
- **Original Dependencies**: `OPS-EP-DEV-MAIN-RECONCILE-001`, `PINT-010-R2`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-VERIFY-HUMAN-001`, `LOOP-PROD-FE-001`
- **Amended Dependencies**: `OPS-EP-DEV-MAIN-RECONCILE-001`, `PINT-010-R2`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-VERIFY-HUMAN-001`


---

### Wave 5 — Deferred strict-auth/security/governance hardening

#### LOOP-PROD-AUTH-001: Strict dev auth cutover and exact BFF build identity

- **Wave**: 5
- **Summary**: 沿用既有 /bff/auth/dev-login，將 hosted dev 切到 AUTH_STUB=false/strict；使用短效 role/tenant identities，移除 default all-role bearer，並在 /bff/version 暴露非敏感 git SHA、image digest、build time、environment、config posture。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Strict dev auth cutover.
- **Original Dependencies**: `LOOP-PROD-002`
- **Amended Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-VERIFY-EXEC-001`

#### LOOP-PROD-FE-001: Gate-before-deploy safe execute-plans release

- **Wave**: 5
- **Summary**: 重構 execute-plans dev release：exact candidate SHA integration gate 先通過，candidate pre-probe 後才 atomic switch，post-switch failure 自動 rollback；safe writes default false，browser 不含 bearer/client secret，並拒絕 out-of-order deploy。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Remove browser development tokens.
- **Original Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-AUTH-001`
- **Amended Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-AUTH-001`

#### LOOP-PROD-MAI-001: Hosted Management AI repair and dev-bridge backend proof

- **Wave**: 5
- **Summary**: 在 hosted strict auth 下證明 debug read-only 與 repair 完整鏈：activate→prepare narrow clean worktree→forward metadata→sentinel write/readback→SA/SD→pending packet→supervisor processed receipt→archive→deactivate。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Its hosted repair proof requires strict auth, exact-CAS worker integrity, and the credential-free browser cutover.
- **Original Dependencies**: `LOOP-PROD-AUTH-001`, `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-TJ-001`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-BROWSER-AUTH-001`
- **Amended Dependencies**: `LOOP-PROD-AUTH-001`, `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-TJ-001`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-BROWSER-AUTH-001`

#### LOOP-PROD-DELIVERY-001: Fleet-only delivery provenance and independent review admission

- **Wave**: 5
- **Summary**: 把 planner、fleet worker、fleet reviewer 的權限邊界做成 fail-closed delivery gate；沒有 canonical task、exact run/worktree/scope binding、獨立正式 review 或唯一 lease 的 PR 一律不能合併。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Ensure worker run/scope signing.
- **Original Dependencies**: `LOOP-PROD-002`
- **Amended Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-VERIFY-EXEC-001`

#### LOOP-PROD-AUTH-BOOT-001: Authorized dev auth credential bootstrap

- **Wave**: 5
- **Summary**: 在 strict auth hosted 驗收之前，由獲授權 Human/Ops 於受保護環境建立 dev signing、dev-login client 與最小權限身份；fleet 僅能驗證 redacted metadata，不能產生、讀取或輸出 secret。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Load strict credentials to vault.
- **Original Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-DELIVERY-001`
- **Amended Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-DELIVERY-001`

#### LOOP-PROD-WORKER-001: Exact-CAS worker outcome and forced termination integrity

- **Wave**: 5
- **Summary**: 所有 launch、resume、retry 與 terminal outcome 都必須以 exact task/event/owner/run/payload signature 做 admission/CAS；失去 ownership 的 process group 與 file-inbox payload 必須確認歸零。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Fleet container outcome and CAS.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-DELIVERY-001`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-DELIVERY-001`

#### LOOP-PROD-LEASE-001: Protected shared-dev mutation lease and payload isolation

- **Wave**: 5
- **Summary**: Dev deploy、OpenClaw、public smoke、Agora 都必須綁定同一 controller-issued lease；candidate 不得繼承 runner cloud credentials，release 前必須有 local/remote zero-member proof。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Mutation lease.
- **Original Dependencies**: `LOOP-PROD-AUTH-BOOT-001`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-WORKER-001`
- **Amended Dependencies**: `LOOP-PROD-AUTH-BOOT-001`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-WORKER-001`

#### LOOP-PROD-BROWSER-AUTH-001: Coordinated credential-free browser auth cutover

- **Wave**: 5
- **Summary**: 把 BFF strict auth、execute-plans credential-free build、完整 viewer route matrix 與 hosted browser 驗收綁成同一 cutover lease；任一側未就緒就不得啟用，且可原子回滾兩側。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: BFF cookie/session strict protection.
- **Original Dependencies**: `LOOP-PROD-AUTH-BOOT-001`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-FE-001`, `LOOP-PROD-DELIVERY-001`, `LOOP-PROD-LEASE-001`, `LOOP-PROD-AUTH-OPS-001`
- **Amended Dependencies**: `LOOP-PROD-AUTH-BOOT-001`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-FE-001`, `LOOP-PROD-DELIVERY-001`, `LOOP-PROD-LEASE-001`, `LOOP-PROD-AUTH-OPS-001`

#### LOOP-PROD-FLEET-001: Fair, quota-aware, starvation-bounded fleet admission

- **Wave**: 5
- **Summary**: 建立 age-aware、公平、quota reset aware 的 owner/reviewer admission；hot retry 必須 quarantine，不能長期佔用 reservation 讓較舊 ready work 飢餓。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Quota & resource admission boundaries.
- **Original Dependencies**: `LOOP-PROD-WORKER-001`
- **Amended Dependencies**: `LOOP-PROD-WORKER-001`

#### LOOP-PROD-ATTEST-001: Protected product attestation trust root

- **Wave**: 5
- **Summary**: Protected controller 從 immutable raw artifacts 產生 canonical attestation，並以 candidate 無法取得的 asymmetric key 或 platform-protected keyed identity 簽署；unkeyed checksum 只可作為 signed envelope 內的內容摘要。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Cryptographic task evidence signer.
- **Original Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-LEASE-001`
- **Amended Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-LEASE-001`

#### LOOP-PROD-AUTH-OPS-001: Governed dev credential and privileged-capability lifecycle

- **Wave**: 5
- **Summary**: 建立 dev JWT signing、dev-login client、role/tenant identity 與 assistant.kernel capability 的治理、rotation、expiry 與 hosted proof；未授權時保持 BLOCKED。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: MFA/Ops credentials rotation.
- **Original Dependencies**: `LOOP-PROD-AUTH-BOOT-001`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-LEASE-001`, `LOOP-PROD-ATTEST-001`
- **Amended Dependencies**: `LOOP-PROD-AUTH-BOOT-001`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-LEASE-001`, `LOOP-PROD-ATTEST-001`


---

### Wave 6 — Post-G2 verification

#### LOOP-PROD-MAI-002: Hosted Management AI repair product UI

- **Wave**: 6
- **Summary**: 在 execute-plans 呈現 mode/readiness、repair metadata、progress、receipt、deactivation與錯誤狀態；browser 不得直接連 OpenClaw，並證明 desktop/mobile/degraded/reconnect/rollback。
- **Classification**: final verification/closeout after the appropriate gate
- **Rationale**: Verify the hosted Management AI repair UI after strict auth and credential-free browser release.
- **Original Dependencies**: `LOOP-PROD-MAI-001`, `LOOP-PROD-FE-001`, `MGMT-SSE-001`, `OPS-EP-DEV-MAIN-RECONCILE-001`, `LOOP-PROD-TJ-002`
- **Amended Dependencies**: `LOOP-PROD-MAI-001`, `LOOP-PROD-FE-001`, `MGMT-SSE-001`, `OPS-EP-DEV-MAIN-RECONCILE-001`, `LOOP-PROD-TJ-002`

#### LOOP-PROD-VERIFY-OODA-001: Multi-persona OODA overlay product verifier

- **Wave**: 6
- **Summary**: 至少以三個動態 persona 各自完成 real OODA packet chain，驗證 duplicate cron、orphan repair、restart、provider outage、Learn attribution，並確認 Act 僅 proposal、無直接執行。
- **Classification**: final verification/closeout after the appropriate gate
- **Rationale**: Its exact OODA closeout composes the deferred hosted Management AI proof.
- **Original Dependencies**: `LOOP-PROD-OODA-001`, `LOOP-PROD-VERIFY-KNOW-001`, `LOOP-PROD-VERIFY-EXEC-001`, `LOOP-PROD-MAI-001`
- **Amended Dependencies**: `LOOP-PROD-OODA-001`, `LOOP-PROD-VERIFY-KNOW-001`, `LOOP-PROD-VERIFY-EXEC-001`, `LOOP-PROD-MAI-001`

#### LOOP-PROD-FE-EVID-001: Fail-closed protected-attestation consumer

- **Wave**: 6
- **Summary**: execute-plans release gate 只接受 protected controller attestation；candidate booleans、zero-count、fixture、snapshot 或 self-signed manifest 一律不能解鎖部署。
- **Classification**: final verification/closeout after the appropriate gate
- **Rationale**: Frontend checks attestation digests.
- **Original Dependencies**: `LOOP-PROD-FE-001`, `LOOP-PROD-ATTEST-001`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-TJ-002`, `LOOP-PROD-MAI-002`
- **Amended Dependencies**: `LOOP-PROD-FE-001`, `LOOP-PROD-ATTEST-001`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-TJ-002`, `LOOP-PROD-MAI-002`

#### LOOP-PROD-FE-BUILD-001: Warning-free, budgeted live/strict product build

- **Wave**: 6
- **Summary**: 最後 feature-bearing live/strict/safe-write build 必須無 invalid CSS、circular chunk、unexpected chunk-load error，並通過明確 bundle budget 與 hosted desktop/mobile quality gate。
- **Classification**: final verification/closeout after the appropriate gate
- **Rationale**: Strict build budget verification.
- **Original Dependencies**: `LOOP-PROD-FE-001`, `LOOP-PROD-FE-EVID-001`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-TJ-002`, `LOOP-PROD-MAI-002`
- **Amended Dependencies**: `LOOP-PROD-FE-001`, `LOOP-PROD-FE-EVID-001`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-TJ-002`, `LOOP-PROD-MAI-002`


---

### Wave 7 — Post-G2 Management AI closeout

#### LOOP-PROD-MAI-003: Management AI/OpenClaw product closeout

- **Wave**: 7
- **Summary**: 彙整 exact BFF/FE SHAs、repair sentinel、SA/SD→task→supervisor receipts、debug/repair security negatives、restart/rollback 與 residual risk，形成 Management AI product closeout。
- **Classification**: final verification/closeout after the appropriate gate
- **Rationale**: Close Management AI only after its strict backend, hosted UI, and OODA verification are admitted.
- **Original Dependencies**: `LOOP-PROD-MAI-001`, `LOOP-PROD-MAI-002`, `LOOP-PROD-VERIFY-OODA-001`
- **Amended Dependencies**: `LOOP-PROD-MAI-001`, `LOOP-PROD-MAI-002`, `LOOP-PROD-VERIFY-OODA-001`


---

### Wave 8 — Post-G2 global product closeout

#### LOOP-PROD-CLOSE-001: Global 12-loop plus OODA product closeout

- **Wave**: 8
- **Summary**: 從 clean target-dev 重跑四大 scenarios；12 canonical loops 加 OODA overlay 全部要有 current controller records、無 registry-only truth，maturity 僅由 evidence 推導，並取得獨立 Human/Ops verdict。
- **Classification**: final verification/closeout after the appropriate gate
- **Rationale**: Run the global all-scenario closeout after the G2 checkpoint and restored strict-auth/browser dependencies.
- **Original Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-FE-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-VERIFY-KNOW-001`, `LOOP-PROD-VERIFY-EXEC-001`, `LOOP-PROD-VERIFY-HUMAN-001`, `LOOP-PROD-VERIFY-OODA-001`, `LOOP-PROD-PPL-001`, `LOOP-PROD-TJ-003`, `LOOP-PROD-PINT-001`, `LOOP-PROD-MAI-003`
- **Amended Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-FE-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-VERIFY-KNOW-001`, `LOOP-PROD-VERIFY-EXEC-001`, `LOOP-PROD-VERIFY-HUMAN-001`, `LOOP-PROD-VERIFY-OODA-001`, `LOOP-PROD-PPL-001`, `LOOP-PROD-TJ-003`, `LOOP-PROD-PINT-001`, `LOOP-PROD-MAI-003`


---

### Wave 9 — Protected Human/Ops sign-off

#### LOOP-PROD-SIGNOFF-001: Protected Human/Ops completion verdict enforcement

- **Wave**: 9
- **Summary**: 在 final closeout 前安裝機器守門：所有 requires_human_ops_signoff 任務必須有受保護、可撤銷、不可重播且綁定 exact catalog、manifest、target 與部署 identity 的 Human/Ops 判決；fleet 不得自行簽發。
- **Classification**: final verification/closeout after the appropriate gate
- **Rationale**: Enforces manual signoff gating.
- **Original Dependencies**: `LOOP-PROD-CLOSE-001`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-ATTEST-001`
- **Amended Dependencies**: `LOOP-PROD-CLOSE-001`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-ATTEST-001`


---

### Wave 10 — Final primary-catalog closeout

#### LOOP-PROD-CLOSE-002: Final primary-catalog closeout after runtime bootstrap

- **Wave**: 10
- **Summary**: 從 clean target-dev 重跑 baseline 四大 scenarios 與 additive safety matrix；其餘 47 個 primary tasks、所有 external dependencies（包含已收斂之 EVOCHAIN-011、EVOLOOP-009 與 EVOLOOP-011）、fleet-only delivery provenance、coordinated browser auth、protected evidence、strict auth bootstrap/ops、fleet fairness、worker/lease integrity、受保護簽核與 warning-free frontend 全部通過後，guarded finalization 才可完成第 48 個任務並宣告 program 完成。
- **Classification**: final verification/closeout after the appropriate gate
- **Rationale**: Final program closeout verification.
- **Original Dependencies**: `EVOCHAIN-011`, `EVOLOOP-009`, `EVOLOOP-011`, `LOOP-PROD-CLOSE-001`, `LOOP-PROD-DELIVERY-001`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-LEASE-001`, `LOOP-PROD-BROWSER-AUTH-001`, `LOOP-PROD-FLEET-001`, `LOOP-PROD-ATTEST-001`, `LOOP-PROD-AUTH-OPS-001`, `LOOP-PROD-FE-EVID-001`, `LOOP-PROD-FE-BUILD-001`, `LOOP-PROD-SIGNOFF-001`
- **Amended Dependencies**: `EVOCHAIN-011`, `EVOLOOP-009`, `EVOLOOP-011`, `LOOP-PROD-CLOSE-001`, `LOOP-PROD-DELIVERY-001`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-LEASE-001`, `LOOP-PROD-BROWSER-AUTH-001`, `LOOP-PROD-FLEET-001`, `LOOP-PROD-ATTEST-001`, `LOOP-PROD-AUTH-OPS-001`, `LOOP-PROD-FE-EVID-001`, `LOOP-PROD-FE-BUILD-001`, `LOOP-PROD-SIGNOFF-001`

---

This file is intentionally a derived view. Change sequencing only in the schema-v2 overlay, validate the complete overlay atomically, and then regenerate this matrix.

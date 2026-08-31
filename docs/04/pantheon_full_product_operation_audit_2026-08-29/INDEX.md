# Full Product Operation Audit & Remediation Plan (2026-08-30)

## Executive Summary

This document package preserves the frozen, root-cause System Architecture (SA), System Design (SD), and parallel Execution Task Catalog for the original 20 product operation gaps (**OP-G01** through **OP-G20**) and appends two post-freeze operational gaps (**OP-G21** and **OP-G22**) discovered during governed execution. The frozen 30-task catalog remains immutable; execution lineage, replacement tasks, and late proof prerequisites are reconciled in the replacement ledger.

#### Baseline Provenance
- **Pantheon Baseline Commit**: `3322f802de76b91ee06fbbe08590ff2ed4bdaadf` (`origin/dev`)
- **Governed Command Runtime SHA**: `b49ebd934f34b3c4a648c1da863adb51a917a397` (promoted command runtime post PR #5444)
- **Accepted Hosted / Product BFF Backend Identity**: `4f0994be548f56da627740f5b7fb193844c1faed` (served `/deployment.json` and live `/bff/version` backend source commit)
- **Execute-Plans Baseline Commit**: `7d30e78476be61222af63a089e7ab141aa43b809` (`origin/dev`)
- **Hosted Environment**:
  - Served `/deployment.json`: Pair ID `b9209d6382cf109fda2504d7622fe7d9f137a084b0214988cc5588fffdeabc93`, Release Candidate ID `1497419171e98b33b42a01ebfd76c60368ab20e75ea45ac5fda61636a289e1cd`, Backend `4f0994be548f56da627740f5b7fb193844c1faed`, Frontend `7d30e78476be61222af63a089e7ab141aa43b809`, Controller Run `33325479949`, Integration Gate Run `33327155009`, Execute-Plans Deploy Run `33327736542`, Status `accepted` (accepted at `2026-08-30T18:28:45Z`).
  - Live `/bff/version`: Source Commit `4f0994be548f56da627740f5b7fb193844c1faed`, Status `accepted`.

#### Post-Freeze Execution Reconciliation
- `OPGAP-PLAN-EXECUTION-ERRATA-V2-20260830` was independently reviewed and merged in PR #5462 (`4cb0007386a6ad0c48b68cc2d71663f3655e5424`), but its `2026-08-30T20:45:00Z` cutoff preceded later governed replacements and deployment evidence. V3 supersedes that documentation result without rewriting the V2 snapshot.
- All nine Batch C catalog ids were later superseded by governed `-V2-20260830` tasks. Training additionally moved from V2 to evidence-contract-complete V3; bridge allowlist derivation moved from V3 to evidence-contract-complete V4.
- Deployment controller run `33332882810` failed its OpenClaw smoke. Its exact execute-plans integration gate `33334694659` nevertheless produced release candidate `9122d0fecd5cf9d5ae574c4c5e802df1d336dd2fd778a54019d2ad4995a2843d`, and run `33335314834` successfully switched that candidate's `read-only` profile. The bounded Persona write-proof and same-pair restore jobs were skipped, so these runs are not Firebase mutation or hosted-product completion evidence.

---

## 補強「正常運作」定義 (Definition of Normal Operation & Verification Protocol)

「有大量程式碼與測試通過」不等於「全系統正常運作」。為消除重複路徑、假 fallback 成功與偽完成宣稱，完整判定必須同時滿足以下 11 項獨立標準與驗證閘門：

1. **Natural Non-Stub Callers**: 所有 production entrypoint 具備真實 upstream caller；無 stub/mock 冒充 production runtime。
2. **Single Write Authority**: 每一類 mutation 只有唯一 write authority；read models 與 projections 嚴格由其衍生。
3. **Same-ID & Version Durable Readback**: 寫入成功必須具備 same-ID/version durable readback，重啟與多副本後仍一致。
4. **Fail-Closed Fault Semantics**: 重試、併發競爭、依賴故障、SSE replay 與 rollback 遵循 fail-closed 語意，無狀態污染。
5. **Authentic Test Topology**: 測試必須在真實多 process / DB 拓撲執行。跳過測試、逾時或缺少服務依賴者嚴格判定為 `NOT_EXECUTED` 或 `UNVERIFIED`，絕不可作為通過證據；執行斷言失敗者為 `FAIL`。
6. **Formal Governance Validation**: 安全與資金關鍵流程經由正式治理路徑驗證，不依賴 test fixture bypass。
7. **Exact Immutable Release Binding**: CI、deployment manifest、container images 與 exact FE/BFF SHAs 嚴格不可變綁定；缺 gate 即阻擋。
8. **Atomic Caller Cutover & Zero-Shim Deletion**: cutover 完成後，舊 implementation、forwarding shims、mounts 與專屬 tests 同步刪除。
9. **Explicit Observability & Correlation Receipts**: 每個狀態變更均產生唯一 trace ID、correlation receipt ID 與 journal sequence，支援跨 plane 追蹤。
10. **Governed CI Workflow Verification**: 所有 PR 與 release workflows 均有實際 job 啟動並通過；0-job 假綠流程視為治理未驗證並阻擋發布。
11. **Single Truth Reconciliation**: 系統狀態於 canonical task store、Git HEAD、部署 manifest 與 live caller wiring 四者間保持嚴格單一真相，消除漂移。

---

## Package Structure

1. **[CURRENT_GAP_DISPOSITION_2026-08-30.md](./CURRENT_GAP_DISPOSITION_2026-08-30.md)**
   Exhaustive disposition of the original 20 operational gaps (OP-G01 to OP-G20) plus post-freeze OP-G21/OP-G22, preserving the frozen audit and adding current task, deployment, and hosted-proof boundaries without relabelling incomplete work as complete.
2. **[SA_GAP_REMEDIATION_2026-08-30.md](./SA_GAP_REMEDIATION_2026-08-30.md)**
   Target System Architecture defining bounded context domain routing, single-namespace port consolidation (`ports/`), reverse import elimination, command executor retention, single-stimulus Source contract, authority/write/read ownership matrix, failure boundaries, the Persona typed mutation-port boundary, bounded OpenClaw readiness fallback, 11-point normal operation definition, and strict separation of development tooling vs product runtime.
3. **[SD_GAP_REMEDIATION_2026-08-30.md](./SD_GAP_REMEDIATION_2026-08-30.md)**
   Detailed System Design for the frozen 18-router migration plus post-freeze Persona reconciliation mutation-port and OpenClaw provider-readiness fallback contracts, including side-effect-safe invoke and no-credential-change authority boundaries.
4. **[EXECUTION_DAG_2026-08-30.md](./EXECUTION_DAG_2026-08-30.md)**
   The frozen 30-task DAG plus a post-freeze execution overlay for all governed replacements, transformed Batch D dependencies, post-freeze gaps, read-only deployment identity, and the still-open bounded Firebase write-proof/watchdog acceptance gate.
5. **[EXECUTION_TASK_CATALOG_2026-08-30.json](./EXECUTION_TASK_CATALOG_2026-08-30.json)**
   Machine-checkable authoritative JSON catalog containing the 30 child tasks with zero duplicate owned surfaces, route migration matrix, top-level AST node inventory with AST digests and edge-level cutover mappings, reverse-main symbol inventory (29 callsite-proven symbols with 100% identity preservation and zero fake port targets), external reverse-main import inventory (270 qualified instances across 215 caller files), domain_ports caller inventory (191 imported-symbol rows across 22 files), reachability-based frontend residual inventory, prior delivery dispositions, signed DevTaskPacket materialization mapping (max 16 tasks/packet), live-derived capacity, and embedded dynamic validation rules. **This catalog is immutable planning evidence and is never edited in place**; actual dispatch/supersede lineage is tracked separately below.
6. **[EXECUTION_REPLACEMENT_LEDGER_2026-08-30.json](./EXECUTION_REPLACEMENT_LEDGER_2026-08-30.json)**
   Versioned plan-execution ledger preserving the V2 23-row admission snapshot while appending: all nine post-freeze Batch C V2 replacements; Training V2 -> V3 and bridge-allowlist V3 -> V4 evidence-contract replacements; the first-stage Batch D dependency transformation and its newly stale Training V2 binding; OP-G21/OP-G22; exact deployment run/candidate/read-only identities; and fail-closed hosted-product proof prerequisites.

---

## Core Planning Rules & Guarantees

1. **Exact AST-Level Route & Symbol Migration**: All 2,272 top-level AST body nodes in `main.py` (68,313 lines, 441 HTTP route decorators across 421 unique route handlers) are mapped to concrete domain owners, pure composition root (`composition_keep`), real port abstractions in `ports/`, or legacy action cluster retirement. Standard library imports are never classified as `extract_shared_port`. All nodes have non-empty rationales and 100% edge-level cutover mappings for every consumer edge.
2. **Minimal Composition Root Invariant**: Governed by an explicit composition-root allowlist (FastAPI app, lifespan startup/shutdown, CORS/auth middlewares, 18 domain router inclusions, root composition logger). Target invariant: **zero inline route handlers, zero side effects outside lifespan, and zero reverse imports of main.py**.
3. **Zero Reverse Imports of `main.py`**: All 270 qualified external reverse-main import instances across BFF routers, background workers, and production scripts (including `command_executor.py`, `identity`, and `personalization`) are eliminated by extracting shared contracts to `services/control-plane/bff/ports/`.
4. **Port Namespace Consolidation**: `services/control-plane/bff/ports/` is the sole public and implementation namespace. All 191 imported-symbol rows across 22 unique files (129 production rows across 7 files, 62 test rows across 15 files) are migrated, the 6 `domain_ports/*.py` files are deleted, and rollback never restores deleted forwarding shims.
5. **Command Executor Preservation**: `command_executor.py` is retained as the production operator command executor, eliminating its reverse-main dependency while deleting dead legacy unrouted action adapters.
6. **Single-Receipt Source Contract**: Strict `reconcile_only` default in development; live stimulus bounded to a single receipt contract (`source_proof_receipt_id`) binding `connectorId` + `ingestRunId` + `sourceId` + `snapshotId` pre-switch and read-only reuse post-switch with zero second egress.
7. **Exclusive Artifact & Surface Ownership**: Zero duplicate `owned_code_surfaces` across all 30 child tasks. Implementation file owners are strictly separated from hosted evidence consumers.
8. **Non-Empty Router Deletion Inventories**: Every router task declares the exact inline `main.py` route handlers, helper functions, and globals being eliminated.
9. **Fail-Closed Forward Rollback**: All tasks specify forward repair or previous release artifact rollback, never restoring shims, duplicate handlers, or in-memory authority.
10. **Clean Materialization Batches & Signed DevTaskPacket Inbox Mapping**:
    - **Batch A (Bootstrap)**: 1 task (`OPGAP-DEVTOOL-TARGET-REPO-BRIDGE-20260830`) — `materializable_now: true`, `allowed_repos: ["pantheon"]`.
    - **Batch B (Parallel Domain Preparation)**: 14 tasks (13 primary domain routers + ports consolidation: Core, Persona, Training, Agora, Research, Governance, Evolution, Capital, Strategy, Management, Postmortem, Incident, Events, Ports Consolidation) — `materializable_now: false` (gated on Batch A bootstrap merge and command runtime promotion), `allowed_repos: ["pantheon"]`.
    - **Batch C (Support & Frontend)**: 9 tasks (5 support and infrastructure domain routers + 4 frontend tasks: Tools, Control Loops, Command Adapters, Runtime Binding, Deployments, FE Cleanup, FE Management, FE Agora, FE Assembly) — `materializable_now: false` (gated on Batch A bootstrap merge, command runtime promotion, and multi-repo allowed-repos config), `allowed_repos: ["pantheon", "execute-plans"]`.
    - **Batch D (Assembly, Retirement & Hosted Promotion/Acceptance)**: 6 tasks (Main Assembly, Command Cutover, Command Retirement, Hosted Promotion, Hosted Backend Acceptance, Hosted Management Acceptance) — `materializable_now: false` (gated on Batch B & C completion and signed readback), `allowed_repos: ["pantheon"]`.
    - Every batch satisfies `task_count <= 16` (fleet limit), forms a dependency-closed subgraph, maps to the signed local DevTaskPacket inbox (`.orchestrator/assistant-dev-packets/`), and produces durable processed receipts with authoritative readback.
11. **Authoritative Capability Selectors & Non-Authoritative Snapshots**:
    - `owner_selector` and `reviewer_selector` are the sole authoritative assignment rules for dispatch.
    - Literal `owner` and `reviewer` fields in the catalog are non-authoritative planning snapshots. At materialization time, live distinct eligible worker identities are dynamically resolved and post-bootstrap BridgeTask spec hashes are computed from the resolved tasks.
12. **Post-Bootstrap Canonical Spec Hash Binding**:
    - Post-bootstrap BridgeTask spec hash explicitly binds 14 canonical fields: `acceptance`, `artifacts`, `delivery_repository`, `dependency_tracks`, `depends_on`, `execution_resources`, `id`, `owner`, `phase`, `reviewer`, `summary`, `target_repo`, `task_class`, and `title`.

---

### Reproducible Dynamic Validation Commands

Run these commands from repository root to dynamically verify all 16 catalog invariants and 17 fail-closed mutation checks:

```bash
python3 docs/04/pantheon_full_product_operation_audit_2026-08-29/validate_catalog.py
python3 docs/04/pantheon_full_product_operation_audit_2026-08-29/test_mutations.py
```

The validation script executes 18 comprehensive verification phases:
1. `main.py` live AST body count (2,272 nodes), AST digest parity, and dynamic validation contract verification against catalog inventory
2. Edge-level cutover mappings for 100% of consuming tasks across all AST nodes
3. Legacy action cluster (9 nodes) assembly ownership and node 118 `os.makedirs` lifespan placement
4. Route migration inventory parity (441 route decorators across 421 unique route handlers)
5. Materialization batches (A: 1, B: 14, C: 9, D: 6), fleet limit `<= 16`, and task set equality
6. Exclusive `owned_code_surfaces` with zero collisions across all 30 child tasks
7. Safe forward rollback policies with zero forbidden shim/memory restoration keywords
8. DAG acyclicity and topological sortability across all 30 child tasks
9. Single-stimulus Source proof receipt contract (`source_proof_receipt_id`, 1 tick, 100 records max, `reconcile_only` default)
10. Special AST node mappings (`_resolve_param`, `_REPO_ROOT`, `_CRON_SERVICE_DIR`, `log`)
11. Reverse-main symbol inventory (29 callsite-proven symbols) and external caller files (215 files, 270 instances)
12. `domain_ports` caller inventory (191 rows across 22 files: 129 production across 7 files, 62 test across 15 files)
13. Dynamic planning agent capacity and authoritative capability selector validation
14. Planning baseline provenance across Pantheon, execute-plans, and hosted runtime
15. Bidirectional `pantheon-dev` execution resource invariant
16. Signed DevTaskPacket materialization mapping and post-bootstrap spec hashes (binding `target_repo` + `task_class` + `delivery_repository`) and catalog SHA-256 digest
17. Frozen execution replacement ledger: exact 23-row Batch B/C admission lineage, one-to-one Batch B V2 supersede mapping, unchanged functional scope, and the planned Batch D dependency transformation
18. Post-freeze execution reconciliation: all nine Batch C V2 replacements, Training V3 and allowlist V4 evidence contracts, OP-G21/OP-G22, task evidence path, exact deployment identity, and the unsatisfied Batch D/Firebase product-proof prerequisites

The mutation suite executes 23 distinct fail-closed assertions, including dedicated omissions for a late gap, governed replacement, task evidence manifest, deployment identity, and hosted-product proof prerequisite.

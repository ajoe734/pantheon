# Current Work

This file is generated from `ai-status.json` and `ai-activity-log.jsonl`.
Do not treat this file as the machine-readable source of truth.

Last updated: 2026-04-06T11:50:52Z

## Objective

Align the repo to the governed Pantheon target architecture where Pantheon orchestrates workflows, the evolution plane learns through feedback and registry gates, and LEAN executes only approved artifacts.

## Current Sprint

- Sprint: `2026-04-01-architecture-alignment`
- Canonical files: `AI_COLLABORATION_GUIDE.md`, `current-work.md`, `ai-status.json`, `ai-activity-log.jsonl`, `TARGET_ARCHITECTURE.md`, `ROADMAP.md`
- Dashboard: `docs-site/index.html`

## Active Slices

- `Claude`: execution, control-plane, governance-review; next: Creating services/learning/trl/WORKFLOW_DEFINITION.md and README.md — governance-first TRL preference-learning workflow using FB-002 governed feedback
- `Gemini`: gcp, ci-cd, runtime-packaging, worker-ops; next: Resetting status per AUD-CLAUDE-001: contract is done but implementation is missing. Re-evaluating LEAN native bridge.
- `Codex`: integration, status-system, schema, acceptance; next: Resuming EX-001 after owned_in_progress dispatch. Auditing artifact-loader contract vs implementation, promotion metadata alignment, and smoke-test path.
- `Grok`: research-ingest, external-search, spec-review, critique; next: LP-003 審查完成並批准。MLflow adapter 實現完整、測試充分、文檔清晰。現在轉向 LP-005 調整。

## Delivery Layers

### Pantheon Product Work

| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |
|---|---|---|---|---|---|---|
| `EX-001` | Epic A | Define artifact loader contract for paper and live execution | Codex | in_progress | `P2-001`, `P3-001` | 定義 artifact loader 契約，確保 LEAN 只會透過受治理的 artifact 與 Object Store 載入 paper/live 可執行內容。 |
| `REG-002` | Epic C | Implement candidate, paper, and live promotion gate | Codex | in_progress | `REG-001`, `EX-001` | 實作 candidate -> paper -> live 的 promotion gate，避免 persona 或工具直接把未批准內容推進 live。 |
| `REG-003` | Epic C | Add rollback and lineage requirements to promoted artifacts | Codex | review_approved | `REG-001`, `REG-002` | 把 rollback 與 lineage 要求補進所有已升版 artifact，確保 live 出問題時能追來源並安全回退。 |
| `FB-001` | Epic D | Define trajectory and preference store schema | Codex | review_approved | `REG-001` | 定義 trajectory 與 preference store schema，把人類回饋與系統學習資料收斂成可治理的格式。 |
| `FB-002` | Epic D | Capture trader approve, edit, reject, and rationale events | Codex | review_approved | `FB-001`, `OC-001` | 把交易員的 approve、edit、reject 與 rationale 變成可記錄的明確 feedback 事件，供後續學習使用。 |
| `FB-003` | Epic D | Capture execution telemetry including pnl, drawdown, slippage, and fills | Claude | todo | `P3-001`, `REG-002` | 把 pnl、drawdown、slippage、fills 等 execution telemetry 結構化，讓 evolution plane 能評估策略表現。 |
| `EV-001` | Epic D | Define evaluator and critic contracts | Grok | todo | `FB-001`, `FB-003` | 定義 evaluator 與 critic 的輸出契約，讓評估結果能被 registry 與 optimizer 使用，而不是直接改 live。 |
| `EV-002` | Epic D | Define optimizer outputs and registry handoff rules | Claude | todo | `EV-001`, `REG-001` | 定義 optimizer 產出與 registry handoff 規則，確保優化結果先變成受治理 artifact 再決定是否升版。 |
| `BUS-VAL-004` | Tooling | Skip stale queued wake events after task status changes | Codex | review_approved | - | 修補 orchestrator queue 中已過期的 wake event，避免 task 狀態已變更後仍被舊 dispatch 喚醒。 |

### Upstream OpenClaw / OSS Integration Work

| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |
|---|---|---|---|---|---|---|
| `RS-001` | Epic E | Begin research ingestion workflow implementation with verified adapters | Grok | in_progress | `OC-002` | 建立研究素材 ingestion workflow，讓 Pantheon 透過受控 API 管道發現論文、repo 與研究筆記，而不是自由漫遊抓網頁。 |
| `RS-002` | Epic E | Normalize discovered material into StrategySpec | Codex | todo | `RS-001`, `OC-003` | 把蒐集到的研究素材正規化成 StrategySpec，讓後續 replication、registry 與 execution 都吃同一種規格。 |
| `LP-001` | Epic F | Integrate DSPy for persona policy optimization | Codex | review_approved | `FB-001`, `OC-003` | 把 DSPy 用在 persona policy 優化，優化的是工具使用與推理流程，不是直接改 live 策略。 |
| `LP-002` | Epic F | Integrate imitation workflows for trader behavior cloning | Codex | todo | `FB-001`, `RS-002` | 導入 imitation 來學人類交易員的操作軌跡，讓系統能從行為示範而不是只靠文字偏好學習。 |
| `LP-003` | Epic F | Integrate experiment registry metadata with MLflow or W&B | Codex | review_approved | `REG-001` | 把 registry metadata 接到 MLflow 或 W&B，讓實驗結果、artifact 與 promotion 狀態能追蹤。 |
| `LP-004` | Epic F | Define approved TRL preference-learning workflows | Claude | in_progress | `FB-002`, `LP-001` | 定義受批准的 TRL preference-learning workflow，限制它只能吃治理過的 feedback 資料。 |
| `LP-005` | Epic F | Define RL path for FinRL or RLlib plus Tune when sequential RL is justified | Grok | review_approved | `RS-003`, `LP-003` | 定義何時才值得進入 FinRL / RLlib / Tune 的 RL 路徑，避免太早把系統複雜化。 |

## Task Board

| ID | Phase | Task | 中文說明 | Owner | Reviewer | Status | Depends On | Last Update | Next |
|---|---|---|---|---|---|---|---|---|---|
| `OPS-001` | Foundation | Canonicalize collaboration rules | 把協作規則收斂成單一真相，讓所有 LLM 都只看同一套規範，不再各自維護不同流程。 | Codex | Claude | done | - | 2026-04-01T04:20:00Z | Keep guide stable while downstream work starts |
| `OPS-002` | Foundation | Build JSON status pipeline | 建立 JSON 狀態與歷史紀錄機制，讓任務狀態、交接、阻塞都能被機器與人同步讀取。 | Codex | Gemini | done | `OPS-001` | 2026-04-01T04:24:00Z | Use script-driven updates only |
| `OPS-003` | Foundation | Build collaboration dashboard | 建立協作 dashboard，把分工、進度、依賴與最近活動視覺化，方便你隨時掌握全局。 | Codex | Claude | done | `OPS-002` | 2026-04-01T04:28:00Z | Collect visual feedback and adjust layout if needed |
| `P1-001` | Phase 1 | Define SignalStoreClient contract | 定義 SignalStoreClient 的最小穩定介面，讓 research、control plane 與 execution 都能用同一個 signal 存取邊界。 | Codex | Gemini | done | - | 2026-04-01T16:10:00Z | Interface locked and accepted by Gemini |
| `P2-001` | Phase 2 | Define signal JSON schema | 定義 signal 的機器可讀 schema，鎖定欄位、型別與交易語義，避免研究端和執行端各說各話。 | Gemini | Claude | done | `P1-001` | 2026-04-01T14:12:24Z | Canonical machine schema approved. Final contract alignment is tracked in P2-002 so schema.json, examples, and human-facing docs describe the same execution semantics. |
| `P2-002` | Phase 2 | Sync schema docs and examples to schema.json v1 | 把 schema 文件、範例 payload 與 machine schema 對齊成同一套契約，讓人看的文件和程式吃的格式一致。 | Codex | Gemini | done | `P2-001` | 2026-04-01T15:09:46Z | Human-facing schema docs, machine schema, and both example payload sets are aligned and validated against schema.json v1. |
| `P3-001` | Phase 3 | Wire LEAN runtime signal consumer | 把 signal intake 真正接進 LEAN runtime，讓已經標準化的訊號可以被解析、檢查後送到執行流程。 | Grok | Codex | done | `P1-001`, `P2-002` | 2026-04-06T11:46:45Z | Implemented three critical executor fixes: (1) EXIT+SHORT uses math.ceil() to preserve fractional shares in crypto—fixes truncation issue where -10.5 was being rounded to 10 instead of 11; (2) EXIT+LONG now checks position direction before liquidating—prevents closing opposite leg; (3) SELL+LONG now uses SetHoldings(0) for PERCENT_PORTFOLIO—matches docstring and provides consistency. All acceptance criteria met: runtime consumes signal payloads correctly, broker config edges documented in code comments. All 8 unit tests pass. |
| `P4-001` | Phase 4 | Draft control-plane routing contract | 定義 control-plane router 的邊界，決定請求怎麼分類、怎麼檢查權限、怎麼把狀態與監控交給下游。 | Claude | Codex | done | `P2-002` | 2026-04-02T03:43:02Z | Review approved: router contract is locked for v1. Remaining items are follow-up infrastructure tasks, not blockers for the contract. |
| `OPS-010` | Foundation | Validate status command flow | 驗證狀態指令流程可用，確認 assign、start、progress、handoff、blocker、done 會正確更新資料。 | Codex | Gemini | done | - | 2026-04-01T04:30:43Z | Validated assign/start/progress/blocker/handoff/done flow end-to-end |
| `OPS-011` | Foundation | Align agent handoff briefs with canonical collaboration flow | 把三個 agent 的 brief 統一到同一套協作流程，避免不同 prompt 指向不同規則。 | Codex | Claude | done | - | 2026-04-02T00:58:18Z | Updated collaboration guidance so every LLM now works in this order: finish assigned reviews first, then own unblocked work, then claim other safe tasks and hand them back to the original owner for review. |
| `ARC-001` | Architecture | Publish Pantheon target architecture aligned to the product and upstream integration model | 把 Pantheon 產品、上游 OpenClaw、Evolution Plane、Research Plane 與 LEAN 的責任分工寫清楚，避免再把產品本體和外部框架混成同一層。 | Codex | Claude | done | - | 2026-04-05T05:27:27Z | Use the Pantheon target architecture as the north star for product and integration planning. |
| `ARC-002` | Architecture | Publish epic roadmap aligned to target architecture | 把目標架構拆成可落地的 Epic 與任務序列，讓後續可以照依賴順序逐步實作。 | Codex | Gemini | done | `ARC-001` | 2026-04-01T06:26:52Z | Published epic roadmap that turns the target architecture into sequenced delivery slices and ownership lanes |
| `EX-001` | Epic A | Define artifact loader contract for paper and live execution | 定義 artifact loader 契約，確保 LEAN 只會透過受治理的 artifact 與 Object Store 載入 paper/live 可執行內容。 | Codex | Claude | in_progress | `P2-001`, `P3-001` | 2026-04-06T11:47:19Z | Resuming EX-001 after owned_in_progress dispatch. Auditing artifact-loader contract vs implementation, promotion metadata alignment, and smoke-test path. |
| `OC-001` | Epic B | Define Pantheon tool permission model for upstream OpenClaw integration | 定義 Pantheon 的工具權限模型，並把 upstream OpenClaw 的工具能力映射到 deny-first allowlist/denylist 規則。 | Codex | Claude | done | `P4-001` | 2026-04-02T04:20:37Z | OC-001 APPROVED for v1 lock. Review written at services/control-plane/permissions/review_oc001_claude.md. Key: deny-first model + 6 mandatory deny rules + approval hooks all aligned with router v1. Two minor open items (allow_with_approval not in schema effect enum; promotion-state not checked at router layer) documented but do not block v1. |
| `OC-002` | Epic B | Implement Pantheon cron workflows through upstream OpenClaw integration | 把 ingest、review、retrain、deploy 做成 Pantheon 的受治理 cron workflow，底層再接 upstream OpenClaw。 | Codex | Grok | done | `OC-001` | 2026-04-06T10:43:38Z | Cron workflows dispatched: All 4 workflows (ingest/review/retrain/deploy) verified and ready. Python syntax validated (py_compile), 8/8 unit tests passing, smoke tests 4/4 PASS. Integration with OC-001, OC-003, REG-002 confirmed. Ready for downstream use by RS-001, RS-002. |
| `OC-003` | Epic B | Define Pantheon StrategySpec and upstream OpenClaw handoff objects | 定義 Pantheon 的 StrategySpec 與 upstream OpenClaw workflow handoff 物件，讓 orchestration 輸出能被 registry、research 與 execution 穩定接手。 | Codex | Claude | done | `P4-001`, `OC-001` | 2026-04-02T05:37:22Z | OC-003 APPROVED for v1 lock. Review at services/control-plane/specs/review_oc003_claude.md. StrategySpec boundary correct (no execution detail leak), governance_context aligned with OC-001/P4-001, registry_hints not over-coupled to storage. Two minor open items: oneOf discriminator clarity in workflow_handoff schema, and from_stage/to_stage should eventually be enum-constrained. |
| `REG-001` | Epic C | Define strategy and model registry contract | 定義策略與模型 registry 契約，包含版本、生命週期、lineage、checksum 與 rollback 所需欄位。 | Codex | Gemini | done | `P4-001` | 2026-04-02T00:55:01Z | Started aligning REG-001 with EX-001 Object Store metadata so registry projection and loader rejection rules share one governed contract. |
| `REG-002` | Epic C | Implement candidate, paper, and live promotion gate | 實作 candidate -> paper -> live 的 promotion gate，避免 persona 或工具直接把未批准內容推進 live。 | Codex | Claude | in_progress | `REG-001`, `EX-001` | 2026-04-06T06:05:58Z | Reconciling promotion-gate metadata with REG-003/EX-001 contracts and tightening the service-local CLI/smoke-test path. |
| `AUD-GEMINI-001` | Architecture | Audit Gemini tasks against Upstream OSS Integration model | 根據新的上游 OSS 整合模型審計 Gemini 負責的任務，識別實作缺口。 | Gemini | Codex | done | `ARC-004`, `WORK_REBASELINE.md` | 2026-04-02T16:00:00Z | Audit complete. Handed off to Codex. |
| `REG-003` | Epic C | Add rollback and lineage requirements to promoted artifacts | 把 rollback 與 lineage 要求補進所有已升版 artifact，確保 live 出問題時能追來源並安全回退。 | Codex | Claude | review_approved | `REG-001`, `REG-002` | 2026-04-05T05:41:15Z | Implementation phase must clarify: (1) how gate.py metadata expectations align with lineage contract, (2) rollback target definition for paper vs live. Build promoted_artifact_metadata.schema.json, loader-side validation, and artifact creation flow. |
| `FB-001` | Epic D | Define trajectory and preference store schema | 定義 trajectory 與 preference store schema，把人類回饋與系統學習資料收斂成可治理的格式。 | Codex | Claude | review_approved | `REG-001` | 2026-04-05T12:24:01Z | APPROVED for v1 lock. Review at services/feedback/schema/review_fb001_fb002_claude.md. governance boundary correct (feedback cannot trigger live promotion), event family separation clean, linkage object sufficient for REG-001/EV-001. Three minor items: (M-1) actor_role 'reviewer' not in OC-001 role enum — reconcile in FB-002 follow-up; (M-2) telemetry promotion_state omits 'draft' — add comment to contract §6; (M-3) 'annotate' operation in edits is non-standard — add $comment to schema. Recommend evaluating registry_id as required field when EV-001 lands. |
| `FB-002` | Epic D | Capture trader approve, edit, reject, and rationale events | 把交易員的 approve、edit、reject 與 rationale 變成可記錄的明確 feedback 事件，供後續學習使用。 | Codex | Claude | review_approved | `FB-001`, `OC-001` | 2026-04-05T14:54:00Z | APPROVED for v1 lock. Review at services/feedback/schema/review_fb001_fb002_claude.md. Trader feedback ingestion captures approve/edit/reject/rationale with governed artifact linkage, idempotent JSONL storage, governance audit mirroring, and store-level RFC3339 query validation that returns HTTP 422 for invalid or inverted windows. Verification: python3 -m unittest discover -s services/control-plane/feedback -p 'test_*.py' (18 tests OK, 8 skipped because fastapi extras are absent); python3 -m py_compile services/control-plane/feedback/*.py passed. |
| `FB-003` | Epic D | Capture execution telemetry including pnl, drawdown, slippage, and fills | 把 pnl、drawdown、slippage、fills 等 execution telemetry 結構化，讓 evolution plane 能評估策略表現。 | Claude | Codex | todo | `P3-001`, `REG-002` | 2026-04-06T06:03:44Z | Ownership shifted from Gemini to Claude so execution telemetry can move with the execution/governance lane. Turn execution outputs into structured telemetry for the evolution plane |
| `EV-001` | Epic D | Define evaluator and critic contracts | 定義 evaluator 與 critic 的輸出契約，讓評估結果能被 registry 與 optimizer 使用，而不是直接改 live。 | Grok | Codex | todo | `FB-001`, `FB-003` | 2026-04-06T06:03:44Z | Ownership shifted from Gemini to Grok so evaluator/critic contract work stays moving even while Gemini auto workers are unstable. Define evaluator and critic contracts once telemetry and registry skeletons are in view |
| `EV-002` | Epic D | Define optimizer outputs and registry handoff rules | 定義 optimizer 產出與 registry handoff 規則，確保優化結果先變成受治理 artifact 再決定是否升版。 | Claude | Codex | todo | `EV-001`, `REG-001` | 2026-04-06T06:03:44Z | Ownership shifted from Gemini to Claude to keep optimizer handoff rules with the governance lane. Define optimizer outputs as governed artifacts instead of direct live mutations |
| `RS-001` | Epic E | Begin research ingestion workflow implementation with verified adapters | 建立研究素材 ingestion workflow，讓 Pantheon 透過受控 API 管道發現論文、repo 與研究筆記，而不是自由漫遊抓網頁。 | Grok | Claude | in_progress | `OC-002` | 2026-04-06T09:46:29Z | LP-003 審查完成並批准。MLflow adapter 實現完整、測試充分、文檔清晰。現在轉向 LP-005 調整。 |
| `RS-002` | Epic E | Normalize discovered material into StrategySpec | 把蒐集到的研究素材正規化成 StrategySpec，讓後續 replication、registry 與 execution 都吃同一種規格。 | Codex | Grok | todo | `RS-001`, `OC-003` | 2026-04-06T06:03:44Z | Reviewer shifted from Gemini to Grok to keep the research normalization lane flowing. Turn discovered research into normalized StrategySpec objects before replication |
| `RS-003` | Epic E | Run first-pass replication gate before registry admission | 在 registry admission 前先跑 first-pass replication gate，避免網路上找到的東西直接進正式候選。 | Grok | Claude | done | `RS-002`, `REG-001` | 2026-04-06T06:11:56Z | Grok |
| `LP-001` | Epic F | Integrate DSPy for persona policy optimization | 把 DSPy 用在 persona policy 優化，優化的是工具使用與推理流程，不是直接改 live 策略。 | Codex | Claude | review_approved | `FB-001`, `OC-003` | 2026-04-05T05:41:15Z | Codex to implement DSPy adapter under services/learning/dspy/ with BootstrapFewShot optimizer, pin DSPy version, and run smoke test with FB-001 governed examples. |
| `LP-002` | Epic F | Integrate imitation workflows for trader behavior cloning | 導入 imitation 來學人類交易員的操作軌跡，讓系統能從行為示範而不是只靠文字偏好學習。 | Codex | Grok | todo | `FB-001`, `RS-002` | 2026-04-06T06:03:44Z | Reviewer shifted from Gemini to Grok to reduce Gemini single-point review risk. [CODEX NOTE] moved to v1.5 from v2 — imitation (BC from trader trajectories) is more foundational than TRL; DSPy + imitation + MLflow should ship together in v1.5 |
| `LP-003` | Epic F | Integrate experiment registry metadata with MLflow or W&B | 把 registry metadata 接到 MLflow 或 W&B，讓實驗結果、artifact 與 promotion 狀態能追蹤。 | Codex | Grok | review_approved | `REG-001` | 2026-04-06T09:46:11Z | LP-003 通過審查: MLflow registry adapter 實現完整，experiment_refs/promoted_metadata 形狀與 REG-001/REG-003/EX-001 相容，可直接支援 LP-005。 |
| `LP-004` | Epic F | Define approved TRL preference-learning workflows | 定義受批准的 TRL preference-learning workflow，限制它只能吃治理過的 feedback 資料。 | Claude | Codex | in_progress | `FB-002`, `LP-001` | 2026-04-06T11:50:52Z | Creating services/learning/trl/WORKFLOW_DEFINITION.md and README.md — governance-first TRL preference-learning workflow using FB-002 governed feedback |
| `LP-005` | Epic F | Define RL path for FinRL or RLlib plus Tune when sequential RL is justified | 定義何時才值得進入 FinRL / RLlib / Tune 的 RL 路徑，避免太早把系統複雜化。 | Grok | Codex | review_approved | `RS-003`, `LP-003` | 2026-04-06T09:54:01Z | All 4 code review items fixed and committed. Lifecycle states aligned, RL artifact model updated for registry governance, RS-003 upstream role clarified, non-existent references removed. Document is now internally consistent with REG-001/REG-003/EX-001 contracts. |
| `ARC-003` | Architecture | Rebalance roadmap ownership to 40-40-20 across Codex, Gemini, and Claude | 重新分配工作量，讓 Codex 與 Gemini 扛比較多，Claude 專注在關鍵路徑與審查。 | Codex | Gemini | done | - | 2026-04-01T10:14:44Z | Rebalanced roadmap ownership so Codex and Gemini carry the larger share and Claude stays focused on review plus critical execution work |
| `ARC-004` | Architecture | Align roadmap ownership, schema contract follow-up, and registry sequencing | 把 roadmap、owner、schema 後續工作與 registry 排程重新對齊，避免規劃和實際任務板漂移。 | Codex | Gemini | done | `ARC-003` | 2026-04-01T14:21:28Z | Planning layers are aligned again; execution can move on a single contract path |
| `TOOL-001` | Tooling | Scaffold shared local orchestrator runtime | 建立共享的本機 orchestrator runtime，讓 watcher、supervisor、approval broker 與 provider inbox 在同一套共享狀態檔上協作。 | Codex | Gemini | done | `OPS-003` | 2026-04-05T05:27:27Z | Canonical local runtime now lives under .orchestrator/, .llm-inbox/, and .github/agents/. tools/pantheon-local remains historical reference only. |
| `RS-000` | Epic E | Draft Grok research and coding intake spec | 定義 Grok 在 Pantheon 裡可做的研究與 coding intake 邊界，說清楚可用來源、可做任務，以及如何把結果交給下游。 | Grok | Gemini | done | `OC-001`, `ARC-002` | 2026-04-05T23:11:00Z | Verified all acceptance criteria met: (1) structured-source-only policy documented in source catalog, (2) VS Code-first intake workflow documented, (3) handoff contract to RS-001/RS-002 with JSON schemas documented. Ready for Gemini review. |
| `AUD-CLAUDE-001` | Audit | Audit prior execution and control-plane work against the OSS integration model | 審查先前執行層與 control-plane 工作是否符合 upstream OSS 整合模型。 | Claude | Codex | done | - | 2026-04-04T13:54:39Z | Audit accepted as a valid corrective checkpoint. Confirmed: EX-001 is already reset to in_progress, upstream OpenClaw source selection is already captured in SPIKE-OC-001, and router contract now documents the persona stub as a temporary local surrogate. Remaining value: keep the audit as sequencing guidance for LEAN-native smoke coverage and upstream adapter follow-up. |
| `AUD-GROK-001` | Audit | Audit research and source workflows against the OSS integration model | 審查研究與來源處理工作是否符合 upstream OSS 整合模型。 | Grok | Gemini | done | - | 2026-04-05T14:43:03Z | Audit complete: RS-000 through RS-003 assessed against OSS integration model. Findings written to audits/oss-alignment/grok_audit.md. Key recommendation: RS-001-003 require upstream API adapters before proceeding as conceptual work. |
| `AUD-CODEX-001` | Audit | Audit contract and learning-integration work against the OSS integration model | 審查合約、schema 與學習框架整合工作是否符合 upstream OSS 整合模型。 | Codex | Gemini | done | - | 2026-04-02T08:14:20Z | Completed Codex-side audit note. REG-001 and FB-001 remain valid local governance contracts; LP-001 and LP-002 still need explicit upstream package selection, version pinning, adapter work, and smoke-test plans. |
| `SPIKE-OC-001` | Spike | Select upstream OpenClaw integration mode and pinning strategy | 釘死 upstream OpenClaw 的來源、整合模式與 pin 策略，避免再把 OpenClaw 當概念名詞而不是實際依賴。 | Codex | Claude | done | - | 2026-04-02T13:50:24Z | Selected upstream OpenClaw source, integration mode, pinning strategy, adapter points, and first smoke-test path. |
| `SPIKE-DSPY-001` | Spike | Select and pin DSPy integration path | 釘死 DSPy 的 upstream、版本與最小整合路徑，讓 LP-001 從純 contract 變成真的可接套件的工作。 | Codex | Claude | done | - | 2026-04-02T13:50:24Z | Selected DSPy upstream source, first version pin, optimizer, governed prompt_bundle boundary, and smoke-test plan. |
| `SPIKE-QLIB-001` | Spike | Select and pin Qlib research integration path | 決定 Qlib 的 upstream 與第一個 research smoke test，先走 supervised path，不讓 Qlib 直接越過治理邊界碰 live。 | Codex | Gemini | done | - | 2026-04-02T13:50:24Z | Selected upstream Qlib source, first package pin, worker packaging approach, governed adapter seam, and supervised smoke-test path. |
| `SPIKE-IMIT-001` | Spike | Select and pin imitation behavior-cloning integration path | 決定 imitation 的 upstream、版本與 BC-first 路徑，讓 trader trajectory learning 有清楚的第一版整合方式。 | Codex | Gemini | done | - | 2026-04-02T13:50:24Z | Selected upstream imitation source, first package pin, BC-first scope, governed trajectory adapter boundary, and smoke-test plan. |
| `SPIKE-EXP-001` | Spike | Select first experiment backend and registry mapping strategy | 先在 MLflow / W&B 之間做選型，決定第一個 experiment backend 與 registry/rollback metadata 的對應方式。 | Codex | Gemini | done | - | 2026-04-02T13:50:25Z | Selected MLflow as the first backend, pinned the first version, defined registry-mapping rules, and documented the first smoke-test path. |
| `AUD-GROK-002` | Audit | Implement OpenAlex and GitHub adapter spike for RS-001 | 為 RS-001 先做 OpenAlex 與 GitHub adapter spike，確認研究來源能走受治理的 structured API 路線。 | Grok | Gemini | done | - | 2026-04-05T14:54:18Z | Verified all tests passing, code compiled successfully, ready for Gemini review |
| `ARC-007` | Architecture | Extract Pantheon services into standalone repo with LEAN as submodule | 把 Pantheon 服務從混合式 LEAN workspace 分離成獨立 repo，並以 `lean/` submodule 方式接回 LEAN fork，避免邊界與同步策略繼續漂移。 | Codex | Gemini | done | - | 2026-04-05T13:33:14Z | Repo split cleanup and validation completed: removed duplicate root pantheon_algo, added repo-split runbook, switched docker-compose LEAN paths to ./lean, and verified submodule/layout/compose checks in pantheon. |
| `TOOL-002` | Tooling | Implement GitHub approval bus for mobile approvals | 建立 GitHub approval bus，讓 review 自動同步到 PR、blocked 自動同步到 issue，並把手機上的 approve/comment 透過 orchestrator polling 回寫到共享狀態。 | Codex | Gemini | done | - | 2026-04-05T08:02:20Z | GitHub approval bus is implemented locally: PR/issue sync, mobile comment commands, webhook intake, cloud relay interfaces, and templates are in place. |
| `BUS-VAL-001` | Tooling | Validate GitHub issue approval bus round-trip | 建立一個只用來驗證 GitHub approval bus 的 blocked 測試任務，確認手機上的 issue comment 指令能被 orchestrator 拉回並回寫到 ai-status。 | Codex | Gemini | done | - | 2026-04-05T13:37:54Z | Validated issue approval bus round-trip: GitHub issue #1 accepted /approve, ai-status reopened locally, and the pantheon ops issue was closed by the bus. |
| `BUS-VAL-002` | Tooling | Validate GitHub issue approval bus round-trip after task-id normalization fix | 第二輪 GitHub approval bus 驗證任務，專門確認 issue comment 指令在修正大小寫容錯後，能由 orchestrator 自動回寫 ai-status。 | Codex | Gemini | done | - | 2026-04-05T13:37:58Z | Validated post-normalization issue approval bus round-trip: GitHub issue #3 accepted /approve with task-id normalization, ai-status reopened locally, and the pantheon ops issue was closed by the bus. |
| `BUS-VAL-003` | Tooling | Validate Pantheon blocked-task GitHub bus after workspace cutover | 第三輪 blocked 驗證任務，專門確認 cutover 後的 Pantheon supervisor 會從 pantheon repo 本身建立 GitHub ops issue。 | Codex | Gemini | done | - | 2026-04-05T14:08:50Z | Hardened orchestrator worker-failure detection while investigating bus re-dispatches: transcript scans now only match anchored runtime/tool failure lines, so borrowed log text like Error:/QUOTA_EXHAUSTED inside BUS-VAL-001 or FB-002 sessions no longer triggers false worker_failed + retry loops. Added .orchestrator/test_supervisor.py regression coverage and verified python3 -m unittest discover -s .orchestrator -p 'test_*.py' plus py_compile. |
| `BUS-VAL-004` | Tooling | Skip stale queued wake events after task status changes | 修補 orchestrator queue 中已過期的 wake event，避免 task 狀態已變更後仍被舊 dispatch 喚醒。 | Codex | Claude | review_approved | - | 2026-04-06T06:03:44Z | Reviewer shifted from Gemini to Claude so tooling validation no longer depends on the unstable Gemini review lane. Review approved: The stale dispatch guard in process_queue correctly re-validates events against current status, and regression tests successfully verify the skipping of stale events and execution of current events. |

## Handoff Queue

| Task | From | To | Message | Status | Created At |
|---|---|---|---|---|---|
| _(none)_ | - | - | - | - | - |

## Blockers

| Task | Owner | Waiting For | Message | Status |
|---|---|---|---|---|
| _(none)_ | - | - | - | - |

## Review Notes

| Task | Reviewer | 修正重點 | Review File |
|---|---|---|---|
| `P4-001` | Codex | 審查通過：permission evaluation 已前移，deny-first evaluator 與最小 role resolution 已和 contract 對齊。<br>session TTL / rate-limit 現在是鎖定政策，runtime enforcement 明確 deferred 到 gateway 或 session backend，這對 v1 contract 是可接受的。<br>monitoring 與 SSE 已改成 deferred scope，不再混成 v1 已實作能力。 | services/control-plane/router/review_p4001_codex_zh.md |
| `OC-001` | Claude | 審查通過：deny-first 模型、強制 deny 規則與 approval hook 都已和 Router v1 對齊。<br>後續只需追蹤兩個 minor open items：policy schema 是否要直接表達 allow_with_approval，以及 promotion-state 是否要在 router 層做二次確認。 | services/control-plane/permissions/review_oc001_claude.md |
| `OC-002` | Grok | O<br>C<br>-<br>0<br>0<br>2<br> <br>實<br>作<br>完<br>整<br>且<br>所<br>有<br>測<br>試<br>通<br>過<br>。<br>四<br>個<br>工<br>作<br>流<br>程<br>已<br>通<br>過<br>整<br>合<br>驗<br>證<br>：<br>i<br>n<br>g<br>e<br>s<br>t<br> <br>製<br>造<br>受<br>治<br>理<br>的<br> <br>r<br>e<br>s<br>e<br>a<br>r<br>c<br>h<br>_<br>p<br>a<br>c<br>k<br>a<br>g<br>e<br>、<br>r<br>e<br>v<br>i<br>e<br>w<br> <br>產<br>生<br> <br>a<br>p<br>p<br>r<br>o<br>v<br>a<br>l<br>_<br>r<br>e<br>q<br>u<br>e<br>s<br>t<br>（<br>政<br>策<br>實<br>施<br>）<br>、<br>r<br>e<br>t<br>r<br>a<br>i<br>n<br> <br>排<br>放<br> <br>r<br>e<br>g<br>i<br>s<br>t<br>r<br>y<br>_<br>s<br>u<br>b<br>m<br>i<br>s<br>s<br>i<br>o<br>n<br>、<br>d<br>e<br>p<br>l<br>o<br>y<br> <br>正<br>確<br>路<br>由<br>通<br>過<br> <br>R<br>E<br>G<br>-<br>0<br>0<br>2<br> <br>推<br>昇<br>門<br>。<br>所<br>有<br>依<br>賴<br>滿<br>足<br>。<br>授<br>權<br>派<br>送<br>。 | services/control-plane/cron/review_oc002_grok.md |
| `REG-001` | Gemini | 已開始吸收 EX-001 的 Object Store metadata 要求，接下來要鎖定 execution projection 與 loader rejection semantics。 | - |
| `FB-002` | Claude | 審查通過：approve/edit/reject/rationale 事件覆蓋完整，governance 邊界正確。<br>minor follow-up 已吸收：actor_role 對齊 OC-001、contract 補上 telemetry draft 說明、annotate 操作補上 schema $comment。 | services/feedback/schema/review_fb001_fb002_claude.md |
| `LP-003` | Grok | LP-003 通過審查。實現完整：正確的治理語義、完整的 lifecycle state 對應、充分的 lineage 與 rollback 支援、完整的 artifact handoff、promoted_metadata 適於下游使用。所有測試通過。與 LP-005 完全相容。 | services/registry/experiments/review_lp003_grok_zh.md |
| `AUD-CLAUDE-001` | Codex | 審查通過：Claude 的 audit 仍是有效的 OSS 整合校正點，主要缺口判斷成立。<br>需要加註時序修正：EX-001 已在 audit 後被重設為 in_progress，且 contract path 已補到 services/execution/artifact-loader/。<br>P4-001 的 persona stub 定位也已正式寫入 router contract；後續重點保留在 P3-001 的真 LEAN bridge、EX-001 的實作與 smoke test，以及 repo 邊界收斂。 | audits/oss-alignment/review_aud_claude_001_codex.md |

## Latest Checkpoints

- 2026-04-06T11:50:17Z Orchestrator: `LP-004` Approval state disappeared before the worker could resume.
- 2026-04-06T11:50:17Z Orchestrator: `LP-004` Approval state disappeared before the worker could resume.
- 2026-04-06T11:50:17Z Orchestrator: `LP-004` Approval state disappeared before the worker could resume.
- 2026-04-06T11:50:17Z Orchestrator: `LP-004` Approval state disappeared before the worker could resume.
- 2026-04-06T11:50:17Z Orchestrator: `LP-004` Approval state disappeared before the worker could resume.
- 2026-04-06T11:50:17Z Orchestrator: `LP-004` Approval state disappeared before the worker could resume.
- 2026-04-06T11:50:24Z Orchestrator: PreToolUse: Bash
- 2026-04-06T11:50:24Z Orchestrator: PostToolUse: Bash
- 2026-04-06T11:50:25Z Orchestrator: PreToolUse: Bash
- 2026-04-06T11:50:25Z Orchestrator: PostToolUse: Bash
- 2026-04-06T11:50:29Z Orchestrator: PreToolUse: Bash
- 2026-04-06T11:50:29Z Orchestrator: PostToolUse: Bash
- 2026-04-06T11:50:30Z Orchestrator: PreToolUse: Bash
- 2026-04-06T11:50:30Z Orchestrator: PostToolUse: Bash
- 2026-04-06T11:50:33Z Orchestrator: PreToolUse: Bash
- 2026-04-06T11:50:33Z Orchestrator: PostToolUse: Bash
- 2026-04-06T11:50:34Z Orchestrator: PreToolUse: Bash
- 2026-04-06T11:50:34Z Orchestrator: PostToolUse: Bash
- 2026-04-06T11:50:52Z Orchestrator: PreToolUse: Bash
- 2026-04-06T11:50:52Z Codex: `LP-004` Creating services/learning/trl/WORKFLOW_DEFINITION.md and README.md — governance-first TRL preference-learning workflow using FB-002 governed feedback

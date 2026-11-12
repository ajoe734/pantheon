# DEV-PLAN-DISCOVERY-DEMAND-EVOLUTION-20261112 — delivery plan for research demand, mechanism tests and scheduled evolution

| Field | Value |
| --- | --- |
| Design | `SD_DISCOVERY_DEMAND_AND_EVOLUTION_TRIGGERS_2026-11-12.md` (PR #7206) |
| Loop-closure prerequisites | `DISCOVERY-ALLOCATOR-RUNS-ADMITTED-STRATEGY-20261112`, `DISCOVERY-GATE-PASS-APPROVED-BASELINE-20261112` |
| Repo base | `origin/dev` e28bef7e4 |
| Scope | plan and decision record only; the tasks below already exist in the live task store |

## 0. 中文摘要

使用者 11/12 回覆「都依照你的建議處理」，所以 SD 的 O-13 到 O-20 全部採用建議方案（§1）。第 5 節的實作拆成 9 筆任務，另加 1 筆 hosted 驗收。有改到同一個檔案的任務一律排先後，不平行；完整清單見 §2。

另外兩件事：
- 會碰到 gate.py 的兩筆 INTRADAY 任務，排在 GATE-MECHANISM-STAGE 之後。
- O-14 (b)（四週後讓抓取主機自動拉取查詢計畫）和 O-19（從商品選擇學習）這次都沒有開任務。

## 1. Operator decisions (2026-11-12, verbatim)

> 都依照你的建議處理

| # | Adopted option |
| --- | --- |
| O-13 | (a) free slot with no candidate ≥ min score and (b) after a decay retire |
| O-14 | (a) weekly plan applied as a reviewed `fetch-policy.json` change for the first four weeks; (b) automatic pull afterwards with the same vocabulary — **not opened yet** |
| O-15 | (b) a contradicted prediction blocks admission |
| O-16 | revalidate every 20 trading days |
| O-17 | weekly per artifact, 50 variants per decision, family lifetime cap 500 trials |
| O-18 | (b) retire a lagging parent with `superseded_by_child` when an eligible child exists |
| O-19 | (a) defer expression-policy learning until 20 trading days of counterfactuals — **no task** |
| O-20 | (a) fixed vocabulary, no model-proposed query terms |

The same message also answered the coordinator's question about switching the hosted discovery allocator off
until the allocator fix merges. The coordinator did not add a separate switch: `DISCOVERY_ALLOCATOR_ENABLED` is not
fed from a GitHub variable (only the compose default `true`, `docker-compose.yml:788`), and the only files that
change it (`docker-compose.yml`, `services/research/tests/test_discovery_allocator_schedule.py`) are held by the
running allocator task, which itself stops the unsafe behaviour (gate-passed candidates only, allocator-created
discovery personas only).

## 2. Tasks (live task store, 2026-11-12)

| Task | Owner → Reviewer | Depends on | SD section |
| --- | --- | --- | --- |
| `HYPOTHESIS-CARD-PREDICTIONS-20261112` | Codex → Claude | — | §3.2 card |
| `RESEARCH-DEMAND-RECORD-20261112` | Claude → Codex | allocator task | §3.1 record and read surface |
| `L2-DEMAND-PRIORITY-20261112` | Codex → Claude | demand record; card predictions | §3.1.1 (also: capped records are deferred, not skipped) |
| `COLLECTOR-DEMAND-QUERY-PLAN-20261112` | Codex2 → Claude | demand record | §3.1.2 with O-14 (a), O-20 (a) |
| `DISCOVERY-EVOLUTION-SCHEDULER-20261112` | Codex → Claude | baseline task; demand record | §3.3 with O-16, O-17, O-18 |
| `GATE-MECHANISM-STAGE-20261112` | Claude → Codex2 | card predictions; baseline task | §3.2 gate with O-15 (b) |
| `TEMPLATE-DEMAND-SEARCH-20261112` | Claude2 → Codex | query plan; evolution scheduler | §3.1.3 |
| `FINDING-SPONSOR-PERSONA-20261112` | Claude2 → Codex | mechanism stage; evolution scheduler | §3.5 |
| `FE-RESEARCH-DEMAND-VIEW-20261112` (`execute-plans`) | Claude2 → Codex2 | demand record; query plan | §3.1 read surface |
| `DISCOVERY-W5-HOSTED-ACCEPTANCE-20261112` | operator → Claude | all above; W4 acceptance | SD §5 last row |

Ordering added to existing tasks: `INTRADAY-REPLICATION-GATE-20261111` and `INTRADAY-EVOLUTION-SEARCH-20261111` now
depend on `GATE-MECHANISM-STAGE-20261112` (shared `services/research/replication/gate.py`).

Dependencies follow shared files, not only logic: every pair of tasks that edits the same file is ordered.

## 3. Rules carried into every task

* The production path calls the new code, proven by an integration test through the real entry point
  (waves 1–4 shipped several functions with no production caller).
* Missing data fails closed; production code never synthesizes market data, candidates or baselines
  (synthetic fallbacks were found in #7147/#7152 on 2026-11-11).
* Tests touching external sources use recorded real responses.
* Hosted acceptance uses naturally occurring events only.

## 4. Not opened

* O-14 (b) automatic collector pull — open after four weekly plans have been reviewed.
* O-19 expression-policy learning — open after 20 trading days of counterfactual records.

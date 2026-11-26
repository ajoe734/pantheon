# SD-PERSONA-POOL-CAPITAL-ADDENDUM-20261112 — capital between personas reuses the quarterly ranking and allocation mechanism

| Field | Value |
| --- | --- |
| Extends | `SD_PERSONA_STRATEGY_POOL_AUTONOMY_2026-11-12.md` (PR #7217, review approved) |
| Repo base | `origin/dev` 8999306d1 |
| Hosted reads | read-only Postgres (`rankings.rankings`) and read-only file reads in `pantheon-capital-1`, 2026-11-12 04:1xZ |
| Scope | design only |

## 0. 中文摘要（操作者用）

- **persona 之間的資金調整不新做，沿用 7/07 設計的季排名與分配機制。** 協調者 11/12 曾說「沒有這個機制」，那是錯的，操作者已糾正。
- **這套機制在 hosted 上有在算排名，但從來沒有產生過資金調整。** 三個 persona 的分數完全相同（62.5），分不出高下，原因在 §2。
- **策略池設計的一個技術問題已由審查者定案（§1）：** 池子每次變動，都要產生一個新的、登記過的版本才能上線；paper 階段的核准自動完成，不需要人。
- **要做的事（§4）：** 修計分讓它能分出高下、讓 paper 模擬分配真的每季跑、把策略池的風險額度利用率接進分配係數（這一項要操作者同意）。

## 1. Review resolution carried into the pool design (R6)

The reviewer of PR #7217 decided R6: reading pool membership at decision time does not meet the registry's
checksum-bound approval and single-active-artifact rules. Therefore every executed pool version (membership
and weights) is a new immutable envelope artifact pinning member versions and checksums, with lineage,
verified Governance approval, a DeploymentPlan and a RuntimeBinding; mismatch fails closed. Automated paper
approval keeps decision P-4 (no extra human approval within paper).

## 2. The existing mechanism (verified)

| Part | Where | Fact |
| --- | --- | --- |
| Design | `docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md` | quarterly ranking → target weights → rebalance proposal → simulation and risk check → human approval → apply → audit and rollback target |
| Allocation policy | `services/capital/allocation_policy.py` | seven-component rank score; tier caps S 25 % / A 15 % / B 8 % / watch 0 %; canary ≤ 5 %; quarterly increase cap +25 %; exclusions; a separate paper simulation allocation |
| League score | `services/control-plane/bff/personas/service.py:5627` | `overall_score = 0.35 pnl + 0.25 risk + 0.25 execution + 0.15 activity`, formula `pm12-default-v1`, tiers 1–4 |
| Snapshots | `services/rankings/snapshots.py`, table `rankings.rankings` | quarterly and rolling snapshots |
| Routes | `/bff/rankings`, `/bff/management/quarterly-ranking/recommendations`, `/bff/rebalances` (+ `/apply`), `/bff/management/allocation-policy/evaluate` | present |

Hosted dev, 2026-11-12:

* `rankings.rankings`: 1,777 rows; 1,772 are `quarterly` / `2026-Q4`; latest snapshot has 166 items:
  158 retired, 4 not running, 4 paper running.
* The three learning personas (衡岳 R2, 澄川 R2, 青禾 R3) all score **62.5**, tier-3, with identical
  components `pnl_score 100, risk_score 95, execution_score 0, activity_score 25`, although their PnL is
  12,880 / 207 / 4,730. `fill_rate` is 0.0 with one trade each and `drawdown` is 0.0;
  `target_weight` and `current_weight` are empty; `evidence_coverage` 0.1.
* Capital allocation authority store: `allocations` 0, `rebalances` 0, `containments` 0. No rebalance proposal
  has ever been created on hosted.

So ranking is computed continuously, but the score does not separate personas and no allocation cycle has run.
Why `pnl_score` saturates, why `fill_rate` and `drawdown` read 0, and whether any scheduler triggers the
quarterly cycle were **not** investigated here; they are the first task in §4.

## 3. How the pool design connects

* Ranking stays per persona, on the persona's combined account. Pool members are never ranked in the league.
* Capital between personas changes only through the existing quarterly proposal; within paper it uses the
  existing paper simulation allocation. The coordinator's earlier "monthly review, 20 % step" proposal is
  withdrawn.
* Proposed (needs operator approval, O-21): feed pool health into the existing factors of the formula —
  `risk_budget_factor` from the persona's risk-budget utilisation and `capacity_factor` from whether the
  persona could use more capital (budget full and admitted candidates waiting) — so an under-used persona is
  not given more capital.

## 4. Tasks to open

| Task | Scope |
| --- | --- |
| `RANKING-SCORE-DISCRIMINATION-AUDIT` | find why the three personas score identically (score saturation, `fill_rate` 0, `drawdown` 0, coverage 0.1), which inputs are wrong, and whether a quarterly allocation cycle is ever triggered; report before any formula change (formula changes need governance evidence) |
| `PAPER-ALLOCATION-QUARTERLY-RUN` | make the paper simulation allocation run each quarter from the ranking snapshot and produce a proposal visible in management |
| `ALLOCATION-POOL-HEALTH-FACTORS` | O-21, only if approved |

## 5. Open question

| # | Question | Recommendation |
| --- | --- | --- |
| O-21 | Use pool risk-budget utilisation and capacity as the existing `risk_budget_factor` and `capacity_factor` | yes |

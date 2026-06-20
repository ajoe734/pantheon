# Agora 跨 Repo 未決設計收斂決議

> 日期：2026-06-20  
> 依據：Agora 跨 Repo SA/SD 與「需系統設計團隊繼續完成的未決項目」  
> 決議：A1–A4、B1–B3、C1–C4 均已收斂為可驗收規格；D1–D2 已完成 P3 設計但不進前幾波執行。

---

## 1. 決議原則

1. 不把產品／量化／隱私空白丟給 autoworker 臆測。
2. Gate 只阻擋不可逆啟用，不阻擋 schema、validator、UI、PoC、測試與 evidence 開發。
3. 既有 Pantheon/OpenClaw/LEAN/Registry/Research 能力優先重用，不建立平行引擎。
4. 前後端共用同一 schema/catalog/policy version。
5. 所有 score、proxy、Dashboard、learning 與 Alpha contribution 都可追溯、版本化、可回滾。

---

## 2. 收斂結果

| 項目 | 決議 | 下游狀態 |
|---|---|---|
| A1 NBQ | 固定五因子、四 penalty、mandatory override、55 分門檻與 golden cases | `AG-BE-SW-003` 可派 |
| A2 Candidate Score | 版本化 per-strategy recipe、decomposition、winner-branch default | `AG-BE-CP-001`、`AG-FE-TR-002` 可派 |
| A3 Widget/Chart | 42 個首發 Widget、13 種 chart grammar、data/interaction allowlist | `AG-BE-DB-001`、`AG-FE-DB-001` 可派 |
| A4 human_actual | 明確 verified/imported/paper_proxy/manual/no-trade，CI 用 deterministic replay | `AG-BE-SH-002`、`AG-E2E-SH-001` 可派 |
| B1 Information Lead | 只允許公開資料統計 proxy；禁止違法/身份斷言；production 需法遵 sign-off | 工程可派；啟用 gate 保留 |
| B2 Institutional Privacy | Private/Institutional/Alpha 三域；k/l、重建風險與 consent policy | `AG-BE-EV-001` 可派 |
| B3 Alpha Governance | state gates、OOS/cost/capacity 門檻與 approver roles | `AG-BE-AL-001`、`AG-E2E-AL-001` 可派 |
| C1 OpenClaw Skills | 9 個 skill 的 purpose/IO/tools/failure/eval 已凍結 | 相關 agent tasks 可派 |
| C2 Persona Schema | Phase 1 metadata，客觀門檻觸發 additive normalization | watch + migration task 可排 |
| C3 Monorepo | npm workspaces 漸進遷移，先雙 entry/build，再 packages/apps | foundation task 可派 |
| C4 Dev Data/Signal | fixture→historical→sandbox 三層，沿用 Artifact/LEAN paper | E2E task 可派 |
| D1 Plugin Pipeline | P3 pipeline 已定，不影響 V1 renderer | 後期派 |
| D2 Aggregate Learning | 只學共通能力，依 B2 cohort/privacy | 後期派 |

---

## 3. 仍保留的人類決策

以下不是規格空白，只是 activation/sign-off：

- B1 法遵／法律顧問 production sign-off。
- 特定資料供應商 license sign-off。
- 使用者 institutional／Alpha contribution opt-in。
- Desk/global Alpha 最終 approver 決策。

工程團隊現在即可完成所有前置實作。

---

## 4. 文件權威順序

```text
本 Closure Pack 的細項規格
> 2026-06-20 Agora SD 中尚未具體化的段落
> Execution task 自行推定
```

若與 Pantheon L1 canonical policy 衝突，仍以 L1 為準並回報設計團隊，不得自行覆寫。

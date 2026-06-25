# Dispatch Unblock Matrix

> 目的：將已收斂設計映射回 SA/SD task，避免已具規格的工作繼續被錯誤標 blocked。

| Task | 依據文件 | 現在可派？ | 仍需注意 |
|---|---|---:|---|
| AG-BE-SW-003 | A1 + C1 completeness skill | 是 | 使用 golden cases 驗收 |
| AG-BE-CP-001 | A2 schema/default recipe | 是 | recipe 需版本化 |
| AG-FE-TR-002 | A2 UI decomposition | 是 | 不可只顯示單一分數 |
| AG-BE-DB-001 | A3 schemas/catalog + C1 dashboard skill | 是 | 前後端 registry checksum 一致 |
| AG-FE-DB-001 | A3 catalog/schemas | 是 | 首發只渲染 registry active widgets |
| AG-BE-SH-002 | A4 + C4 | 是 | dev label 必須是 paper proxy |
| AG-E2E-SH-001 | A4 + C4 | 是 | Mode A deterministic replay |
| Winner-branch Information Lead | B1 | 是（工程） | Production activation 需法遵 sign-off |
| AG-BE-EV-001 institutional path | B2 + C1 shadow/personalization | 是 | Private writeback 與 institutional 分流 |
| AG-BE-AL-001 | B3 | 是 | 預設 private、明確 opt-in |
| AG-E2E-AL-001 | B3 | 是 | 證明 default private |
| AG-BE-RS-003 | C1 expert-consult | 是 | 最小化 ContextBundle |
| AG-BE-RS-004 | C1 result-synthesis | 是 | evidence grounded |
| AG-XR/FE foundation | C3 | 是 | 先雙 entry/build，不大搬檔 |
| AG-E2E-TR-001 | C4 | 是 | fixture replay 足以開始 |
| Persona columns migration | C2 | 條件式 | 先加觀測；觸發門檻後執行 |
| Real custom widget plugin | D1 | 後期 | V1 不需等待 |
| Cross-user aggregate learning | D2 | 後期 | 依 B2 opt-in/cohort |

---

## 建議 Dispatch Wave

### Wave U1 — 立即

```text
AG-BE-SW-003
AG-BE-CP-001
AG-BE-DB-001
AG-FE-DB-001
AG-FE-TR-002
C1 skill implementation/eval tasks
```

### Wave U2 — 可平行

```text
AG-BE-SH-002
AG-E2E-SH-001
AG-E2E-TR-001
C4 fixture/data/signal tasks
```

### Wave U3 — Privacy/Governance implementation

```text
B1 policy validator/UI labels
AG-BE-EV-001 institutional path
AG-BE-AL-001
AG-E2E-AL-001
```

### Watch/P3

```text
C2 normalization migration
C3 full apps/packages relocation
D1 plugin pipeline
D2 aggregate learning rollout
```

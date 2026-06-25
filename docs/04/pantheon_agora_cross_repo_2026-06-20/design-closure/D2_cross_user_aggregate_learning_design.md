# D2 — 跨使用者 Aggregate Learning 設計

> 狀態：P3 Design Frozen；依賴 B2 privacy model  
> 原則：跨使用者只學「共通能力模式」，不學回可識別的私人策略。

## Allowed Learning Targets

```text
哪類追問更有效
哪種工具路由成功率較高
哪個研究順序節省時間
哪種風險檢查常被證明有用
哪類 Widget 對特定策略家族有效
僕人回覆格式偏好
```

禁止：

```text
完整私人 StrategySpec
精確交易參數
單一交易員 symbol/rule sequence
原始 prompt/journal
未授權 Alpha
```

## Cohort Builder

依 B2：global k>=10、desk k>=5、單人占比<=20%、時間與 quasi-identifier generalization。

## Model/Policy Outputs

```text
Shared Skill Candidate
QuestionScoringPolicy Candidate
ToolRoutingPolicy Candidate
Dashboard Template Candidate
RiskCheck Template Candidate
```

不是直接 persona overwrite 或 live policy。

## Evaluation

- Offline replay on held-out users。
- No degradation for minority styles。
- Privacy tests。
- Shadow opt-in rollout。
- Per-user override always wins。

## Writeback

```text
institutional candidate
→ eval
→ governance
→ versioned shared skill/policy
→ optional servant adoption
```

個人交易僕人可拒絕或覆蓋 shared default，不得被秘密強制套用。

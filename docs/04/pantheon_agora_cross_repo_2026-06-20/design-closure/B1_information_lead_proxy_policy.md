# B1 — Information Lead Proxy 法遵與呈現政策

> 狀態：Engineering Policy Frozen v1.0；Production 啟用前需法遵／法律顧問簽核  
> 阻擋解除：贏家分點策略 Information Lead Proxy、事件領先 Widget、相關 BFF／artifact  
> 原則：系統只可描述公開資料中的統計關聯與時間領先現象，不得斷言任何人知悉內部消息、從事內線交易、操縱市場或其他違法行為。

---

## 1. 允許的分析物件

```text
information_lead_proxy
pre_event_activity_score
temporal_lead_association
public_event_alignment
branch_party_association_probability
unusual_activity_indicator
statistical_relation
```

這些均是「研究 proxy」，不是身份、主觀意圖或違法行為認定。

---

## 2. 禁止輸出

除非引用主管機關、法院或正式公開處分文件，系統不得輸出：

```text
內線交易
知情交易
操縱股價
對倒
共謀
非法帳戶
特定關係人就是特定分點
```

亦不得使用同義斷言，例如「這個分點一定事先知道消息」。

允許替代語：

```text
公開資料顯示統計上的事件領先關聯
可能存在資金遷移或關聯分點反向流
關係人—分點映射信賴值為 X，仍需人工驗證
樣本呈現異常活動，但不能據此判定違法或主觀知情
```

---

## 3. 資料來源限制

只允許：

- 交易所／櫃買中心公開成交、分點或市場資料。
- 公司依法公開的重大訊息、財報、法說、併購公告。
- 公開的董監事／關係人持股與公司登記資料。
- 經授權的市場資料供應商資料。
- 正式主管機關、法院、執法或交易所處分公開紀錄。

禁止：

- 非法取得、洩漏或未授權的內部資料。
- 私人通訊內容。
- 未經授權的個資或帳戶識別資料。
- 來源不明的論壇爆料作為身份認定證據。

所有資料需具 `source_ref`、`available_at`、`license_scope`、`jurisdiction_profile`。

---

## 4. Proxy 計算最低條件

### Information Lead Proxy

至少需：

- 5 個獨立公開事件，且事件定義一致。
- 事件前後資料可得時間明確。
- 至少 60 個交易日的非事件基準期間。
- 資料 coverage ≥ 80%。
- 有 placebo／對照期間。
- 有成本與市場整體變動控制。

不符合者只顯示 `insufficient_evidence`，不得輸出數值 ranking。

### Branch–Party Association Probability

至少需：

- 3 個以上獨立持股變化／事件窗口。
- 2 種以上證據來源，例如持股變化同步、分點行為共現、歷史交易模式。
- 不得只憑單日共現建立高信賴映射。

---

## 5. Confidence Bands

| 值 | UI label | 規則 |
|---:|---|---|
| < 0.35 | 證據不足 | 不做 ranking |
| 0.35–0.54 | 低度關聯 | 僅供研究 |
| 0.55–0.74 | 中度關聯 | 必須顯示限制 |
| 0.75–0.89 | 高度統計關聯 | 仍不得稱為確認身份／知情 |
| ≥ 0.90 | 強統計關聯 | 必須人工複核，除非有正式公開文件，仍不得自動稱確認 |

系統永遠不使用 `confirmed insider` 或 `confirmed illegal` 狀態。

---

## 6. 強制 Disclaimer

每個相關 Widget、報告與 artifact 都必須顯示：

> 本分析僅依公開或已授權資料建立統計關聯與事件領先 proxy，不構成身份認定、違法行為判斷、投資建議或法律結論。相關關聯可能由共同市場因素、資料缺漏、分點遷移或其他未觀測因素造成。

Detail view 另顯示：

- 資料範圍。
- 事件數。
- coverage。
- confidence interval。
- placebo 結果。
- 主要限制。

---

## 7. UI 命名

允許 Widget 名稱：

```text
事件領先關聯
異常活動觀察
關係人—分點概率映射
公開訊息前活動分布
```

禁止 Widget 名稱：

```text
內線分點
知情分點
操縱網路
違法帳戶
```

---

## 8. Suppression Rules

遇下列情形直接 suppress：

- 事件日期或公開時間不可靠。
- 事件樣本不足。
- 來源 license 不允許衍生分析。
- 可能顯示自然人或私人帳戶身份。
- 關聯結果高度依賴單一股票／單一事件。
- 模型無法排除未來資料洩漏。
- 使用者要求系統斷言違法。

Suppression output 必須有 typed reason code。

---

## 9. Human Review

以下輸出需 review flag：

- confidence ≥ 0.75。
- 涉及自然人或關係人映射。
- 可能進入 desk/global Alpha contribution。
- 將被 Management、外部報告或多人共享。

Private Agora research 可以產生中低信賴 proxy，但不得突破上述命名與呈現限制。

---

## 10. Audit

保留：

```text
query definition
source refs
data cutoff
model/version
sample count
placebo result
confidence calculation
rendered disclaimer
review status
```

不得把 raw private prompt 放入 audit event。

---

## 11. Production Gate

工程與測試可依本政策立即進行；正式對使用者開啟前需：

- 法遵／法律顧問 sign-off。
- 適用法域 profile 確認。
- UI disclaimer 審核。
- 資料供應商衍生使用權確認。

Gate 只阻擋 production activation，不阻擋 schema、validator、UI、測試與 evidence 開發。

---

## 12. Definition of Done

- 禁止詞與替代詞規則可機器驗證。
- Proxy 最低樣本、coverage、PIT、placebo gate 已實作。
- Widget／artifact 強制 disclaimer。
- confidence band 不等同身份確認。
- suppression 與 review flag 有 typed reason。
- 只有公開／已授權資料可進計算。

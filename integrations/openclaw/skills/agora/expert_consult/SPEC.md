# Skill — agora-expert-consult

> Pantheon-side implementation of the C1 design-closure skill.
> Canonical SPEC source: `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/skills/agora/expert-consult/SPEC.md`
> Implementation: `integrations/openclaw/skills/agora/expert_consult/skill.py`
> Task: AG-BE-RS-003
> Depends on: AG-BE-ID-004 (ContextBundle privacy boundary)

---

## Purpose

利用既有 OpenClaw multi-agent session，為私人交易僕人建立最小化 consult／committee／red-team ContextBundle，並收集中央專業人格回覆。

---

## Input

```ts
type ExpertConsultInput = {
  strategySpecRef: string;       // → strategy_spec_ref
  question: string;
  relevantSymbols: string[];     // → relevant_symbols
  evidenceRefs: string[];        // → evidence_refs
  dataCutoff: string;            // → data_cutoff
  requiredExpertise: string[];   // → required_expertise
  mode: "consult"|"committee"|"red_team";
  privateFieldsAllowed: string[]; // → private_fields_allowed (cannot include forbidden fields)
};
```

---

## Output

```ts
type ExpertConsultOutput = {
  consultGroupId: string;        // → consult_group_id
  sessionRefs: string[];         // → session_refs
  memos: Array<{
    personaId: string;           // → persona_id
    memoRef: string;             // → memo_ref
    conclusion: string;
    confidence: number;
    evidenceRefs: string[];      // → evidence_refs
  }>;
  disagreements: unknown[];
  missingEvidence: string[];     // → missing_evidence
  privacyManifest: {
    rawPromptIncluded: false;    // → raw_prompt_included (always False, Literal)
    userIdentityIncluded: false; // → user_identity_included (always False, Literal)
    fieldsShared: string[];      // → fields_shared
  };
  status: "completed"|"needs_user"|"blocked"|"failed";
  blockingReasons: string[];     // → blocking_reasons
  warnings: string[];
};
```

---

## Rules

- 使用 OpenClaw `consult`、`committee`、`red_team` sessions（不建立新 agent runtime）。
- 只分享 StrategySpec ref、問題、必要 symbols、evidence、cutoff。
- 不分享 raw prompt、完整 Journal、其他策略、使用者身份。
- 中央 persona 只能回 Memo/Evidence/Critique/RiskNote，不直接寫私人記憶。
- disagreement 必須保留，不可由僕人偷偷消除。
- ContextBundle 透過 `build_context_bundle()`（AG-BE-ID-004）建立：
    - `raw_prompt_included = False`（Literal[False]，不可覆寫）
    - `user_identity_included = False`（Literal[False]，不可覆寫）
    - 11 欄位禁止清單（`_FORBIDDEN_FIELD_NAMES`）
- B1 政策：輸出文本不得斷言內線交易、操縱或非法行為。
    - 違反者：memo 被 suppress 並以 POLICY_SUPPRESSED warning 回報。
    - 替代語言：「公開資料顯示統計上的事件領先關聯」等。
    - 強制 disclaimer 附在每份受影響的 warning。
- 不得自創 schema/欄位/route、不得擴張 capability allowlist。
- 不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。

---

## Privacy Boundary

Per AG-BE-ID-004:

| Field | Allowed in ContextBundle |
|---|---|
| strategy_spec_draft_ref | ✓ ref only, never raw content |
| question | ✓ |
| symbols | ✓ |
| evidence_refs | ✓ ref IDs only, never raw content |
| data_cutoff | ✓ |
| required_output_schema | ✓ |
| raw_prompt | ✗ RAW_PRIVATE_CONTENT_FORBIDDEN |
| user_identity / user_id / user_email | ✗ RAW_PRIVATE_CONTENT_FORBIDDEN |
| private_journal / journal_entries | ✗ RAW_PRIVATE_CONTENT_FORBIDDEN |
| session_history / conversation_history | ✗ RAW_PRIVATE_CONTENT_FORBIDDEN |
| capital_binding | ✗ RAW_PRIVATE_CONTENT_FORBIDDEN |
| pii | ✗ RAW_PRIVATE_CONTENT_FORBIDDEN |

---

## B1 Information-Lead Proxy Policy

Per `design-closure/B1_information_lead_proxy_policy.md`:

Forbidden output terms (raises B1PolicyViolationError → POLICY_SUPPRESSED):
- 內線交易, 知情交易, 內線, 操縱, 操縱股價, 對倒, 共謀, 非法
- insider, insider trading, illegal, manipulation, confirmed illegal, confirmed insider
- 特定關係人就是特定分點

Required disclaimer on any information-lead output:
> 本分析僅依公開或已授權資料建立統計關聯與事件領先 proxy，不構成身份認定、違法行為判斷、投資建議或法律結論。相關關聯可能由共同市場因素、資料缺漏、分點遷移或其他未觀測因素造成。

---

## Golden Evals (C1 SPEC §6)

### Eval 1 — Winner branch consult
Input: five-path consult (chips/stats/compliance/risk/red-team expertise paths).  
Expected: five memos, no B1 violations, privacy manifest clean, disagreements preserved.

### Eval 2 — Privacy boundary
Input: bundle with any forbidden field (raw_prompt, user_identity, etc.).  
Expected: status="blocked", blocking_reason="RAW_PRIVATE_CONTENT_FORBIDDEN", zero memos.

### Eval 3 — Expert unavailable (degraded)
Input: valid input, session_adapter=None.  
Expected: status="blocked", blocking_reason="EXPERT_UNAVAILABLE", memos=[], no memo forged,  
missing_evidence lists all required_expertise entries.

---

## Failure Codes

Per C1 SPEC §5 common failure codes:

| Code | Trigger |
|---|---|
| `RAW_PRIVATE_CONTENT_FORBIDDEN` | Forbidden field in ContextBundle |
| `EXPERT_UNAVAILABLE` | No session adapter or persona unavailable |
| `B1_POLICY_SUPPRESSED` (warning) | Memo text contains forbidden assertion |
| `POLICY_SUPPRESSED` | Capability denied per capability snapshot |
| `INSUFFICIENT_EVIDENCE` | evidence_refs empty when required |
| `CONTEXT_SCOPE_VIOLATION` | Cross-user scope attempt |

Failure result 不得以自然語言成功答案掩蓋錯誤。

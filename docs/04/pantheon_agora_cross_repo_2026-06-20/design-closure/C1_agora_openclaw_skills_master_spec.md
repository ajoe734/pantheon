# C1 — Agora OpenClaw Skills Master Spec

> 狀態：Design Frozen v1.0  
> 阻擋解除：`AG-BE-SW-003`、`AG-BE-RS-003`、`AG-BE-RS-004`、`AG-BE-DB-001` agent quality  
> 實作位置：`integrations/openclaw/skills/agora/<skill>/SPEC.md`  
> 原則：使用既有 OpenClaw skills/session/tools/workflows；不建立新 agent runtime。

---

## 1. Skill Inventory

```text
agora-strategy-dialogue
agora-strategy-completeness
agora-research-planning
agora-expert-consult
agora-result-synthesis
agora-dashboard-compose
agora-shadow-review
agora-personalization
agora-journal-replay
```

---

## 2. Common Invocation Envelope

```json
{
  "skill_call_id": "sc_...",
  "skill_version": "1.0.0",
  "session_id": "...",
  "session_type": "interactive|trainer|research_task|consult|committee|red_team|background_job",
  "user_scope_ref": "...",
  "servant_persona_id": "...",
  "trace_id": "...",
  "request_id": "...",
  "context_bundle_ref": "...",
  "data_cutoff": "ISO-8601",
  "payload": {}
}
```

## 3. Common Result Envelope

```json
{
  "status": "completed|needs_user|blocked|failed",
  "output_schema": "...",
  "result": {},
  "result_ref": null,
  "evidence_refs": [],
  "warnings": [],
  "blocking_reasons": [],
  "tool_invocations": [],
  "memory_candidates": [],
  "audit": {
    "trace_id": "...",
    "skill_version": "1.0.0",
    "input_checksum": "...",
    "output_checksum": "..."
  }
}
```

---

## 4. Common Hard Rules

所有 skill：

- 必須尊重 session capability snapshot。
- 不得直接寫 RuntimeBinding、capital binding、broker order 或 live enable。
- 不得跨 user scope。
- 不得把 raw private prompt 送中央 persona，除非本次明確授權。
- 研究／consult／dashboard／memory output 先是 proposal/candidate，不是自動治理真相。
- 所有引用需 evidence ref／source ref。
- 無證據時明確標示 uncertain，不得補造。
- 所有 patch 需 base version 與 JSON Patch／typed delta。

---

## 5. Common Failure Codes

```text
INPUT_SCHEMA_INVALID
CONTEXT_SCOPE_VIOLATION
CAPABILITY_DENIED
SOURCE_UNAVAILABLE
INSUFFICIENT_EVIDENCE
CONFLICT_UNRESOLVED
TOOL_TIMEOUT
TOOL_OUTPUT_INVALID
POLICY_SUPPRESSED
REGISTRY_VERSION_MISMATCH
WIDGET_SPEC_INVALID
NO_ACTIONABLE_CHANGE
```

Failure result 不得以自然語言成功答案掩蓋錯誤。

---

## 6. Evaluation Policy

每個 skill 至少維護：

- 3 個 golden cases。
- 1 個 privacy/scope failure case。
- 1 個 tool failure/degraded case。
- deterministic schema validation。
- evidence completeness check。

Prompt/skill 更新需：

```text
offline eval
→ regression suite
→ shadow sessions
→ reviewed skill version
→ OpenClaw shared skill update
```

每個 skill 詳細規格見子目錄。

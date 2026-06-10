# Upstream FE Requirement — Management AI 對話持久化 (verbatim)

> Archived 2026-06-03. This is the FE-view requirement as received from the
> Pantheon BFF / OpenClaw team, preserved unedited as the source of truth for
> the BE-view spec (`MANAGEMENT_AI_PERSISTENCE_GAP_SPEC.md`) in this directory.

---

BE Requirement — Management AI 對話持久化
Date: 2026-06-03
Owner: Pantheon BFF / OpenClaw team
Priority: P0（阻擋多回合對話正確性）
Scope: /bff/management/nl/ask + /bff/management/ai/conversations/{sessionId}
FE 狀態: FE 已送 sessionId + conversation.recentTurns + summary + attachments，但僅當 hint，非 source of truth。

## 1. 問題

目前 FE → BFF → OpenClaw 的對話歷史完全靠 FE 每次 request 帶 conversation.recentTurns 過去。問題：

- FE recentTurns 有 char-budget（~32KB），長對話會被截窗，LLM 看不到早期內容。
- 多端 / 換瀏覽器 / 清快取 → FE 沒歷史可送，OpenClaw 等於每次重開。
- GET /bff/management/ai/conversations/{sessionId} 實測常常回 {turns: []}（stale cache / 沒寫入），證明 BE 沒在落地。
- Audit / 合規 也需要 server-side 留底，不能靠 client。

## 2. 需求

### 2.1 POST /bff/management/nl/ask 行為合約

每次收到 request：

1. 若 body 沒帶 sessionId → 建立新 session row，回 canonical sessionId。
2. 把這一輪 user message（含 attachments、ui snapshot）寫進 management_ai_turns。
3. 組 provider context 時，從 DB 讀完整 session 歷史（不是只用 FE 送來的 recentTurns）；recentTurns 只當 FE 視窗 hint，不取代 server truth。
4. 呼叫 OpenClaw / Codex。
5. 把 assistant response（含 provider_status、trace_id、ui_actions）寫回 management_ai_turns。
6. 回 { sessionId, traceId, providerStatus, answer, uiActions, auditLog }。

Idempotency：若 Idempotency-Key header 重複 → 回上次同筆結果，不重複寫 turn。

### 2.2 GET /bff/management/ai/conversations/{sessionId} 行為合約

- 回該 session 從建立以來所有 turn，依 created_at 升序。
- Turn shape：
```json
{
  "id": "turn_...",
  "role": "user" | "assistant" | "system",
  "text": "...",
  "createdAt": "ISO-8601",
  "providerStatus": { ... } | null,
  "attachments": [
    { "kind": "image", "mimeType": "...", "filename": "...", "sizeBytes": 12345, "url": "https://..." }
  ]
}
```
- Session 不存在 → 404，不要回 {turns:[]} 假裝成功（FE 會分不出 stale 跟空 session）。
- 權限：只回 owner / tenant 內可見的 session。

### 2.3 Attachment 處理

- FE 送 inline base64（attachments[].dataBase64）。
- BE 收到後寫進 object storage（GCS / S3），DB 只存 { kind, mimeType, filename, sizeBytes, storageUrl }。
- GET /conversations/{sessionId} 回的 attachments[].url 必須是 signed URL 或代理 URL（不要再回 base64）。
- 轉給 OpenClaw / Codex 時用 multimodal payload（image_url 或 base64，依 provider 要求）。

## 3. 建議 schema

```sql
CREATE TABLE management_ai_sessions (
  id            TEXT PRIMARY KEY,        -- e.g. mgmt-nl-xxxxxxxxxx
  owner_id      UUID NOT NULL,
  tenant_id     UUID,
  title         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE management_ai_turns (
  id              TEXT PRIMARY KEY,
  session_id      TEXT NOT NULL REFERENCES management_ai_sessions(id) ON DELETE CASCADE,
  role            TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
  text            TEXT NOT NULL DEFAULT '',
  attachments     JSONB NOT NULL DEFAULT '[]',
  provider_status JSONB,
  trace_id        TEXT,
  ui_snapshot     JSONB,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON management_ai_turns(session_id, created_at);
```

## 4. Acceptance（給 BE QA）

1. 連發 30 則 message 在同一個 sessionId 內，GET /conversations/{sessionId} 回 60 turns（30 user + 30 assistant）。
2. FE 清掉 localStorage 後重新打開同 sessionId，GET /conversations/{sessionId} 仍回完整歷史。
3. FE 故意把 conversation.recentTurns 砍到只剩最後 2 turns 送出，BE 回覆品質不變（證明 BE 用 server-side 歷史而不是 FE 視窗）。
4. 送圖片 → DB attachments 欄位存 storage URL，不是 base64；後續 GET /conversations 回 signed URL，瀏覽器可開。
5. 不存在的 sessionId → 404，不是 200 + {turns:[]}。
6. Idempotency-Key 重送 → 不會出現重複 turn。

## 5. FE 不變

- FE 繼續送 sessionId + recentTurns（最後 ~32KB）+ summary + attachments（base64）。
- FE localStorage 快取退化成 UX 加速層；BE 落地後就以 BE 為準。
- FE merge by turn.id；BE 回的 id 必須穩定。

## 6. Related

- 上游 spec：`.lovable/spec/management-2026-05-20/Pantheon_Management_Lovable_Spec_2026-05-20.md`
- FE 客戶端：`src/lib/bff-v1/managementAi.ts`、`src/management/components/agent/AgentPanelBody.tsx`
- 既有 BE write-gap SoT：`.lovable/specs/be-requirements/BE_WRITE_GAP_SPEC_2026-05-28.md`（這份是它的延伸 P0 項目）

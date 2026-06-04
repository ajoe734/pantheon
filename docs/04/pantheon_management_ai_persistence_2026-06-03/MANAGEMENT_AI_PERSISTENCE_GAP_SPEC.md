# Management AI Conversation Persistence — Closure Spec (BE View) — 2026-06-03

| | |
|---|---|
| **Doc ID** | `MGMT_AI_PERSISTENCE_SPEC_2026-06-03` |
| **Version** | 1.0 |
| **Date** | 2026-06-03 |
| **Author** | Pantheon Operator (BE-view rewrite of FE requirement) |
| **Audience** | BE / BFF owners (workers under `EPIC-MGMT-AI-PERSIST-*`) |
| **Priority** | P0 — blocks multi-turn conversation correctness |
| **Scope** | `POST /bff/management/nl/ask` + `GET /bff/management/ai/conversations/{sessionId}` |
| **Probe env** | `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io` |
| **Probe auth** | `Authorization: Bearer pantheon-dev-browser:reviewer` |
| **Upstream FE requirement** | `UPSTREAM_FE_REQUIREMENT_2026-06-03.md` (this dir) |
| **Current-state audit** | `CURRENT_STATE_AUDIT.md` (this dir) |
| **Parent SoT** | `docs/04/pantheon_bff_write_gap_2026-05-28/BFF_WRITE_GAP_SPEC.md` (this is its P0 extension) |
| **Code root** | `services/control-plane/bff/main.py` |

## 0. Why this exists

FE → BFF → OpenClaw conversation history relies entirely on the FE re-sending
`conversation.recentTurns` on every request. That window is char-budgeted (~32KB),
dies on cache clear / device switch, and is not auditable. The BFF was supposed to
be the server-side source of truth but today persists nothing durable — see
`CURRENT_STATE_AUDIT.md`. As a result:

- Long conversations lose early context (window truncation).
- Cross-device / cleared-cache sessions restart from zero.
- `GET /conversations/{sessionId}` returns `200 + {turns:[]}` and the FE cannot
  tell a stale/empty response from a real one.
- No compliance trail independent of the client.

This doc translates the FE-view requirement into BE-view tickets so workers can
land the persistence layer without re-reading the FE narrative.

## 1. Headline

| Severity | EPIC | Tasks |
|---|---|---|
| **P0 store/schema** | `EPIC-MGMT-AI-PERSIST-P0-STORE` | 1 |
| **P0 write path** | `EPIC-MGMT-AI-PERSIST-P0-WRITE` | 3 |
| **P0 read path** | `EPIC-MGMT-AI-PERSIST-P0-READ` | 1 |
| **P1 attachments** | `EPIC-MGMT-AI-PERSIST-P1-ATTACH` | 2 |
| **OPS redeploy + live verify** | `EPIC-MGMT-AI-PERSIST-OPS` | 1 |
| **Total** | — | **8** |

### Gap index

```
P0  STORE   Durable session+turn+idempotency store (replaces deque + /tmp jsonl)
P0  WRITE   nl/ask persists user+assistant turns (full text, no 400-char truncation)
P0  WRITE   provider context built from server-side history (recentTurns demoted to hint)
P0  WRITE   durable Idempotency-Key (replay returns original, zero duplicate turns)
P0  READ    GET conversations: store-backed, created_at ASC, 404 on miss, owner/tenant scope
P1  ATTACH  attachment ingest -> object storage; DB stores {kind,mime,filename,size,storageUrl}
P1  ATTACH  multimodal forward of attachments to OpenClaw/Codex provider
OPS         redeploy dev BFF + live-verify all 6 acceptance criteria (babysit rule)
```

## 2. Storage decision — RESOLVED: real Postgres (Option B), shared assistant substrate

> **Context:** the BFF today has no SQL driver (`requirements.txt` ships no
> psycopg / sqlalchemy / asyncpg); all existing state is JSON-file / in-memory
> `read_store`. SQLite-on-volume (A) and durable-JSONL (C) were considered and
> **rejected** in favour of real Postgres because this store is not a one-off:
> it becomes the **shared assistant-conversation substrate** that the future
> **Agora helper** assistant must also follow (with fewer features), and that
> requires multi-writer durability and proper per-surface account isolation that
> single-writer SQLite cannot give.

**Decision (operator, 2026-06-03):** **Option B — Postgres, built on the existing
platform foundation.** Postgres is already the platform standard: ~10 services
(`capital`, `governance`, `promotion`, `incident`, `search`, `consultation`,
`training-session`, `memory`, `policy-learning`, `source_ingestion`, …) use it, and
there is a shared module [`services/foundation/postgres_json_store.py`](../../../services/foundation/postgres_json_store.py)
(`PostgresJsonOwnerStore`) plus a posture gate
[`services/foundation/persistence_posture.py`](../../../services/foundation/persistence_posture.py).
The file/in-memory BFF is the anomaly; this work aligns it to the house pattern. We
**do not roll our own psycopg layer** — we reuse foundation primitives and
conventions.

### 2.1 Reuse the foundation, don't reinvent

- **Connection + bootstrap + identifier safety:** reuse `quote_pg_identifier`,
  schema-`CREATE IF NOT EXISTS`, and the lazy `import psycopg` pattern from
  `PostgresJsonOwnerStore` rather than re-implementing them.
- **Backend selector (matches every other service):** platform services default to
  `json`/`jsonl` backends in dev and **only enforce Postgres in staging/prod** via
  `persistence_posture.ENFORCED_MODES`. So add a `MANAGEMENT_AI_STORE_BACKEND`
  env (`json` for local/dev, `postgres` enforced in staging/prod) and register the
  BFF in `persistence_posture` so staging/prod refuses to start without a real DB.
  This keeps dev light and is exactly how `capital`/`governance`/etc. behave.
- **Store shape:** `PostgresJsonOwnerStore` is a single-table JSONB KV
  (`record_id` PK + `payload` + `updated_at`) — fine for the `sessions` record, but
  `turns` needs ordered listing by `created_at` and a session FK, which the KV does
  not give. So the `AssistantConversationStore` is a small dedicated module that
  **follows foundation conventions** (same connect/bootstrap/quoting, same
  service-owned-table + read-only-role rule, same posture gate) with the relational
  tables in §3. Reuse `PostgresJsonOwnerStore` directly where a KV suffices.

### 2.2 Shared substrate + separate accounts (= existing foundation convention)

`PostgresJsonOwnerStore` already documents the exact rule you asked for: *"the table
is intentionally service-owned; cross-service consumers should use the owning API
surface or a **read-only DB role**."* "分開帳號" is therefore a platform convention,
not an invention:

1. **One architecture, reused.** One `AssistantConversationStore` interface + one
   DDL template serve every surface. The Agora helper instantiates the same store
   against its own schema — it does not re-invent persistence.
2. **Separate accounts (isolation).** Each surface = its own **owner role + schema**:
   - `management_ai` schema, owner role `pantheon_management_ai` (this spec).
   - `agora_ai` schema, owner role `pantheon_agora_ai` (future helper).
   Each role is granted only its own schema; per-surface creds
   (`MANAGEMENT_AI_DATABASE_URL`, later `AGORA_AI_DATABASE_URL`). Cross-surface reads,
   if ever needed, go through a **read-only role** per the foundation rule — never a
   shared read-write account.

WRITE/READ tasks are **store-agnostic** against the `AssistantConversationStore`
interface, so the Agora helper reuses them by pointing at its own role/schema — no
handler rewrite.

> Sub-decision **RESOLVED (operator, 2026-06-03)**: separate accounts = **per-role +
> per-schema in one Postgres instance** (matches foundation). No separate
> database/instance per surface.

## 3. Schema (Postgres) — per-surface schema, shared DDL template

The DDL below is the **template** every assistant surface uses. Management AI
materializes it in the `management_ai` schema (role `pantheon_management_ai`); the
Agora helper will later materialize the same template in `agora_ai` (role
`pantheon_agora_ai`). Tables keep the FE-spec names so the contract is unchanged.

```sql
-- runs inside the surface's own schema (search_path = management_ai)
CREATE TABLE sessions (
  id          TEXT PRIMARY KEY,        -- canonical "mgmt-nl-xxxxxxxxxx"
  owner_id    TEXT NOT NULL,           -- operator_id from identity
  tenant_id   TEXT,                    -- caller tenant scope
  title       TEXT,                    -- nullable; first-question summary acceptable
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE turns (
  id              TEXT PRIMARY KEY,     -- stable "turn_..." (user) / "<msg>-assistant"
  session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role            TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
  text            TEXT NOT NULL DEFAULT '',   -- FULL text, no 400-char truncation
  attachments     JSONB NOT NULL DEFAULT '[]',-- array of attachment metadata (see §6)
  provider_status JSONB,                -- assistant turns only
  trace_id        TEXT,
  ui_snapshot     JSONB,                -- user turns: ui snapshot hint
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON turns(session_id, created_at);

CREATE TABLE idempotency (             -- durable replacement for the in-memory dict
  idempotency_key TEXT PRIMARY KEY,
  request_hash    TEXT NOT NULL,
  session_id      TEXT,
  response_json   JSONB NOT NULL,       -- original 202 body to replay verbatim
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

> The FE-spec names `management_ai_sessions` / `management_ai_turns` are satisfied
> as `management_ai.sessions` / `management_ai.turns` (schema-qualified). If an
> unqualified flat name is required for any external consumer, expose views.
> The `AssistantConversationStore` interface (session/turn/idempotency CRUD) is
> identical across surfaces; only the connection's role + search_path differ.
> Bootstrap (schema + table creation) reuses foundation's `quote_pg_identifier` +
> `CREATE … IF NOT EXISTS` pattern; no separate migration framework is introduced
> beyond what the other services already do.

## 4. Cross-cutting contract

- Auth: `Authorization: Bearer <jwt>`; `_extract_identity` + `_require_read_role` (unchanged). `owner_id = identity.operator_id`, `tenant_id = _mgmt_nl_caller_tenant(...)`.
- Idempotency: `Idempotency-Key` (RFC) preferred, `X-Idempotency-Key` compat alias — reuse `_resolve_final_idempotency_key` + `_reject_body_idempotency_key`.
- Response envelope unchanged: existing `nl/ask` returns `202 {status,data,meta}`; keep all current keys, add persistence-backed ones.
- High-risk refusal policy (`_mgmt_nl_high_risk_classify`) stays and still runs **before** any session create/write.
- Audit events (`_management_ai_record_event`) stay as a **debug/trace** sidecar; they are no longer the source of truth.
- Persistence goes through the `AssistantConversationStore` interface (Postgres-backed), which the future Agora helper reuses against its own role/schema (§2.1).

## 5. Endpoint contracts

### 5.1 `POST /bff/management/nl/ask` (rewrite — handler at `main.py:30467`)

Per request:
1. If body has no `sessionId`/`session_id` → create a session row, return canonical `sessionId`. If present and exists → reuse; if present but unknown → create with that id (FE owns the id).
2. Persist the user turn (role=`user`, **full** `text`, `attachments`, `ui_snapshot`, `created_at`) via the store — **remove the 400-char truncation** for stored text.
3. Build provider context from **store history** (all prior turns for the session, ordered), not from FE `recentTurns`. `recentTurns` becomes a hint only.
4. Call provider (`_mgmt_nl_maybe_provider_answer`) with history-augmented prompt.
5. Persist the assistant turn (role=`assistant`, full answer, `provider_status`, `trace_id`, `ui_actions`).
6. Return `{ sessionId, traceId, providerStatus, answer, uiActions, auditLog, ... }` (keep existing keys).

Idempotency: on duplicate `Idempotency-Key` with matching `request_hash` → replay the stored `response_json`, write **zero** new turns; mismatched hash → existing 409 behavior.

### 5.2 `GET /bff/management/ai/conversations/{sessionId}` (rewrite — handler at `main.py:30780`)

- Read all turns for the session from the store, ordered by `created_at` ASC.
- **404** `RESOURCE_NOT_FOUND` when the session row does not exist (NOT `200 + {turns:[]}`).
- Owner/tenant scope: only return sessions owned by caller / visible in caller tenant; foreign session → 404 (do not leak existence).
- Turn shape (FE contract):
  ```json
  { "id": "turn_...", "role": "user|assistant|system", "text": "...",
    "createdAt": "ISO-8601", "providerStatus": {…}|null,
    "attachments": [ { "kind","mimeType","filename","sizeBytes","url" } ] }
  ```
  Keep existing snake_case mirrors in `data` for back-compat; the camelCase keys above are required.

## 6. Attachments (P1)

- FE sends inline base64 `attachments[].dataBase64` (+ `kind`,`mimeType`,`filename`).
- On `nl/ask`: decode, upload to object storage (GCS bucket; reuse any existing GCS creds on the BFF VM), store **only** metadata `{kind,mimeType,filename,sizeBytes,storageUrl}` in the turn's `attachments` JSON. Never store base64 in the DB.
- `GET /conversations` returns `attachments[].url` as a **signed URL** (or a BFF proxy URL) — never base64.
- Forward to OpenClaw/Codex as a multimodal payload (`image_url` or base64 per provider need) when calling the provider.
- Enforce per-attachment + per-request size caps and an allowlist of `mimeType` (images first).

## 7. Acceptance (BE QA) — maps 1:1 to FE §4

1. 30 messages in one `sessionId` → `GET` returns **60 turns** (30 user + 30 assistant), full text, ASC order.
2. Clear FE localStorage, reopen same `sessionId` → `GET` still returns full history (survives a BFF pod restart between write and read).
3. FE sends only the last 2 `recentTurns` → answer quality unchanged (proves provider used server-side history, not the FE window).
4. Send an image → turn `attachments` stores a storage URL (not base64); subsequent `GET` returns a signed URL openable in a browser.
5. Nonexistent `sessionId` → **404** (not `200 + {turns:[]}`).
6. Replay `Idempotency-Key` → no duplicate turn; original response replayed.
7. (scope) Foreign-tenant `sessionId` → 404, no existence leak.

## 8. Dispatch plan

Sprint `2026-06-03-pantheon-mgmt-ai-persistence`. Owner split follows the
2026-05-24 delta 3-class pattern: P0 core → Codex/Claude; P1 attachments →
Codex2/Claude2; OPS → Codex/Claude. Dispatcher:
`scripts/dispatch_mgmt_ai_persistence_2026-06-03.py`.

| Task ID | EPIC | Owner / Reviewer |
|---|---|---|
| `MGMT-AI-PERSIST-P0-STORE-001` | STORE | Codex / Claude |
| `MGMT-AI-PERSIST-P0-WRITE-002` | WRITE | Codex / Claude |
| `MGMT-AI-PERSIST-P0-WRITE-003` | WRITE | Codex / Claude |
| `MGMT-AI-PERSIST-P0-WRITE-004` | WRITE | Codex / Claude |
| `MGMT-AI-PERSIST-P0-READ-005` | READ | Codex / Claude |
| `MGMT-AI-PERSIST-P1-ATTACH-006` | ATTACH | Codex2 / Claude2 |
| `MGMT-AI-PERSIST-P1-ATTACH-007` | ATTACH | Codex2 / Claude2 |
| `OPS-MGMT-AI-PERSIST-REDEPLOY-20260603` | OPS | Codex / Claude |

**Babysit rule** (`feedback_babysit_deploy_tasks`): do not mark the OPS task done
until all 7 acceptance criteria are verified by live curl against the dev BFF.

## 9. Open items

- **RESOLVED**: storage engine = Postgres (Option B), built on `services/foundation/postgres_json_store.py` + `persistence_posture` conventions; backend selector defaults to `json` in dev and enforces `postgres` in staging/prod (§2).
- **RESOLVED**: "separate accounts" = per-role + per-schema in **one** Postgres instance (matches foundation; no separate DB/instance per surface).
- OPS: provision the Postgres instance, the `management_ai` schema + `pantheon_management_ai`
  role + `MANAGEMENT_AI_DATABASE_URL` secret, and the GCS attachment bucket. Leave a
  documented path to add the `agora_ai` schema + `pantheon_agora_ai` role later.
- Agora helper (future, separate sprint): reuses `AssistantConversationStore` against
  its own role/schema with a trimmed feature set — not in this sprint's scope.
- FE contract is frozen (FE §5): FE keeps sending `sessionId + recentTurns + summary + attachments`; BE becomes source of truth; turn `id` must be stable for FE merge-by-id.

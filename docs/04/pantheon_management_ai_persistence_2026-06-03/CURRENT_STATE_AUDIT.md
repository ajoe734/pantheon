# Current-State Audit — Management AI Conversation Persistence

| | |
|---|---|
| **Doc ID** | `MGMT_AI_PERSISTENCE_AUDIT_2026-06-03` |
| **Date** | 2026-06-03 |
| **Auditor** | Operator (read-only code audit) |
| **Verdict** | **NOT DONE** — endpoints exist, but persistence is an ephemeral in-memory `deque` + `/tmp` JSONL audit log, not a durable store. Core P0 (server-side history feeding the provider, attachments, 404 contract) is missing. |
| **Probe env** | `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io` |
| **Code root** | `services/control-plane/bff/main.py` (FastAPI; ~19k lines) |

## 0. Headline

Both endpoints in scope are wired and emit audit events, but **none of the 6 FE
acceptance criteria pass durably**. "Persistence" today is:

- `_MGMT_AI_AUDIT_EVENTS = deque(maxlen=500)` — in-process, per-worker, capped at 500 events ([main.py:29337](../../../services/control-plane/bff/main.py#L29337))
- optional append-only `/tmp/pantheon-bff/management-ai-audit.jsonl` — ephemeral, lost on pod restart/redeploy ([main.py:29340-29439](../../../services/control-plane/bff/main.py#L29340))

There is **no DB**: the entire BFF has no SQL driver (`requirements.txt` = fastapi,
uvicorn, httpx, pydantic, python-multipart, python-jose, passlib, pytest,
jsonschema — no psycopg/sqlalchemy/asyncpg). All state is JSON-file / in-memory
`read_store`. The spec's `CREATE TABLE management_ai_sessions/turns` has no
backing engine in this service today. **This is the central architectural fork
(see GAP SPEC §2).**

## 1. Endpoint inventory

| Route | Handler | Status |
|---|---|---|
| `POST /bff/management/nl/ask` | [main.py:30467](../../../services/control-plane/bff/main.py#L30467) `bff_management_nl_ask` | exists; emits audit events, no durable turn write |
| `GET /bff/management/ai/conversations/{session_id}` | [main.py:30780](../../../services/control-plane/bff/main.py#L30780) `bff_management_ai_conversation` | exists; reconstructs turns from audit events; never 404s |
| `GET /bff/management/ai/audit` | [main.py:30746](../../../services/control-plane/bff/main.py#L30746) | exists (trace/debug only) |

## 2. Persistence backing

- `_management_ai_record_event` appends to the bounded deque + best-effort `/tmp` JSONL ([main.py:29420-29439](../../../services/control-plane/bff/main.py#L29420)).
- `_management_ai_conversation_turns` rebuilds turns by replaying `management_ai.exchange.accepted` (→ user) and `management_ai.exchange.completed` (→ assistant) events ([main.py:29537-29595](../../../services/control-plane/bff/main.py#L29537)).
- Stored `question`/`answer` are truncated to **400 chars** by `_management_ai_summary_value` before recording ([main.py:30641](../../../services/control-plane/bff/main.py#L30641), [main.py:30737](../../../services/control-plane/bff/main.py#L30737)) — so even within the window the content is lossy.
- Idempotency uses an in-memory dict `_MGMT_NL_IDEMPOTENCY: Dict[...] = {}` ([main.py:29251](../../../services/control-plane/bff/main.py#L29251)); replay records a `.replayed` event instead of re-recording turns ([main.py:30531-30544](../../../services/control-plane/bff/main.py#L30531)).

## 3. Acceptance-criteria mapping

| # | FE acceptance | Result | Evidence |
|---|---|---|---|
| 1 | 30 msgs → GET returns 60 turns | ⚠️ fragile | rebuilt from events; 400-char truncation; deque cap 500; single-process only |
| 2 | clear localStorage, reopen same sessionId → full history | ❌ | only `/tmp` JSONL; gone on restart/redeploy; no DB |
| 3 | FE sends only 2 recentTurns → quality unchanged (proves server history) | ❌ **core gap** | provider prompt is `question + focus + context_pack` only ([main.py:30229-30246](../../../services/control-plane/bff/main.py#L30229)); **no conversation history at all** — neither DB nor FE recentTurns is fed to the model. `recentTurns` is not read anywhere in main.py. Multi-turn correctness unsolved. |
| 4 | image → DB stores storageUrl; GET returns signed URL | ❌ | no attachment handling in `nl/ask`; no object storage; no signed URLs; base64 unprocessed |
| 5 | nonexistent sessionId → 404 | ❌ | GET always returns 200 + `turns: []` ([main.py:30801-30818](../../../services/control-plane/bff/main.py#L30801)) — exactly the anti-pattern the spec forbids |
| 6 | Idempotency-Key replay → no duplicate turn | ⚠️ | works in-process via dict; ephemeral, not shared across workers |

Additional: GET conversations enforces only `_require_read_role` — **no owner/tenant
scoping** on the session (spec §2.2) ([main.py:30788-30789](../../../services/control-plane/bff/main.py#L30788)).

## 4. Adjacent (non-applicable) code

`/bff/assistant/*` ([assistant/routes.py](../../../services/control-plane/bff/assistant/routes.py), [assistant/transcript_store.py](../../../services/control-plane/bff/assistant/transcript_store.py))
has session+transcript concepts but is the kernel/assistant lifecycle line, is
explicitly in-process only ("not shared across BFF workers… production should
replace with Redis/DB", [transcript_store.py:1-9](../../../services/control-plane/bff/assistant/transcript_store.py#L1)), and is **not** on the `/bff/management/*`
path. It does not satisfy this requirement and is out of scope except as prior art.

## 5. Conclusion

Only the endpoint shell + audit/replay events exist. The actual P0 — a durable,
DB-equivalent source of truth that (a) survives restart, (b) feeds full history to
the provider, (c) handles attachments to storage, (d) returns 404 for unknown
sessions, (e) scopes by owner/tenant — is **not implemented**. See
`MANAGEMENT_AI_PERSISTENCE_GAP_SPEC.md` for the BE-view closure plan.

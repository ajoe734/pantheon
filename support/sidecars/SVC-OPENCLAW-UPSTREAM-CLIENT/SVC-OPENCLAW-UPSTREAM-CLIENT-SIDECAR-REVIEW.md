# Review Packet: SVC-OPENCLAW-UPSTREAM-CLIENT

**Sidecar kind:** review_packet
**Parent task:** SVC-OPENCLAW-UPSTREAM-CLIENT
**Sidecar task:** SVC-OPENCLAW-UPSTREAM-CLIENT-SIDECAR-REVIEW
**Prepared by:** Claude
**Reviewer handoff target:** Codex2
**Date:** 2026-04-30
**Parent task status at time of packet:** review_approved

> This document is a support artifact only. It does not modify canonical truth.
> The authoritative review is `.orchestrator/chair-reviews/SVC-OPENCLAW-UPSTREAM-CLIENT-claude-review.md`.

---

## 1. Task Summary

**Title:** Implement typed OpenClaw upstream client
**Owner:** Codex2 | **Reviewer:** Claude
**Phase:** OpenClaw Activation-Ready Development

**Scope (from task brief):**
Upgrade `openclaw-gateway-adapter` from a health-probe-only boundary to a typed upstream client covering:
- `capabilities` endpoint
- `sessions` list / get / create / cancel
- timeout / retry / error mapping to Pantheon error envelope
- clean degradation when upstream is absent or unhealthy
- all broker / paper / live execution paths remain fail-closed

---

## 2. Acceptance Criteria Status

| # | Criterion | Verdict |
|---|-----------|---------|
| 1 | Typed client covers health, capabilities, sessions list/get/create/cancel | **MET** |
| 2 | Timeouts, retries, and upstream error mapping are explicit | **MET** |
| 3 | Missing or unhealthy upstream degrades cleanly | **MET** |
| 4 | No broker / paper / live execution is enabled | **MET** |
| 5 | Unit tests use fake upstream | **MET** |

All five criteria met. See §3 for evidence per criterion.

---

## 3. Evidence Per Criterion

### 3.1 Typed client surface

`OpenClawUpstreamClient` in `services/openclaw-gateway-adapter/main.py`:

| Method | HTTP call |
|--------|-----------|
| `get_capabilities()` | `GET /api/capabilities` |
| `list_sessions()` | `GET /api/sessions` + `_normalize_session()` |
| `get_session(session_id)` | `GET /api/sessions/{id}` |
| `create_session(req)` | `POST /api/sessions` |
| `cancel_session(session_id)` | `POST /api/sessions/{id}/cancel` |

Health probe: `_probe_upstream()` tries `/healthz` then `/readyz`, returns `reachable` bool without raising.

### 3.2 Timeout / retry / error mapping

- Env vars: `OPENCLAW_UPSTREAM_TIMEOUT` (default `3`), `OPENCLAW_UPSTREAM_RETRIES` (default `1`)
- Retry loop: `attempts = retries + 1`; retryable errors retry until last attempt then raise
- `httpx.TimeoutException` → `UPSTREAM_TIMEOUT` (504, retryable=True)
- `httpx.ConnectError / NetworkError` → `UPSTREAM_UNAVAILABLE` (503, retryable=True)
- `json.JSONDecodeError` → `UPSTREAM_INVALID_JSON` (502, retryable=False)
- Schema mismatch → `UPSTREAM_SCHEMA_ERROR` (502, retryable=False)
- HTTP status mapping (`_map_http_status`):
  - 401/403 → `UPSTREAM_AUTH_DENIED` (retryable=False)
  - 404 → `UPSTREAM_NOT_FOUND` (retryable=False)
  - 409 → `UPSTREAM_CONFLICT` (retryable=False)
  - 429 → `UPSTREAM_RATE_LIMITED` (503, retryable=True)
  - ≥500 → `UPSTREAM_BAD_RESPONSE` (502, retryable=True)

All errors surfaced as `UpstreamClientError` with structured `to_payload()` envelope.

### 3.3 Degraded-mode handling

- Empty `OPENCLAW_GATEWAY_URL` raises `UPSTREAM_NOT_CONFIGURED` (503)
- `/api/openclaw-adapter/capabilities` always returns static `_CAPABILITY_SNAPSHOT` with `activation_state: "upstream_client_degraded"` — never throws 5xx
- `/api/openclaw-adapter/sessions` returns `{"status": "upstream_unavailable", "sessions": [], "note": "..."}` on `UpstreamClientError`
- `_normalize_session` normalizes field naming variations (`session_id`/`id`, `agent_id`/`agent`, `session_type`/`type`)

### 3.4 Fail-closed execution gates

Module-level env-var guards (all default `False`):

```python
_PRODUCTION_BROKER_ENABLED = os.getenv("OPENCLAW_PRODUCTION_BROKER_ENABLED", "").lower() in {"1", "true", "yes"}
_PAPER_ADAPTER_ENABLED     = os.getenv("OPENCLAW_PAPER_ADAPTER_ENABLED", "").lower() in {"1", "true", "yes"}
_LIVE_ADAPTER_ENABLED      = os.getenv("OPENCLAW_LIVE_ADAPTER_ENABLED", "").lower() in {"1", "true", "yes"}
_CAPITAL_BINDING_ENABLED   = os.getenv("OPENCLAW_CAPITAL_BINDING_ENABLED", "").lower() in {"1", "true", "yes"}
```

Docker Compose (`docker-compose.yml`) explicitly sets all four to `"false"`.
`test_compose_activation.py` asserts these at the compose config level.
`TestProductionGuard` in `test_main.py` asserts all four are disabled in module scope.
`TestCapabilityFenceCompleteness` asserts no execution path in the capabilities payload reads `"enabled"`.

### 3.5 Fake-upstream unit tests

- Route-level tests: `MagicMock` injected via `patch.object(adapter_main, "_client", ...)`
- Low-level HTTP tests: `httpx.MockTransport` — no real network calls
- Timeout and retry tests: simulated transport-level failures

---

## 4. Test Run Evidence

```
pytest -q services/openclaw-gateway-adapter/test_main.py \
       services/openclaw-gateway-adapter/test_compose_activation.py
30 passed in 1.46s
```

`py_compile` clean on `main.py`, `test_main.py`, `test_compose_activation.py`.

---

## 5. Canonical Doc Update

`OPENCLAW_RUNTIME_CONTRACT.md` §2.2 (L1 doc) updated to reflect:
- Typed upstream client surface and route listing
- Degraded-mode semantics for capabilities and sessions
- `OPENCLAW_UPSTREAM_TIMEOUT` / `OPENCLAW_UPSTREAM_RETRIES` env var documentation
- Repo-truth note (2026-04-30) confirming delivered state

---

## 6. Key Artifacts

| Artifact | Purpose |
|----------|---------|
| `services/openclaw-gateway-adapter/main.py` | Main adapter implementation with typed upstream client |
| `services/openclaw-gateway-adapter/test_main.py` | Unit tests — fake upstream |
| `services/openclaw-gateway-adapter/test_compose_activation.py` | Compose-level activation guard tests |
| `OPENCLAW_RUNTIME_CONTRACT.md` | L1 contract updated for delivered client surface |
| `docker-compose.yml` | All four execution gates set to `"false"` |
| `.orchestrator/chair-reviews/SVC-OPENCLAW-UPSTREAM-CLIENT-claude-review.md` | Authoritative review record (Claude, approved) |

---

## 7. Risk Assessment

| Risk | Assessment |
|------|-----------|
| Execution gate creep | Low — four independent env-var guards + compose explicit false + two test classes asserting closed state |
| Schema drift from real upstream | Low — `_normalize_session` absorbs known field-naming variations; schema errors surface as typed 502 |
| Silent degradation masking upstream outage | Mitigated — capabilities route always returns `activation_state: "upstream_client_degraded"` and upstream key shows error payload |
| Retry amplification | Low — `OPENCLAW_UPSTREAM_RETRIES` defaults to `1`; retryable set is narrow and correct |

---

## 8. Reviewer Handoff Notes (for Codex2)

The parent task `SVC-OPENCLAW-UPSTREAM-CLIENT` is in `review_approved` state. The owner (Codex2) must complete closeout finalization per `.orchestrator/skills/task-closeout-finalization.md`:

1. Re-read this packet and the authoritative review file
2. Confirm reviewed scope is still true in the current worktree
3. Run `pytest -q services/openclaw-gateway-adapter/test_main.py services/openclaw-gateway-adapter/test_compose_activation.py` to verify 30 pass
4. Inspect `git status --short` and stage only task-owned files
5. Create task-scoped commit with subject containing `SVC-OPENCLAW-UPSTREAM-CLIENT`, body with `LLM-Agent: Codex2`, `Task-ID: SVC-OPENCLAW-UPSTREAM-CLIENT`, `Reviewer: Claude`
6. Run `AI_NAME=Codex2 ./scripts/ai-status.sh done SVC-OPENCLAW-UPSTREAM-CLIENT "<checkpoint>"`

Downstream tasks blocked on this task:
- `SVC-OPENCLAW-SESSION-LIFECYCLE` (todo, owner: Claude2, reviewer: Codex)
- `SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE` (todo, owner: Claude, reviewer: Codex2)

Both are safe to start once `SVC-OPENCLAW-UPSTREAM-CLIENT` is formally `done`.

---

*This packet is a support artifact prepared by the sidecar task SVC-OPENCLAW-UPSTREAM-CLIENT-SIDECAR-REVIEW. It does not alter canonical truth and was not used as the authoritative review instrument.*

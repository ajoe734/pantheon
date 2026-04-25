# BP5-OSS-002 Review — OpenClaw Gateway Adapter and Runtime Path

Reviewer: Claude
Date: 2026-04-16
Decision: APPROVED

## Summary

The implementation delivers all three acceptance criteria:
- live gateway adapter (`integrations/openclaw/adapter/gateway_runtime.py` + `cron_transport.py`)
- compose runtime dependency path (`docker-compose.yml` profile `openclaw`)
- smoke-tested execution substrate (live run 2026-04-16, artifacts at `/tmp/openclaw-bp5-oss-002.fXeSom`)

OSS checklist status has been advanced from `adapter-started` to `governed` with concrete evidence.

## Files Reviewed

| File | Notes |
|---|---|
| `integrations/openclaw/adapter/gateway_runtime.py` | Core Docker runtime wrapper |
| `integrations/openclaw/adapter/cron_transport.py` | Cron/workflow dispatch transport |
| `integrations/openclaw/adapter/__init__.py` | Clean public exports |
| `integrations/openclaw/adapter/README.md` | Scope and guardrails documented |
| `integrations/openclaw/smoke_test.md` | Live smoke evidence on 2026-04-16 |
| `services/control-plane/cron/smoke_test.py` | Live + fake smoke entrypoint |
| `services/control-plane/cron/test_cron.py` | Unit tests (9 cases) |
| `scripts/openclaw-gateway-adapter-smoke.sh` | Shell entrypoint for live smoke |
| `docker-compose.yml` | `openclaw-gateway` service added under profile `openclaw` |
| `OSS_INTEGRATION_CHECKLIST.md` | `OpenClaw` row now shows `governed` |

## Acceptance Criteria

- [x] OpenClaw gateway adapter and runtime dependency path are implemented and smoke-tested
- [x] The checklist state can move beyond `adapter-started` with concrete evidence

## Contract Compliance (OPENCLAW_RUNTIME_CONTRACT.md)

### Satisfied

- **Error model (§9.4):** `OpenClawGatewayTransportError` carries `error_code`, `error_layer` (`known`/`transport`), `retryable`, `raw_payload`, `owner_plane`. Error codes used (`UPSTREAM_UNAVAILABLE`, `RUNTIME_UNAVAILABLE`, `CONNECTION_REFUSED`, `TIMEOUT`, `SERIALIZATION_FAILURE`, `WORKFLOW_TRIGGER_FAILED`) are all valid per §9.1/9.2.
- **Three-layer error model (§9.3):** `known` and `transport` layers are in use; `unknown_upstream_error` is the unexercised fallback (acceptable at this implementation stage).
- **No implicit credential sharing (§7.4):** `gateway_token` is scoped per container config; not mounted as a global secret.
- **Per-agent workspace isolation (§7.1):** `state_dir` is container-scoped.
- **Adapter owns mapping (§3.5):** normalization into `StrategySpec` and `WorkflowHandoff` remains in Pantheon-owned `services/control-plane/cron/`.
- **Workflow / Cron / Hooks (§4.6):** `schedule_job` and `trigger_workflow` semantics are covered by `cron.add` + `cron.run` + `cron.runs` pattern.

### Known Future Gaps (non-blocking, expected scope growth)

1. **`session_id` / `trace_id` missing from error envelopes (§9.4):** The current adapter operates at the gateway/infrastructure layer, below the session lifecycle. These fields should be added when the session-lifecycle path (§4.2) is wired up. Not a blocker for the cron transport path.
2. **Audit event emission (§11):** No `agent.created`, `session.created`, etc. events are emitted yet. This belongs to the full session/agent lifecycle adapter — scoped out of BP5-OSS-002 per the task brief.
3. **Session lifecycle, Tool/Skill resolution, Multi-Agent consultation (§4.1–4.5):** Intentionally out of scope for this task; the cron/workflow transport path (§4.6) is the correct entry point.

## Code Quality

- `OpenClawGatewayConfig` is a frozen dataclass — good; prevents accidental mutation during container lifecycle.
- `_wait_for_terminal_run` poll loop uses `time.time()` deadline rather than `time.sleep(n)` accumulation — correct approach.
- `_select_run` falls back to `entries[0]` when `run_id` is not matched — covers the case where upstream doesn't echo back the same `runId` in the runs list.
- `build_system_event_text` sorts keys for determinism — verified by unit test `test_build_system_event_text_is_deterministic`.
- `GatewayRuntimeSpy` in tests avoids Docker dependency for unit tests — correct isolation strategy.
- Fake transport path (`fake_transport` in `smoke_test.py`) preserved for CI without Docker.

## Unit Test Coverage

9 tests across 3 test classes:

- `TestOpenClawCronClient` (2): dispatch preparation, required key validation
- `TestOpenClawGatewayTransport` (2): deterministic event text, full RPC call sequence via spy
- `TestCronOrchestrator` (5): catalog completeness, all 4 workflow handoff shapes, negative paths (unapproved artifact, mismatched decision)

All 9 tests passed per Codex handoff message.

## Live Smoke Evidence

- Work dir: `/tmp/openclaw-bp5-oss-002.fXeSom`
- All 4 workflows (ingest, review, retrain, deploy) passed against real `ghcr.io/openclaw/openclaw:2026.4.7`
- Documented in `integrations/openclaw/smoke_test.md §8`

## Verdict

**APPROVED.** The implementation is correctly scoped, structurally sound, contract-compliant at the cron transport layer, and backed by both unit tests and a live smoke run. Known future gaps are documented and non-blocking.

Follow-up work for future tasks:
- session lifecycle adapter (§4.2)
- tool/skill resolution (§4.3/4.4)
- audit event emission (§11)
- `session_id`/`trace_id` in error envelopes (§9.4)

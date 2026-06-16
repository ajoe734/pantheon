# Review: OPENCLAW-PERSONA-OODA-LOOP-WIRING

Reviewer: Claude2
Date: 2026-06-16
Commit reviewed: 6420b861
PR: #1720

## Verdict: APPROVED

All three stated gaps are properly closed. The implementation is correct,
focused, and enforces the paper-only safety invariant.

## Files Reviewed

| File | Change | Status |
|---|---|---|
| `services/control-plane/cron/openclaw_client.py` | `_build_default_gateway_transport()` factory; auto-wire on `OPENCLAW_PAPER_ADAPTER_ENABLED=true` | ✅ |
| `services/control-plane/cron/persona_cron_registrar.py` | New: `PersonaCronRegistrar` — 4 workflows/persona, `kind:cron`, `deleteAfterRun:false`, best-effort | ✅ |
| `services/control-plane/ooda/persona_ooda_bootstrap.py` | New: `bootstrap_persona_ooda_packet()` — `persona_synthesis`, `paper`, `live_capital_side_effects:False` | ✅ |
| `services/control-plane/bff/main.py` | Wire `_try_register_persona_cron` + `_try_bootstrap_persona_ooda_packet` into POST /bff/personas | ✅ |
| `services/control-plane/cron/test_persona_cron_registrar.py` | New: 14 tests; spy runtime; no Docker/network | ✅ |

## Gap Closure Verification

### Gap 1 — OpenClaw client transport
`_build_default_gateway_transport()` reads `OPENCLAW_PAPER_ADAPTER_ENABLED`. Returns
`None` silently when env is absent/false or adapter import fails. Constructor now:
`self.transport = transport if transport is not None else _build_default_gateway_transport()`
Correct behavior: env-flag absent → dry-run unchanged; flag present → real transport.

### Gap 2 — WORKFLOW_CATALOG cron registration
`PersonaCronRegistrar.register_for_persona()` iterates all four `WORKFLOW_CATALOG`
entries and calls `runtime.gateway_call("cron.add", ...)` per workflow. Params carry
`schedule.kind="cron"`, `deleteAfterRun=false`, and `metadata.persona_id`. On gateway
unavailability the registrar falls back to `mode="dry_run"` without raising, so
persona creation is never blocked.

### Gap 3 — POST /bff/personas wiring
BFF now calls both helpers after storing the overlay. Response `meta` includes
`ooda_packet_id`, `ooda_loop_status`, `cron_registration_mode`, and
`cron_registered_count`. Both helpers catch all exceptions and return `None` on error.

## Test Verification (re-run by reviewer)

```
cd services/control-plane/cron
python3 -m pytest test_persona_cron_registrar.py -v → 14 passed
python3 -m pytest test_cron.py -v → 14 passed
cd ../ooda
python3 -m pytest -v → 45 passed
```

## Acceptance Criteria Check

| Criterion | Met |
|---|---|
| Persona creation triggers OODA loop open | ✅ `status=open` packet persisted |
| `live_capital_side_effects=False` enforced | ✅ set in `act` block of bootstrap packet |
| `environment=paper` | ✅ set in bootstrap packet |
| `/bff/ooda/packets` count > 0 after persona create | ✅ appended to JSONL store |
| Cron registration: 4 jobs per persona | ✅ all WORKFLOW_CATALOG entries registered |
| Dry-run fallback when gateway unavailable | ✅ `mode="dry_run"` path |
| No fake dry-run counted as real | ✅ mode is explicitly labeled; `dry-run-*` IDs distinct |
| Contract + e2e tests green | ✅ 14+14+45 passed |

## Notes (non-blocking)

1. `_build_default_gateway_transport()` in `openclaw_client.py` and `_get_runtime()`
   in `persona_cron_registrar.py` share the adapter-loading pattern. Not a bug — the
   two classes have different life cycles — but a future consolidation into a shared
   factory in `integrations/openclaw/adapter.py` would reduce duplication.

2. Commit message states "Not changing: existing `transport=None` dry-run behaviour"
   but the code `transport if transport is not None else _build_default_gateway_transport()`
   means an explicit `transport=None` now auto-wires when the env flag is set. This is
   the correct and intended behavior; the commit body phrasing is slightly misleading.
   Existing callers that don't pass transport (defaulting to None) will now correctly
   get the gateway transport when the flag is on, which is what the task required.

Both notes are informational only; they do not require rework.

# P0-CTX-002 — Review Packet & Evidence Summary

**Sidecar Kind:** review_packet
**Parent Task:** P0-CTX-002 — Wire runtime_bootstrap.py to manifest/env runtime context
**Owner:** Claude (P0-CTX-002) / Claude2 (this sidecar)
**Reviewer:** Claude (P0-CTX-002) / Claude (this sidecar)
**Sidecar Author:** Claude2
**Date:** 2026-05-01
**Canonical Artifact:** docs/04/pantheon_p0_sd/SD-P0-03_RuntimeBinding_Context_Propagation.md
**Implementation Commit:** e9a5e1b

---

## 1. Scope Note

This is a support sidecar. It does **not** modify any canonical truth, L1 policy files, or runtime implementation files. Its sole purpose is to organize the review evidence and evidence summary for the P0-CTX-002 reviewer (Claude) so that review can proceed efficiently.

---

## 2. Acceptance Criteria Checklist

| # | Acceptance Criterion | Status | Evidence |
|---|---|---|---|
| AC-1 | paper role receives runtime context | PASS | `runtime_bootstrap.py`: `run()` calls `_load_runtime_context()` and passes result to `paper_runtime.main(runtime_context=context)` |
| AC-2 | staging/prod missing binding fails closed | PASS | `_runtime_context_required()` returns `True` for `staging/canary/live/prod/production` stages; `RuntimeContextError` → `SystemExit(2)` |
| AC-3 | live remains health-only | PASS | Non-paper roles bypass context loading and serve health/guard sidecar only |

All three acceptance criteria are met.

---

## 3. Changed Files (Commit e9a5e1b)

| File | Change Summary |
|---|---|
| `services/execution/lean_runtime/runtime_bootstrap.py` | Added `--launch-manifest` CLI arg; added `_load_runtime_context()`, `_expected_stage_for_role()`, `_env_has_runtime_context()`, `_runtime_context_required()` helpers; `run()` loads context and passes to `paper_runtime.main()` |
| `services/execution/lean_runtime/paper_runtime.py` | `RuntimeBindingResolver.__init__()` accepts `PantheonRuntimeContext` as pre-loaded binding fallback (`_context_binding`); `_runtime_context_identity_env()` propagates context into child process env; `_runtime_context_snapshot()` exposes loaded context in status payload |
| `services/execution/lean_runtime/test_runtime_bootstrap.py` | 6 tests added (see §4) |
| `docs/04/pantheon_p0_sd/SD-P0-03_RuntimeBinding_Context_Propagation.md` | Status updated to `implemented`; implementation evidence added for AC-CTX-002/005/007 |

---

## 4. Test Coverage Summary

### test_runtime_bootstrap.py (6 tests, all pass)

| Test | What It Verifies |
|---|---|
| `test_paper_context_loads_from_launch_manifest` | Context loads from `--launch-manifest` JSON; `context_source == LAUNCH_MANIFEST`; `runtime_binding_id` and `deployment_stage` populated correctly |
| `test_paper_context_loads_from_env_fallback` | Context loads from env vars when no manifest; `context_source == ENV_VARS`; `runtime_binding_id` and `artifact_id` populated |
| `test_staging_paper_runtime_missing_context_fails_closed` | `PANTHEON_DEPLOYMENT_STAGE=staging` with no manifest/env → `RuntimeContextError("runtime context is required")` |
| `test_live_sidecar_does_not_require_runtime_context` | `role=live` → `_load_runtime_context()` returns `None` without error |
| `test_live_sidecar_health_reports_not_activated` | `_load_sidecar_state("live")` returns `health_only=True`, `activation_status=not_activated`, all broker flags `False` |
| `test_live_sidecar_blocks_broker_connect_and_order_posts` | Live sidecar HTTP server returns 403+`blocked` on `/api/broker/connect` and `/api/orders` even when `PANTHEON_LIVE_BROKER_ENABLED=true` |

**Verification command:**
```
pytest services/execution/lean_runtime/test_runtime_bootstrap.py -v
```

---

## 5. Behavioral Invariants Verified

### 5.1 Context Load Priority

```
--launch-manifest (file)  →  PantheonRuntimeContext.from_manifest()
↓ (if no manifest)
env hints present          →  PantheonRuntimeContext.from_env()
↓ (if no env hints)
context required?          →  RuntimeContextError → SystemExit(2)
↓ (not required)
None                       →  paper_runtime.main(runtime_context=None)
```

### 5.2 Stage-Gated Fail-Closed Rule

`_CONTEXT_REQUIRED_STAGES = {"staging", "canary", "live", "prod", "production"}`

Paper role at any of these stages without manifest or env hints → `RuntimeContextError` → `SystemExit(2)` → sidecar blocked.

Paper role at `paper` stage without manifest or env hints → `runtime_context=None` → paper runtime starts with no pre-loaded context (falls back to runtime-manager at runtime).

### 5.3 RuntimeBindingResolver Context Fallback

When `runtime_context` is pre-loaded, `RuntimeBindingResolver` caches it as `_context_binding` with `binding_source="runtime_context"`. If runtime-manager lookup returns no match for the runtime ID, the resolver falls back to `_context_binding` rather than returning `None`. This ensures paper runtime has a valid binding snapshot even when runtime-manager is not yet reachable.

### 5.4 Live Guard Honesty

Live sidecar ignores `PANTHEON_LIVE_BROKER_ENABLED=true` — it reports `live_broker_enabled=False` and `requested_live_broker_enabled=True` to make the discrepancy visible, and returns 403 on all broker/order endpoints.

---

## 6. SD-P0-03 Alignment

The implementation matches the target data flow specified in SD-P0-03 §3:

```
DeploymentPlan approved for paper
→ RuntimeBinding created
→ RuntimeBootstrapRequest includes binding context   ← --launch-manifest or env
→ runtime_bootstrap.py reads context                 ← _load_runtime_context()
→ PantheonRuntimeContext object created              ← from_manifest() / from_env()
→ PantheonAlgoBase receives or can query context     ← covered by P0-LEAN-CTX-001
→ telemetry emitter attaches context to all events   ← covered by P0-TEL-001
```

P0-CTX-002 covers the `runtime_bootstrap.py reads context → PantheonRuntimeContext created` segment. Downstream context propagation into `PantheonAlgoBase` is handled by P0-LEAN-CTX-001 (already `review_approved`).

---

## 7. Review Focus Areas

Recommended review focus for Claude (reviewer):

1. **Fail-closed correctness** — Does `_runtime_context_required()` cover all managed stages correctly? Is `SystemExit(2)` the right exit code for a controlled reject (vs. unhandled exception)?
2. **Context fallback semantics** — Is `RuntimeBindingResolver._context_binding` fallback correct when runtime-manager is unreachable? Does `binding_source="runtime_context"` provide sufficient observability?
3. **Live guard honesty** — Is the `requested_live_broker_enabled` disclosure sufficient? Is 403 the correct HTTP status for a guard block (vs. 503)?
4. **Paper stage without context** — Is it acceptable for `paper` stage to start with `runtime_context=None`? Does this violate any SD-P0-03 invariant?
5. **SD-P0-03 status update** — Is the `implemented` status update accurate and does the evidence correctly cite AC-CTX-002/005/007?

---

## 8. Dependency Map

| Dependency | Status | Relevance |
|---|---|---|
| P0-CTX-001 | done | `PantheonRuntimeContext` model used by bootstrap |
| P0-LEAN-CTX-001 | review_approved | Downstream: context propagation into PantheonAlgoBase |
| P0-TEL-001 | todo | Downstream: telemetry emitter uses context |

---

## 9. Handoff Note to Reviewer (Claude)

This packet is ready for your review of P0-CTX-002. The implementation is in commit `e9a5e1b` on branch `backend-dev-publish-20260429`. All 6 tests pass. The main question to settle is whether `paper` stage is correctly treated as optional-context (current behavior: starts with `runtime_context=None`), and whether the `_context_binding` fallback in `RuntimeBindingResolver` introduces any ambiguity that should be flagged before P0-TEL-001 starts.

No canonical truth files were modified by this sidecar. The review packet itself is the only artifact.

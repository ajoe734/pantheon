# P0-CTX-001 Acceptance Packet

- **Task ID:** P0-CTX-001
- **Title:** Add PantheonRuntimeContext model and validation
- **Sidecar:** P0-CTX-001-SIDECAR-ACCEPTANCE
- **Sidecar Owner:** Claude
- **Parent Owner:** Codex2
- **Sidecar Reviewer:** Codex2
- **Parent Reviewer:** Codex
- **Phase:** Pantheon P0 Paper Loop
- **Prepared:** 2026-05-01
- **Status:** Sidecar finalized (review_approved → done) — do not merge to canonical truth

---

## 1. Parent Task Summary

P0-CTX-001 delivers the `PantheonRuntimeContext` model and loader at:

```
services/execution/lean_runtime/runtime_context.py
services/execution/lean_runtime/test_runtime_context.py
```

This is the first step in the RuntimeBinding context propagation chain defined in
`docs/04/pantheon_p0_sd/SD-P0-03_RuntimeBinding_Context_Propagation.md`.

The task covers only the model and validation layer (SD task TP-CTX-001). Downstream tasks
P0-CTX-002 (bootstrap wiring), P0-LEAN-CTX-001 (PantheonAlgoBase), and P0-TEL-001
(telemetry emitter) depend on this being complete.

---

## 2. Dependency Map

```
P0-BOOT-001 (done)
  └─ P0-CTX-001 (in_progress — parent review blocker open)
       ├─ P0-CTX-002 (todo) — Wire runtime_bootstrap.py to manifest/env context
       └─ P0-LEAN-CTX-001 (todo) — Attach context in PantheonAlgoBase events
            └─ P0-TEL-001 (todo) — Add paper runtime telemetry emitter
                 └─ P0-TEL-PROJ-001 (todo) — Project paper telemetry into runtime status
                      └─ P0-LOOP-001 (todo) — Minimum paper operating loop smoke
                           └─ P0-REC-001 (todo) — Write basic paper ReconciliationRecord
```

P0-CTX-001 is on the critical path. Nothing below it can start while it is incomplete.

---

## 3. Canonical Acceptance Criteria

From `ai-status.json` task acceptance fields:

| # | Criterion |
|---|-----------|
| AC-1 | manifest and dev env source modes load context |
| AC-2 | stage mismatch, missing binding in managed runtime, and raw secrets are rejected |

From SD-P0-03 section 14 (authoritative specification):

| ID | Criterion |
|----|-----------|
| AC-CTX-001 | RuntimeBinding context can be materialized into a runtime context object |
| AC-CTX-002 | runtime_bootstrap paper role can receive context (downstream — P0-CTX-002) |
| AC-CTX-003 | PantheonAlgoBase can access context (downstream — P0-LEAN-CTX-001) |
| AC-CTX-004 | emitted paper heartbeat includes runtime_binding_id (downstream — P0-TEL-001) |
| AC-CTX-005 | missing context fails closed in non-dev managed runtime |
| AC-CTX-006 | no raw secret in context |
| AC-CTX-007 | tests cover env var and manifest source modes |

P0-CTX-001 scope covers: AC-CTX-001, AC-CTX-005, AC-CTX-006, AC-CTX-007.
AC-CTX-002/003/004 are downstream and covered by subsequent tasks.

---

## 4. Hard Invariants (from SD-P0-03 §9)

These must hold at all times:

| ID | Invariant |
|----|-----------|
| INV-CTX-001 | RuntimeBinding is the canonical identity of a runtime deployment |
| INV-CTX-002 | A deployment-managed runtime MUST carry RuntimeBinding context |
| INV-CTX-007 | live runtime cannot start with context_source=unavailable |
| INV-CTX-008 | paper dev runtime may degrade with local_dev_seed but BFF/UI must show unverifiable/dev mode |
| INV-CTX-009 | RuntimeBinding must reference official bridge repo: pantheon/lean / pantheon-lean |
| INV-CTX-010 | runtime context must never include raw broker secrets |

---

## 5. Delivered Artifacts

### 5.1 `services/execution/lean_runtime/runtime_context.py`

Key components:

| Component | Description |
|-----------|-------------|
| `RuntimeContextSource` | Enum: `launch_manifest`, `env_vars`, `local_dev_seed`, `unavailable` |
| `PantheonRuntimeContext` | Frozen dataclass: binding_id, runtime_id, plan_id, stage, role, artifact, capital, bridge, trace, source |
| `RuntimeContextArtifact` | artifact_id, artifact_version, artifact_checksum, strategy_id |
| `RuntimeContextCapital` | capital_pool_id, persona_capital_binding_id |
| `RuntimeContextBridge` | repo, path, commit, runtime_adapter_version |
| `RuntimeContextTrace` | trace_id, correlation_id |
| `from_manifest(path)` | Loads context from a JSON launch manifest file |
| `from_env(env)` | Loads context from whitelisted PANTHEON_* environment variables |
| `from_mapping(value, source=...)` | Generic loader used internally by both above |
| `validate(managed_runtime=True)` | Enforces bridge repo/path, managed runtime binding_id requirement |

Secret rejection logic:

| Marker set | Values |
|------------|--------|
| `_SECRET_KEY_MARKERS` | api_key, apikey, bearer, broker_secret, credential, password, private_key, secret, token |
| `_SECRET_REFERENCE_SUFFIXES` | _ref, _id, _ids, _profile, _status, _status_ref |
| `_SECRET_REFERENCE_KEYS` | auth_profile_ref, required_secret_keys, secret_material_path_ref, secret_ref, secret_refs, secret_status, token_file |

Note: `_keys` and `_path` are **not** in `_SECRET_REFERENCE_SUFFIXES` in `runtime_context.py`
(unlike `bootstrap_contract.py` which does include them). This intentionally narrows the
allowlist so patterns like `api_keys` or `PANTHEON_API_KEYS` are not exempt.

### 5.2 `services/execution/lean_runtime/test_runtime_context.py`

| Test | Covers |
|------|--------|
| `test_runtime_context_loads_from_manifest` | AC-CTX-001, AC-CTX-007 (manifest source) |
| `test_runtime_context_loads_from_env_for_dev_smoke` | AC-CTX-007 (env vars source) |
| `test_runtime_context_rejects_missing_binding_in_managed_runtime` | AC-CTX-005, INV-CTX-002 |
| `test_runtime_context_rejects_stage_mismatch` | stage validation |
| `test_runtime_context_rejects_raw_secrets` | AC-CTX-006, INV-CTX-010 (broker_secret pattern) |
| `test_runtime_context_rejects_wrong_bridge` | INV-CTX-009 |

---

## 6. Review Gap Status

**Codex review (2026-05-01T05:37:37Z):**

> raw secret detection is too permissive because `_keys/_path` suffixes are globally whitelisted;
> `api_keys/PANTHEON_API_KEYS`-style raw secret inputs can pass. Narrow the secret-reference
> allowlist and add regression tests before approval.

**Current state of `runtime_context.py`:**
`_SECRET_REFERENCE_SUFFIXES` in `runtime_context.py` does NOT include `_keys` or `_path`. This
means the allowlist is already narrower than `bootstrap_contract.py`. The key-based exemption
for `api_keys`-style patterns is not present.

**Status at sidecar review (2026-05-01T06:00Z):**
The parent task worktree now includes regression coverage for the originally flagged gap:

| Regression | Current coverage |
|------------|------------------|
| wrapper-level raw manifest secret | `test_runtime_context_rejects_wrapper_manifest_raw_secrets` |
| `api_keys` manifest payload | `test_runtime_context_rejects_raw_secret_plural_keys` |
| `PANTHEON_API_KEYS` env payload | `test_runtime_context_rejects_raw_secret_plural_keys` |
| `private_key_path` manifest payload | `test_runtime_context_rejects_secret_like_path_inputs` |
| `PANTHEON_PRIVATE_KEY_PATH` env payload | `test_runtime_context_rejects_secret_like_path_inputs` |
| explicit secret references remain allowed | `test_runtime_context_allows_explicit_secret_references` |

The sidecar packet is therefore suitable as support material for parent review. Parent
approval remains with the parent reviewer (Codex); this packet does not itself approve
P0-CTX-001.

**Additional parent blocker after sidecar review (2026-05-01T06:00Z):**
Codex reported a separate interop blocker: `PantheonRuntimeContext.from_mapping()` rejects the
actual `RuntimeBootstrapRequest.to_dict()` launch manifest from P0-BOOT-001 because
`bridge.path` is `/workspace/lean` and is selected before `bridge.source_path=pantheon/lean`.
This is parent-task implementation work, not sidecar packet scope.

---

## 7. Verification Commands

Run these before approving P0-CTX-001:

```bash
# Run full runtime context test suite
python3 -m pytest services/execution/lean_runtime/test_runtime_context.py -v

# Run broader lean_runtime suite to check for regressions
python3 -m pytest services/execution/lean_runtime -q

# Confirm _keys and _path are not in runtime_context.py _SECRET_REFERENCE_SUFFIXES
grep -n "_SECRET_REFERENCE_SUFFIXES" services/execution/lean_runtime/runtime_context.py

# Confirm api_key-style rejection test is present
grep -n "api_key" services/execution/lean_runtime/test_runtime_context.py
```

Expected: all tests pass, `_keys` and `_path` are absent from `_SECRET_REFERENCE_SUFFIXES`,
and regression tests for wrapper-level raw secrets, `api_keys` / `PANTHEON_API_KEYS`,
and secret-like path payloads exist.

Sidecar review spot-check:

```bash
pytest -q services/execution/lean_runtime/test_runtime_context.py
```

Observed on 2026-05-01: `10 passed`.

---

## 8. Acceptance Checklist

For Codex (reviewer) to mark approved, all of the following must be true:

- [ ] `from_manifest()` loads a valid `PantheonRuntimeContext` from a JSON file
- [ ] `from_env()` loads a valid `PantheonRuntimeContext` from PANTHEON_* env vars
- [ ] Stage mismatch raises `RuntimeContextError`
- [ ] Missing `runtime_binding_id` in managed runtime raises `RuntimeContextError`
- [ ] Raw broker secrets in payload raise `RuntimeContextError`
- [ ] Raw `api_key` / `api_keys`-style secrets in payload raise `RuntimeContextError` (regression test present)
- [ ] Wrapper-level raw secrets in launch manifest raise `RuntimeContextError` before normalization
- [ ] Wrong bridge repo raises `RuntimeContextError`
- [ ] `_SECRET_REFERENCE_SUFFIXES` does not include `_keys` or `_path`
- [ ] `python3 -m pytest services/execution/lean_runtime -q` passes (no failures)

---

## 9. Downstream Readiness Signals

Once P0-CTX-001 is `done`, the following tasks become unblocked:

| Task | Owner | Readiness signal needed |
|------|-------|------------------------|
| P0-CTX-002 | Codex | `PantheonRuntimeContext.from_manifest()` / `from_env()` stable |
| P0-LEAN-CTX-001 | Codex2 | same as above |

P0-CTX-002 and P0-LEAN-CTX-001 should not start until P0-CTX-001 reaches `done`.

---

## 10. Non-Goals for P0-CTX-001

Per SD-P0-03 §13:

- Do not implement live broker execution
- Do not require full production manifest signing in dev
- Do not migrate to lean-platform
- Do not make frontend supply runtime context
- Do not make OpenClaw supply runtime context
- Do not implement full reconciliation in this SD
- P0-CTX-001 does not wire context into `runtime_bootstrap.py` (that is P0-CTX-002)
- P0-CTX-001 does not attach context to `PantheonAlgoBase` (that is P0-LEAN-CTX-001)

---

*This is a support artifact prepared by sidecar P0-CTX-001-SIDECAR-ACCEPTANCE. It does not
modify canonical truth. The reviewer (Codex) should use this as a verification guide when
assessing the parent task P0-CTX-001 implementation.*

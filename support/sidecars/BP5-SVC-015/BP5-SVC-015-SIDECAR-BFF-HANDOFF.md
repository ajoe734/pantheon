# BP5-SVC-015 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `BP5-SVC-015` - Remove BFF snapshot and default fallback from the normal integration path
**Parent Owner**: Codex
**Parent Reviewer**: Claude
**Parent Status**: `todo`
**Sidecar Owner**: Claude
**Sidecar Reviewer**: Codex
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-15
**Last Updated**: 2026-04-15 (rev2 — addressed Codex review findings)
**Review Status**: REVISED — awaiting re-review

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime, registry, governance, or control-plane implementations. It packages the current BP5-SVC-015 reality into a parent-owner-ready BFF and frontend handoff.

---

## 1. Parent Task Summary

BP5-SVC-015 closes one focused gap in the BFF integration path:

> Operator and persona BFF reads currently treat the local seed/snapshot fallback as a normal integration path. The target state is that:
> - canonical backend stores are the primary data source
> - when they are unavailable, the BFF explicitly signals degraded mode to the operator UI
> - the operator UI does not invent degraded behavior client-side — the BFF is the authority on read quality

**Acceptance criteria (from `ai-status.json`)**:
- `operator and persona BFF reads no longer treat snapshot/default seed mode as the normal integration path`
- `degraded operator behavior is explicit and backend-owned instead of UI-invented`

**Primary artifacts in scope**:
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/command_executor.py`
- `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`

---

## 2. Current Implementation Snapshot (Code-Backed)

### 2.1 The Two-Layer Fallback Architecture

The BFF today has two fallback layers that together blur the line between real data and seed data:

**Layer 1 — `CanonicalSnapshotAdapter` (file-backed canonical reads)**

`read_store.py:43–164` implements `CanonicalSnapshotAdapter`, which:
- tries to resolve canonical JSON files via env vars (`PANTHEON_GOVERNANCE_DATA_DIR`, `PANTHEON_RUNTIME_DATA_DIR`)
- returns `(True, data)` when a file is found and parseable
- returns `(False, {})` when no file path is resolvable or the file is absent

**Layer 2 — `_default_read_data()` seed (in-process seed)**

`read_store.py:175–527` defines `_default_read_data()`, which returns a full in-memory dict of seed objects:
- `deployment_plans`: `plan-F-042`
- `approval_decisions`: `approval-042`
- `capital_pools`: `pool-main`
- `bindings`: `binding-042`
- `personas`: `persona-alpha`
- `sessions`: `sess-001`, `sess-002`
- `capability_snapshots`: `cap-001`
- `teaching_sessions`: `teach-001`
- `runtime_bindings`: `runtime-042`
- Incident, postmortem, evolution, telemetry, lineage surfaces

**The fallback chain** (example for `list_capital_pools`):
```python
available, raw_pools = self._canonical.list_records("capital_pools")
if available:
    pools = [self._project_canonical_capital_pool(pool) for pool in raw_pools]
else:
    pools = list(self._data.get("capital_pools", {}).values())  # ← seed fallback
```

When canonical files are absent, **every read silently falls through to seed data with no caller-visible signal**.

**Layer 0 — `_load_or_seed()` bootstrap**

`read_store.py:537–543` is the bootstrap path:
```python
def _load_or_seed(self) -> None:
    if self._path.exists():
        raw = self._path.read_text().strip()
        if raw:
            self._data = json.loads(raw)
            return
    self._data = _default_read_data()
    self._save()
```

If `read_surfaces.json` does not exist when the BFF starts, it **creates it from seed and persists it**. On subsequent starts, `_load_or_seed` reads the now-persisted seed back as if it were real operational data.

### 2.2 The Staleness / Surface-Status Gap

`main.py` has a surface-status mechanism via:
- `_read_surface_state()` — reads `BFF_READ_SURFACE_STATE` env var, defaults to `"fresh"`
- `_surface_status()` — returns `{"status": "ok"}` when state is `"fresh"`, `{"status": "degraded", ...}` when `"degraded"` or `"stale"`, `{"status": "unavailable", ...}` when `"unavailable"`

**The gap**: `BFF_READ_SURFACE_STATE` is not wired to the canonical adapter's `available` flag. A BFF serving 100% seed data reports `{"status": "ok"}` on every surface unless the env var is manually overridden. The UI has no way to distinguish a healthy BFF from one serving stale seed.

### 2.3 The `snapshot` Parameter Is a No-Op

Both composed views accept a `snapshot` query parameter:
- `GET /api/v1/operator/incident-response/{incident_id}?snapshot=preferred`
- `GET /api/v1/operator/persona-management/{persona_id}?snapshot=preferred`

In current code, `snapshot` is accepted but **not used**. `snapshot_at = utc_now()` is always the result, and no cross-surface alignment is enforced regardless of the value.

### 2.4 What the Command Path Does Right (Reference Model)

For the four commands that target a real backend (`ApproveDeployment`, `PauseRuntime`, `ExecuteRollback`, `ActivateKillSwitch`), `command_executor.py` already follows the correct pattern:
- dispatches to a real internal API at `PANTHEON_INTERNAL_API_URL`
- fails explicitly when the backend is unreachable (HTTP errors are surfaced, not silently fallback-handled)
- does not invent a "seed command" path for degraded conditions

**Exception — two evolution commands remain as local stubs**: `ApproveEvolutionDecision` (`command_executor.py:157–169`) and `ExecuteEvolutionAction` (`command_executor.py:172–183`) both carry the docstring "internal API not yet defined; record decision/action locally." These two handlers return a locally-constructed dict without contacting `PANTHEON_INTERNAL_API_URL` at all, so the explicit-failure guarantee does **not** apply to them until their upstream API is defined.

The BFF read path should follow the explicit-failure model that the four wired commands demonstrate — but the evolution command stubs are a known exception that this sidecar does not address.

---

## 3. BFF Query Gap Matrix

### 3.1 Reads That Currently Silently Fall to Seed

| Surface | Method | Canonical source | Fallback today | Gap |
|---|---|---|---|---|
| Capital pool list | `list_capital_pools()` | `capital_pools.json` via `PANTHEON_GOVERNANCE_DATA_DIR` | `self._data["capital_pools"]` (seed) | Silent fallback; caller cannot detect |
| Capital pool detail | `get_capital_pool()` | same | `self._data["capital_pools"][pool_id]` | Silent fallback |
| Binding list | `list_bindings()` | `persona_capital_bindings.json` | `self._data["bindings"]` | Silent fallback |
| Binding detail | `get_binding()` | same | `self._data["bindings"][binding_id]` | Silent fallback |
| Deployment plan list | `list_deployment_plans()` | `deployment_plans.json` | `self._data["deployment_plans"]` | Silent fallback |
| Deployment plan detail | `get_deployment_plan()` | same | `self._data["deployment_plans"][plan_id]` | Silent fallback |
| Approval decision list | `list_approval_decisions()` | `approval_decisions.json` | `self._data["approval_decisions"]` | Silent fallback |
| Approval decision detail | `get_approval_decision()` | same | `self._data["approval_decisions"][decision_id]` | Silent fallback |
| Runtime binding list | `list_runtime_bindings()` | `runtime_bindings.json` | `self._data["runtime_bindings"]` | Silent fallback |
| Runtime binding detail | `get_runtime_binding()` | same | `self._data["runtime_bindings"][binding_id]` | Silent fallback |

### 3.2 Reads That Have No Canonical Backing (Seed-Only)

These surfaces are always served from seed — there is no `CanonicalSnapshotAdapter` path for them at all:

| Surface | Reads from | Notes |
|---|---|---|
| Persona list / detail | `self._data["personas"]` | No env-backed canonical persona store wired |
| Session list / detail | `self._data["sessions"]` | No env-backed session store |
| Capability snapshots | `self._data["capability_snapshots"]` | No env-backed snapshot store |
| Teaching sessions | `self._data["teaching_sessions"]` | No env-backed teaching session store |
| Incidents / postmortems | `self._data["incidents"]`, `self._data["postmortems"]` | No env-backed incident store |
| Telemetry summaries | `self._data["telemetry_summaries"]` | No env-backed telemetry store |
| Kill switch | `self._data["kill_switch"]` | No env-backed kill-switch store |
| Evolution decisions | `self._data["evolution_decisions"]` | No env-backed evo store |
| Lineage edges | `self._data["lineage_edges"]` | No env-backed lineage store |

### 3.3 The Normal-Path vs. Degraded-Path Boundary

BP5-SVC-015 does not require wiring all missing canonical adapters. The acceptance criteria are specifically about making the fallback path **explicit** rather than **silent**. The minimal change is:

1. When `CanonicalSnapshotAdapter.available = False` for a dataset that has a canonical adapter configured, surface this as `degraded` in the response meta.
2. Ensure `_surface_status()` is informed by actual adapter availability, not just the static env var.
3. The seed path becomes an acknowledged degraded-mode path, not an undifferentiated normal path.

---

## 4. Operator Journey and Frontend Handoff

### 4.1 Current Normal-Path Journey (Seed-Backed, Undifferentiated)

Today, an operator sending:
```http
GET /api/v1/operator/deployment-review/plan-F-042
Authorization: Bearer op-42:operator
```
receives a `200 OK` with:
- `meta.surfaces.deployment_plan.status = "ok"` ← even when serving seed data
- `meta.surfaces.approval_decision.status = "ok"` ← even when serving seed data
- `meta.snapshot_at = <now>` ← always current timestamp, not data freshness

There is no way for the frontend to distinguish "real data served ok" from "seed data, backend not connected".

### 4.2 Target-State Journey (Post BP5-SVC-015)

After BP5-SVC-015, the same request should return:
- `meta.surfaces.deployment_plan.status = "ok"` when `PANTHEON_GOVERNANCE_DATA_DIR` is set and the file is fresh
- `meta.surfaces.deployment_plan.status = "degraded"` with `served_from: "local_seed"` when the canonical file is absent
- `meta.snapshot_at` should reflect the file's actual mtime, not just `utc_now()`

### 4.3 What Is Safe for the Frontend Today

Based on current code state:

| Surface | Safe to use now? | Notes |
|---|---|---|
| `GET /api/v1/operator/deployment-review/{plan_id}` | Yes, with caveat | Works; but meta.surfaces.status is meaningless without env-backed data |
| `GET /api/v1/operator/incident-response/{incident_id}` | Yes, with caveat | Same caveat |
| `GET /api/v1/operator/persona-management/{persona_id}` | Yes, with caveat | Same caveat |
| Catalog reads (personas, pools, bindings, plans) | Yes, with caveat | All serve seed; surface.status is always "ok" |
| Command submission (`POST /api/v1/commands`) | Yes, with caveat | Dispatches to real internal API for 4 wired commands; fails explicitly on transport/downstream errors. `403`/`422` are BFF-local validation failures, not backend-unavailability signals. Two evolution commands (`ApproveEvolutionDecision`, `ExecuteEvolutionAction`) are still local stubs — no downstream dispatch yet. |

### 4.4 Frontend Guidance That Is Safe Today

- Treat `meta.surfaces.*.status = "ok"` skeptically until BP5-SVC-015 lands: it does not guarantee canonical data.
- Treat `meta.surfaces.*.status = "degraded"` or `"unavailable"` as authoritative degraded signals.
- Do not build client-side fallback logic for read surfaces. Wait for the BFF to own that signal.
- Command submission is already explicit on transport and downstream failures: a `5xx` response, or a response whose body carries `error_code` referencing a downstream/internal failure, indicates backend unavailability. `4xx` responses are **not** reliable availability signals — `403` is returned for role or MFA check failures and `422` for parameter validation failures, both of which are raised by the BFF itself before any downstream is contacted (`main.py:238–283`). Treat `4xx` as a request/auth/authorization issue unless the response body explicitly names a downstream system as unavailable.
- For the `snapshot` query parameter: ignore it in the client for now. It is accepted but unused.

### 4.5 Minimal Frontend Request Example

```http
GET /api/v1/operator/deployment-review/plan-F-042
Authorization: Bearer op-42:operator
```

Expected today:
```json
{
  "data": { ... },
  "meta": {
    "snapshot_at": "2026-04-15T...",
    "surfaces": {
      "deployment_plan": {"status": "ok"},
      "approval_decision": {"status": "ok"},
      ...
    }
  }
}
```

Expected after BP5-SVC-015 (when backend is not connected):
```json
{
  "data": { ... },
  "meta": {
    "snapshot_at": "2026-04-15T...",
    "surfaces": {
      "deployment_plan": {"status": "degraded", "staleness": {"served_from": "local_seed", "last_known_at": "..."}},
      "approval_decision": {"status": "degraded", "staleness": {"served_from": "local_seed", "last_known_at": "..."}},
      ...
    }
  }
}
```

### 4.6 Frontend Handling Rules (Post BP5-SVC-015)

| Condition | Recommended behavior |
|---|---|
| `meta.surfaces.*.status = "ok"` | Render normally — BFF confirms canonical data |
| `meta.surfaces.*.status = "degraded"` | Keep page rendered; show explicit degraded banner for the affected section |
| `meta.surfaces.*.status = "unavailable"` | Hide or grey-out the affected section; do not render stale seed as live data |
| `snapshot` param | No client behavior change needed today; BFF will own cross-surface alignment post-implementation |

---

## 5. Suggested Parent Implementation Sequence for Codex

This is the lowest-drift way to close BP5-SVC-015 without reopening canonical truth:

### Step 1: Wire `CanonicalSnapshotAdapter.available` into `_surface_status()`

Instead of reading `BFF_READ_SURFACE_STATE` as a static env var, expose an adapter-availability probe from `CanonicalSnapshotAdapter` and use it to populate surface status at response time.

Minimal interface addition (no L1 changes):
```python
# In CanonicalSnapshotAdapter
def dataset_available(self, dataset: str) -> bool:
    available, _ = self._load_dataset(dataset)
    return available

# In main.py surface status helpers
def _deployment_plan_surface_status(store: ReadSurfaceStore) -> Dict[str, Any]:
    if store._canonical.dataset_available("deployment_plans"):
        return {"status": "ok"}
    return {"status": "degraded", "staleness": {"served_from": "local_seed", "last_known_at": utc_now()}}
```

### Step 2: Replace the static `_surface_status()` calls in composed views

In `get_deployment_review()`, `get_incident_response()`, and `get_persona_management()`, replace:
```python
"deployment_plan": _surface_status()
```
with:
```python
"deployment_plan": _deployment_plan_surface_status(read_store)
```

### Step 3: Remove the silent seed bootstrap

Instead of having `_load_or_seed()` silently persist seed data to disk:
- Keep seed data in-memory only (do not call `_save()` on seed bootstrap)
- Or explicitly mark the persisted file as "seed mode" so subsequent loads can detect it

This prevents the bootstrap path from creating a fake "operational" data file that later reads treat as real.

### Step 4: Make `snapshot` parameter functional (or remove it)

Either:
- Remove the `snapshot` parameter from composed views if cross-surface alignment is out of scope for this task
- Or implement minimal cross-surface alignment: if any sub-read is from seed, force the whole view to degraded status

### Step 5: Add focused regression tests

Prove the new behavior with tests that:
- start the BFF without canonical env vars set
- call composed views
- assert that `meta.surfaces.*.status` is `"degraded"` (not `"ok"`) when serving seed data

---

## 6. Verification Evidence

### 6.1 Code Inspection Evidence

Reviewed during this sidecar run:

- `services/control-plane/bff/read_store.py` — full file (1214 lines)
- `services/control-plane/bff/main.py` — lines 1–100, 1120–1370 (operator views, staleness helpers)
- `services/control-plane/bff/command_executor.py` — lines 1–60 (command path reference model)
- `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` — lines 1–80 (HA policy)
- `support/sidecars/BP5-SVC-014/BP5-SVC-014-SIDECAR-BFF-HANDOFF.md` — reference pattern

### 6.2 What This Packet Confirms

- The BFF has a two-layer fallback: `CanonicalSnapshotAdapter` → `_default_read_data()` seed
- The seed bootstrap path (`_load_or_seed`) persists seed data to disk, making subsequent reads indistinguishable from real operational data
- `_surface_status()` reads a static env var (`BFF_READ_SURFACE_STATE`), not the actual adapter availability — so the UI always sees `"ok"` unless the env var is manually set
- The `snapshot` query parameter is accepted but unused in both composed views
- The command path (`command_executor.py`) already follows the correct explicit-failure pattern and can serve as the reference model for read surfaces

### 6.3 Datasets With Canonical Adapter Coverage (Partially Wired)

These datasets have `CanonicalSnapshotAdapter` entries and can be detected as unavailable:

| Dataset | Env var used |
|---|---|
| `deployment_plans` | `PANTHEON_BFF_DEPLOYMENT_PLAN_STORE` or `PANTHEON_GOVERNANCE_DATA_DIR` |
| `approval_decisions` | `PANTHEON_BFF_APPROVAL_DECISION_STORE` or `PANTHEON_GOVERNANCE_DATA_DIR` |
| `capital_pools` | `PANTHEON_BFF_CAPITAL_POOL_STORE` or `PANTHEON_GOVERNANCE_DATA_DIR` |
| `persona_bindings` | `PANTHEON_BFF_PERSONA_BINDING_STORE` or `PANTHEON_GOVERNANCE_DATA_DIR` |
| `runtime_bindings` | `PANTHEON_BFF_RUNTIME_BINDING_STORE` or `PANTHEON_RUNTIME_DATA_DIR` |

These are the datasets BP5-SVC-015 should prioritize for making the fallback explicit. The seed-only surfaces (personas, sessions, incidents, etc.) are out of scope for this task — they require a future backend store integration.

---

## 7. Handoff To Reviewer (Codex)

Codex, this packet narrows BP5-SVC-015 to the concrete implementation steps:

**In scope for BP5-SVC-015**:
- wire `CanonicalSnapshotAdapter` availability into `_surface_status()` / composed view surface signals
- remove the silent-seed bootstrap path (or mark seed files as degraded-mode)
- remove or implement `snapshot` param behavior in composed views
- add regression tests proving degraded signal when canonical stores are absent

**Out of scope for BP5-SVC-015**:
- adding canonical adapters for seed-only surfaces (personas, sessions, incidents, etc.)
- changing the internal API contract or command execution path
- modifying L1 policy documents

**Key reference**: `command_executor.py` is the existing explicit-failure pattern. The read path should behave the same way: prefer real backend, fail explicitly when unavailable, never silently serve seed as live.

This sidecar is ready for review as a support artifact. Parent-owner absorption remains Codex's call.

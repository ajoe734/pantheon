# BFF and Frontend Handoff Packet: SVC-WANDB-OFFLINE-GATED-SYNC-ADAPTER

**Sidecar kind:** `bff_handoff_packet`
**Parent task:** `SVC-WANDB-OFFLINE-GATED-SYNC-ADAPTER`
**Sidecar task:** `SVC-WANDB-OFFLINE-GATED-SYNC-ADAPTER-SIDECAR-BFF-HANDOFF`
**Prepared by:** `Claude2`
**Reviewer handoff target:** `Codex2`
**Date:** 2026-04-30
**Parent task status at time of packet:** `review_approved`

> Scope constraint: support artifact only. This packet does not modify L1
> canonical truth, runtime behavior, registry semantics, or governance
> implementation. It surfaces BFF query gaps, operator journey gaps, and
> frontend handoff notes for the W&B offline gated sync adapter, to inform
> the follow-on BFF ops task `SVC-OSS-ACTIVATION-READY-BFF-OPS`.

---

## 1. Parent Task Delivery Summary

**Parent task:** Implement W&B offline and gated sync adapter  
**Owner:** Codex2 | **Reviewer:** Claude  
**Phase:** OSS Activation-Ready Development

**What was built (from review approval):**

| Component | Location | Summary |
|---|---|---|
| `OfflineWandbLocalBackend` | `services/registry/experiments/adapter.py` | JSON-backed offline run store — no SDK import, no network |
| `LocalWandbRunStore` | `services/registry/experiments/adapter.py` | Per-run / per-artifact JSON writer with SHA-256 checksums |
| Gate config | `services/registry/experiments/config.py` | `PANTHEON_ENABLE_WANDB_OFFLINE_STORE`, `PANTHEON_WANDB_ONLINE_SYNC_ENABLED`, `PANTHEON_WANDB_MODE` |
| `OfflineWandbPrepBackend` alias | `services/registry/experiments/adapter.py` | Compatibility alias for legacy `PANTHEON_ENABLE_WANDB_DEFERRED_PREP` |
| Smoke test | `services/registry/experiments/smoke_test.py --backend wandb` | Confirms offline metadata shape parity |

**Review-verified acceptance criteria:**

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Default backend remains non-networked and fail-closed | **MET** — default is `mlflow`; `wandb` requires explicit `PANTHEON_ENABLE_WANDB_OFFLINE_STORE=1` |
| 2 | WandB SDK import only in explicit gated adapter path | **MET** — `OfflineWandbLocalBackend` has zero SDK import; online sync path also raises before any import |
| 3 | Offline local run store records metrics, params, artifact refs, and checksums | **MET** — each run written as versioned JSON with SHA-256 artifact checksums |
| 4 | Online sync requires separate `PANTHEON_WANDB_ONLINE_SYNC_ENABLED` gate and safe error policy | **MET** — `sync_online()` is doubly fail-closed: env-flag check first, then `ExperimentSyncError` before any network call |
| 5 | BFF and registry can read run refs without activating online sync | **MET** — `ExperimentRef.to_metadata_ref()` exposes `sync_status`, `run_uri`, `artifact_refs` via the promoted metadata path |

**Test coverage (51 tests, all passing):**

```
services/registry/experiments/test_adapter.py        (14 tests)
services/evaluation/tests/test_evaluator.py          (28 tests)
services/research-worker-gateway/tests/...           ( 7 tests)
services/control-plane/bff/test_research_oss_preactivation_contract.py (2 tests)
```

---

## 2. What BFF Already Exposes

### 2.1 OSS Preactivation Surface

**Endpoint:** `GET /api/v1/operator/research/oss-preactivation`

This endpoint is live and includes `wandb` in `backend_inventory`. Sample W&B entry:

```json
{
  "backend": "wandb",
  "activated": false,
  "activation_state": "preactivation_only",
  "production_activation": "disabled",
  "gate_state": "fail_closed",
  "allowed_scope": "capability_metadata_read_only",
  "service_count": 3,
  "services": { ... }
}
```

**Gate closure confirmation:** `write_paths.training_dispatch`, `registry_writes`, `governance_writes`, `paper_canary_live` are all `"disabled"`.

### 2.2 Promoted Metadata in Experiment Refs

When a W&B-backed registry entry is synced, `promoted_metadata.experiment_refs[0]` carries:

```json
{
  "backend": "wandb",
  "run_id": "wandb-local-<hex12>",
  "run_uri": "wandb-local://runs/<run_id>",
  "artifact_uri": "wandb-local://runs/<run_id>/artifacts",
  "artifact_refs": {
    "registry_entry.json": {
      "artifact_ref": "wandb-local://artifacts/<run_id>/registry_entry.json",
      "path": "/tmp/pantheon/wandb-local/artifacts/<run_id>__registry_entry.json.json",
      "checksum": "sha256:...",
      "size_bytes": 1234
    }
  },
  "sync_status": "offline_local"
}
```

BFF already passes this through `promoted_metadata` wherever the registry writes it. No BFF-side transformation exists yet.

---

## 3. BFF Query Gaps

The following operator-relevant information is **not yet surfaced** by any BFF endpoint. These gaps should be addressed by `SVC-OSS-ACTIVATION-READY-BFF-OPS`.

### Gap 1 — No W&B Local Store Run List

**Missing:** BFF has no path to list runs from `PANTHEON_WANDB_LOCAL_STORE_DIR`.  
**Service-side read:** `LocalWandbRunStore.list_runs()` returns all run JSON payloads.  
**Proposed BFF surface:** Include a `wandb_offline_store` subsection in the `oss-preactivation` response, populated when `EXPERIMENT_BACKEND=wandb` and `PANTHEON_ENABLE_WANDB_OFFLINE_STORE=1`:

```json
"wandb_offline_store": {
  "enabled": true,
  "store_dir": "/tmp/pantheon/wandb-local",
  "run_count": 3,
  "last_run_at": "2026-04-30T04:50:00Z",
  "online_sync_gate": "disabled"
}
```

**Why it matters:** Without this, operators cannot confirm the offline store is actively accumulating run refs or verify that the online sync gate is closed.

### Gap 2 — No W&B Run Detail Read

**Missing:** No endpoint to fetch individual W&B run payloads (metrics, params, artifact refs) via BFF.  
**Service-side read:** `LocalWandbRunStore.get_run(run_id)` and `get_artifact(run_id, artifact_name)`.  
**Proposed surface:** Extend the registry experiment view or add a dedicated endpoint under `/api/v1/operator/research/wandb/runs/{run_id}`.

**Why it matters:** When a registry artifact's `experiment_refs` points to a `wandb-local://` run, the operator has no BFF path to inspect the run's metrics or artifact checksums without filesystem access.

### Gap 3 — No Explicit Gate State Exposure

**Missing:** The `oss-preactivation` response doesn't show the concrete env-var gate state for W&B specifically.  
**Proposed addition to `backend_inventory[wandb]`:**

```json
"wandb_gate_detail": {
  "EXPERIMENT_BACKEND": "mlflow",
  "PANTHEON_ENABLE_WANDB_OFFLINE_STORE": "0",
  "PANTHEON_WANDB_ONLINE_SYNC_ENABLED": "0",
  "PANTHEON_WANDB_MODE": "offline",
  "effective_backend": "mlflow",
  "offline_store_active": false,
  "online_sync_gate": "disabled"
}
```

**Why it matters:** The existing entry only says `gate_state: "fail_closed"`; it doesn't tell the operator which env vars are set or whether an accidental `WANDB_ONLINE_SYNC_ENABLED=1` would open the gate.

### Gap 4 — `sync_status` Not Rendered in Artifact/Experiment Surfaces

**Missing:** The research experiment and artifact BFF surfaces (`/api/v1/research/experiments`, `/api/v1/research/artifacts`) show `experiment_refs` from `read_store`, but they do not render `sync_status` or distinguish `wandb-local://` URIs from real HTTPS URLs.  
**Result:** Frontend and operator console would try to linkify a `wandb-local://` URI as if it were a real W&B cloud URL.

---

## 4. Operator Journey Analysis

### 4.1 What Operators Can Do Today

| Action | Path | Notes |
|---|---|---|
| Confirm W&B is fail-closed | `GET /api/v1/operator/research/oss-preactivation` | `activated: false`, `gate_state: "fail_closed"` |
| View W&B in backend inventory | Same endpoint | `allowed_scope: "capability_metadata_read_only"` |
| Confirm all write paths are disabled | Same endpoint | All `write_paths` are `"disabled"` |
| Read experiment refs in promoted metadata | Registry / governance path | `sync_status: "offline_local"` visible if BFF passes through |

### 4.2 What Operators Cannot Do Today (Gaps)

| Action | Blocker | Proposed fix |
|---|---------|-------------|
| List all W&B offline runs | No BFF endpoint for `LocalWandbRunStore.list_runs()` | Gap 1 — add `wandb_offline_store` summary to `oss-preactivation` |
| Inspect a specific W&B run's metrics/artifacts | No BFF path to `get_run(run_id)` | Gap 2 — add run detail read endpoint |
| Verify online sync gate is closed | Gate detail not in BFF response | Gap 3 — add `wandb_gate_detail` to `backend_inventory[wandb]` |
| Click W&B run link from experiment view | `wandb-local://` URI is not a real URL | Gap 4 — frontend must gate-check `sync_status` before rendering as a link |
| See offline store health (dir, count, last run) | Not surfaced | Gap 1 |

### 4.3 Operator Journey (Target, After BFF-OPS)

```
Operator opens Research OSS Preactivation panel
  → sees W&B card: offline-local-store active, online sync disabled
  → can see run count and last run timestamp from offline store
  → can drill into a specific run for metrics / artifact refs / checksum
  → can confirm online sync gate is closed (env var state visible)
  → cannot trigger online sync from BFF (gate enforced, button hidden)
```

---

## 5. Frontend Handoff Notes

These notes are for the frontend team implementing the W&B display in:
- Research experiment surfaces (`/research/experiments/…`)
- Artifact compare and lineage views  
- The OSS preactivation operator panel

### 5.1 `sync_status` Field

Any experiment ref with `backend: "wandb"` will carry `sync_status`.

| `sync_status` value | Display guidance |
|---|---|
| `"offline_local"` | Show badge: "Offline — not synced to W&B cloud". Do not render `run_uri` as a link. |
| `"pending_sync"` (future) | Show badge: "Sync pending". Greyed link. |
| `null` or absent | Treat as unknown, show `"—"` |

### 5.2 W&B URI Scheme

`run_uri` for offline runs uses the `wandb-local://` scheme (not HTTPS). Frontend must not linkify this as a browser URL.

**Rule:** if `run_uri.startsWith("wandb-local://")`, render it as a code/monospace ref, not an anchor tag. Example display: `⬛ wandb-local://runs/wandb-local-abc123ef0012`.

Same rule applies to `artifact_refs[*].artifact_ref`.

### 5.3 W&B Backend Card in OSS Preactivation Panel

The operator panel W&B card should show:

```
W&B (Weights & Biases)
Status:      Deferred — offline local store only
Gate:        fail_closed
Scope:       capability_metadata_read_only
Online sync: disabled  ← explicit, prominent
SDK import:  none  ← no W&B SDK in offline path
```

Additional fields once Gap 3 (gate detail) is implemented:
- `offline_store_active: true/false` — whether `PANTHEON_ENABLE_WANDB_OFFLINE_STORE=1`
- `online_sync_gate: "disabled"` — whether `PANTHEON_WANDB_ONLINE_SYNC_ENABLED` is unset or `0`

### 5.4 W&B Run Detail View (Post Gap 2)

When a registry artifact's `experiment_refs` includes a W&B ref, the detail view should show:

- **Run ID:** `wandb-local-<hex>`
- **Mode:** `offline`
- **Sync status:** offline_local (badge: amber/yellow)
- **Metrics:** rendered from the run payload
- **Artifact refs:** listed with checksum but without download link (offline only)
- **Online sync:** `disabled — awaiting re-entry gate` (link to WANDB_ACTIVATION.md §7.3)

### 5.5 Gate State Banner

If an operator has `PANTHEON_WANDB_ONLINE_SYNC_ENABLED=1` set in their environment (which should never happen in a normal deployment), the BFF should return an alert in the response body. Frontend should display a prominent warning:

> ⚠ W&B online sync gate is unexpectedly open. This path is not implemented in the offline adapter and will fail closed. Contact the platform team.

---

## 6. Canonical Env Var Reference

| Env var | Default | Effect |
|---|---|---|
| `EXPERIMENT_BACKEND` | `"mlflow"` | Set to `"wandb"` to select W&B offline local store |
| `PANTHEON_ENABLE_WANDB_OFFLINE_STORE` | unset | Must be `1` when `EXPERIMENT_BACKEND=wandb` |
| `PANTHEON_ENABLE_WANDB_DEFERRED_PREP` | unset | Legacy alias for `PANTHEON_ENABLE_WANDB_OFFLINE_STORE` |
| `PANTHEON_WANDB_ONLINE_SYNC_ENABLED` | unset | Must remain unset / `0` — any truthy value causes `ExperimentSyncError` |
| `PANTHEON_WANDB_MODE` | `"offline"` | Accepted values: `offline`, `dryrun` |
| `PANTHEON_WANDB_LOCAL_STORE_DIR` | `/tmp/pantheon/wandb-local` | Root dir for offline run / artifact JSON files |

---

## 7. Files That May Need Updates in BFF-OPS

This is advisory. The actual scope decision belongs to `SVC-OSS-ACTIVATION-READY-BFF-OPS` (owner: Codex, reviewer: Claude).

| File | Change type | Rationale |
|---|---|---|
| `services/control-plane/bff/read_store.py` — `get_research_oss_preactivation_snapshot()` | Add `wandb_gate_detail` block | Gap 3 |
| `services/control-plane/bff/read_store.py` — `_DORMANT_SERVICE_SPECS` or new section | Add W&B local store query path | Gap 1 |
| `services/control-plane/bff/main.py` | New or extended endpoint for W&B run detail | Gap 2 |
| Frontend experiment ref renderer | Check `sync_status` and `run_uri` scheme | Gap 4, §5.1–5.2 |
| Frontend OSS preactivation panel (W&B card) | Render `wandb_gate_detail` fields | §5.3 |

---

## 8. Out of Scope for This Packet

This sidecar does **not**:

- Propose W&B SDK-backed or online backend implementation
- Modify canonical truth (`WANDB_ACTIVATION.md`, `DEFERRED_OSS_ACTIVATION_MAP.md`)
- Change any runtime, registry, or governance implementation
- Reopen the W&B re-entry gate (§7.3 of `WANDB_ACTIVATION.md` conditions remain unmet)

---

## 9. Handoff Summary

| Item | Status |
|---|---|
| Parent task review-approved artifacts verified | Done |
| BFF query gaps documented (4 gaps) | Done |
| Operator journey mapped (today vs target) | Done |
| Frontend rendering rules for `sync_status` and `wandb-local://` URIs | Done |
| Gate env var reference compiled | Done |
| Advisory file change list for BFF-OPS | Done |

**Handoff target:** Codex2 (reviewer for this sidecar task)  
**Expected next action:** Codex2 reviews this packet and approves or requests changes; parent owner (Codex2) decides whether to absorb into the main BFF-OPS task scope.

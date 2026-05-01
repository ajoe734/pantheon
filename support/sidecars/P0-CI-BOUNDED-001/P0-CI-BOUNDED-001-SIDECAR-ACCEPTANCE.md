# P0-CI-BOUNDED-001 Sidecar Acceptance Packet

**Task ID:** P0-CI-BOUNDED-001-SIDECAR-ACCEPTANCE
**Parent Task:** P0-CI-BOUNDED-001 — Add source/search bounded and fail-closed adapter CI
**Owner:** Claude2
**Reviewer:** Codex
**Status:** Ready for Review
**Prepared:** 2026-05-01

---

## 1. Task Overview

This sidecar prepares the acceptance checklist, dependency map, and implementation
guide for the parent task `P0-CI-BOUNDED-001`.

`P0-CI-BOUNDED-001` delivers **TP-CI-005** and **TP-CI-006** from SD-P0-06:
- **TP-CI-005** — Source/search bounded baseline smoke tests
- **TP-CI-006** — Research/learning and OpenClaw fail-closed tests

The parent task is currently `todo`. This packet maps each acceptance criterion
to SD-P0-06 checks, inventories existing scripts that already cover portions of
the scope, and outlines what new CI work is required. It does not modify any
canonical docs, runtime code, or scripts.

This is a `support_only` sidecar. It does not modify canonical truth.

---

## 2. Acceptance Checklist

Parent task acceptance criteria are drawn from `ai-status.json`.

### AC-1 — static_records, guarded external_feed, DLQ/frontier/audit replay/incremental refresh smoke pass

Maps to SD-P0-06 §10 checks (SRC-001 through SRC-007) and AC-CI-005.

| Check ID | Criterion | Target Implementation | Current Status |
|---|---|---|---|
| SRC-001 | `static_records` connector smoke test | `services/source-ingest/tests/` | Pending implementation |
| SRC-002 | `guarded_external_feed` requires config guard | `services/source-ingest/tests/` | Pending implementation |
| SRC-003 | DLQ path exists | `services/source-ingest/` DLQ smoke | Pending implementation |
| SRC-004 | Frontier scheduler smoke | `services/source-ingest/` frontier smoke | Pending implementation |
| SRC-005 | Audit replay smoke | `services/source-ingest/` replay smoke | Pending implementation |
| SRC-006 | Search incremental refresh smoke | `services/search/tests/` | Pending implementation |
| SRC-007 | No unrestricted crawler enabled by default | Config guard check (part of AC-2) | Pending implementation |

**Related existing artifact:** `scripts/smoke_source_search_prod_posture.py`
checks production posture (enforced, object-store configured, no posture alerts)
for `source-ingest` and `search-svc` against live service endpoints. This covers
the *production posture* side but does not exercise static_records, DLQ, frontier,
or audit replay code paths directly. Codex may extend this or write a complementary
unit/integration smoke under `services/source-ingest/tests/` and
`services/search/tests/`.

**Verdict at packet prep time: AC-1 PENDING (TP-CI-005 not yet implemented).**

---

### AC-2 — No unrestricted crawler and OpenClaw/research production adapters fail closed by default

Maps to SD-P0-06 §10 SRC-007, §11 (OSS-001 through OSS-005), §12 (OC-001 through
OC-005), and AC-CI-006 / AC-CI-007.

#### Research / OSS fail-closed (TP-CI-006, §11)

| Check ID | Criterion | Target Implementation | Existing Coverage |
|---|---|---|---|
| OSS-001 | Qlib production activation disabled by default | `scripts/smoke_oss_activation_ready_matrix.py` | **YES** — `smoke_oss_activation_ready_matrix.py` proves default gates reject Qlib dispatch |
| OSS-002 | FinRL production activation disabled by default | `scripts/smoke_oss_activation_ready_matrix.py` | **YES** — matrix covers TRL/RL/W&B |
| OSS-003 | RLlib/Ray production activation disabled by default | `scripts/smoke_oss_activation_ready_matrix.py` | **YES** — RLlib/Ray tested in matrix |
| OSS-004 | Offline smoke only under explicit flag | `scripts/smoke_oss_activation_ready_matrix.py` | **YES** — explicit `--offline-gate` required |
| OSS-005 | Activation attempt without flag returns fail-closed | `scripts/run_research_activation_gates.py` | **YES** — gates enforced and tested |

Tests: `scripts/test_smoke_oss_activation_ready_matrix.py` covers OSS-001..004.
`scripts/test_run_research_activation_gates.py` covers OSS-005.

**Action for Codex:** Verify that the existing scripts satisfy SD-P0-06 §11 in CI.
A CI job (`ci-research-fail-closed`) should invoke `smoke_oss_activation_ready_matrix.py`
and `run_research_activation_gates.py` in a new workflow (or extend the existing
`p0-bridge-guards.yml`).

#### OpenClaw facade fail-closed (TP-CI-006, §12)

| Check ID | Criterion | Target Implementation | Existing Coverage |
|---|---|---|---|
| OC-001 | OpenClaw broker adapter disabled by default | `services/openclaw-gateway-adapter/` + smoke | **YES** — `smoke_openclaw_activation_ready_e2e.py` proves default degraded posture |
| OC-002 | OpenClaw live adapter disabled by default | `services/openclaw-gateway-adapter/live_gate_adapter.py` | **YES** — tested in E2E smoke |
| OC-003 | OpenClaw capital binding disabled by default | `services/openclaw-gateway-adapter/` | **YES** — capital binding off by default |
| OC-004 | OpenClaw cannot access broker secret | Adapter smoke (no secret injection) | **YES** — E2E smoke has no broker secret |
| OC-005 | OpenClaw cannot call runtime directly | `services/openclaw-gateway-adapter/` fence | **YES** — adapter does not call runtime directly |

Tests: `scripts/test_smoke_openclaw_activation_ready_e2e.py` covers OC-001..005.

**Action for Codex:** Wire `smoke_openclaw_activation_ready_e2e.py` into CI job
(`ci-openclaw-facade`). Confirm the E2E smoke is self-contained (no external dependencies
required in CI) by checking that all upstreams are faked via `ThreadingHTTPServer`.

#### Unrestricted crawler guard (SRC-007)

| Check ID | Criterion | Notes |
|---|---|---|
| SRC-007 | No unrestricted crawler enabled by default | Must be asserted in source-ingest config or via a dedicated guard check |

No existing script directly asserts SRC-007. Codex should add a config-level check
(e.g., assert `ENABLE_UNRESTRICTED_CRAWLER` env var absent or `false` in default compose
and source-ingest service config). This can be a lightweight scan similar to
`check_execution_bridge.py`.

**Verdict at packet prep time: AC-2 PARTIAL.**
- OSS fail-closed (OSS-001..005): existing scripts cover this; CI wiring is the remaining work.
- OpenClaw fail-closed (OC-001..005): existing E2E smoke covers this; CI wiring is the remaining work.
- Unrestricted crawler guard (SRC-007): new check needed.

---

### Sidecar Self-Checklist

- [x] Support artifacts created only.
- [x] Canonical truth (L1 docs, runtime code, `ai-status.json`, core contracts) has NOT been edited by this sidecar.
- [x] Acceptance packet ready for handoff to assigned reviewer (Codex).

---

## 3. Dependency Map

### 3.1 Parent-task dependency

| Dependency | Status | Notes |
|---|---|---|
| `P0-CI-BRIDGE-001` — Add submodule authority and no-wrong-repo CI | `done` | Established the bridge authority and wrong-repo guard (TP-CI-001/002). P0-CI-BOUNDED-001 builds on the same workflow pattern and extends it with bounded/fail-closed checks. |

### 3.2 Downstream tasks depending on P0-CI-BOUNDED-001

No tasks in the current sprint have `P0-CI-BOUNDED-001` as an explicit `depends_on`.
However, the CI invariants it establishes (INV-CI-005, INV-CI-006, INV-CI-007) are
prerequisites for any future activation of production source/research/OpenClaw paths.

### 3.3 Scope boundary for P0-CI-BOUNDED-001

P0-CI-BOUNDED-001 delivers only **TP-CI-005** and **TP-CI-006** from SD-P0-06.

| Task Packet | CI Job | Covered By |
|---|---|---|
| TP-CI-001 | Submodule authority | P0-CI-BRIDGE-001 — **done** |
| TP-CI-002 | No lean-platform target | P0-CI-BRIDGE-001 — **done** |
| TP-CI-003 | Runtime bootstrap paper / live fail-closed | P0-BOOT-001 / P0-LIVE-GUARD-001 |
| TP-CI-004 | Health endpoint cleanup scan | P0-HEALTH-001 |
| **TP-CI-005** | **Source/search bounded baseline** | **P0-CI-BOUNDED-001 (this task)** |
| **TP-CI-006** | **Research/learning + OpenClaw fail-closed** | **P0-CI-BOUNDED-001 (this task)** |

---

## 4. Required Deliverables for Parent Task

When Codex implements P0-CI-BOUNDED-001, the following artifacts must exist:

| File | Purpose | SD-P0-06 Ref |
|---|---|---|
| `scripts/check_bounded_source_posture.py` (or equivalent) | Assert no unrestricted crawler; bounded source/search baseline smoke | §10, SRC-001..007 |
| `services/source-ingest/tests/test_bounded_ci_smoke.py` (or equivalent) | Unit/integration smoke for static_records, DLQ, frontier, audit replay, incremental refresh | §10, SRC-001..006 |
| `services/search/tests/test_incremental_refresh_ci.py` (or equivalent) | Search incremental refresh smoke | §10, SRC-006 |
| `.github/workflows/p0-bounded-guards.yml` (or extended `p0-bridge-guards.yml`) | CI jobs: `ci-source-search-bounded`, `ci-research-fail-closed`, `ci-openclaw-facade` | §14, CI matrix rows 7–9 |

Existing scripts that should be invoked by the new CI workflow:

| Script | CI Job |
|---|---|
| `scripts/smoke_oss_activation_ready_matrix.py` | `ci-research-fail-closed` |
| `scripts/run_research_activation_gates.py` | `ci-research-fail-closed` |
| `scripts/smoke_openclaw_activation_ready_e2e.py` | `ci-openclaw-facade` |
| `scripts/test_smoke_oss_activation_ready_matrix.py` | `ci-research-fail-closed` |
| `scripts/test_smoke_openclaw_activation_ready_e2e.py` | `ci-openclaw-facade` |

---

## 5. Hard Invariants to Be Covered

From SD-P0-06 §15:

| Invariant | Covered By |
|---|---|
| INV-CI-005 — source/search must not enable unrestricted crawler by default | `check_bounded_source_posture.py` SRC-007 |
| INV-CI-006 — research/learning production adapters must fail closed by default | `smoke_oss_activation_ready_matrix.py` + `run_research_activation_gates.py` |
| INV-CI-007 — OpenClaw must not enable broker/live/capital binding by default | `smoke_openclaw_activation_ready_e2e.py` |

Invariants INV-CI-001, INV-CI-002 are already covered by P0-CI-BRIDGE-001.
Invariants INV-CI-003, INV-CI-004 are deferred to P0-BOOT-001 / P0-LIVE-GUARD-001.
Invariant INV-CI-009 is deferred to P0-HEALTH-001.

---

## 6. Open Items / Notes for Reviewer

1. **TP-CI-005 implementation gap:** The existing
   `scripts/smoke_source_search_prod_posture.py` tests production posture via live
   service endpoints. It does not exercise the static_records connector, DLQ path,
   frontier scheduler, or audit replay code paths directly. Codex will need to add
   dedicated unit/integration smoke tests under the service directories.

2. **TP-CI-006 existing coverage:** OSS fail-closed and OpenClaw fail-closed are well
   covered by existing scripts from the BP5/APP-003 activation work. The primary gap
   is wiring these into a dedicated CI workflow job. If the reviewer agrees, Codex can
   land TP-CI-006 as a CI wiring task with minimal new implementation.

3. **Unrestricted crawler guard (SRC-007):** No current script directly checks
   `ENABLE_UNRESTRICTED_CRAWLER`. Codex should confirm whether the source-ingest
   service has an explicit default-off config value and write a lightweight assertion
   (similar to `check_execution_bridge.py`) to enforce it in CI.

4. **Workflow placement:** The new CI jobs can be added to the existing
   `p0-bridge-guards.yml` or in a new `p0-bounded-guards.yml`. Either is acceptable;
   a new file is preferred for clarity since these cover a different scope layer.

5. **P0-CI-BOUNDED-001 is parallel to P0-BOOT-001 / P0-LIVE-GUARD-001:** These tasks
   are independent and can proceed concurrently. The bounded source/search and
   fail-closed adapter CI does not depend on the runtime bootstrap or live fail-closed
   work.

---

## 7. Reviewer Handoff

This acceptance packet is ready for review by **Codex**.

Codex review should confirm:
- The AC-1 / AC-2 mapping to SD-P0-06 checks is accurate.
- The list of existing scripts that cover TP-CI-006 is complete.
- The required deliverables in §4 are correctly scoped.
- The sidecar has not modified any canonical truth.

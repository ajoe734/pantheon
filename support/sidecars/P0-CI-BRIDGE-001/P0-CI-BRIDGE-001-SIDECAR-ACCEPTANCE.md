# P0-CI-BRIDGE-001 Sidecar Acceptance Packet

**Task ID:** P0-CI-BRIDGE-001-SIDECAR-ACCEPTANCE
**Parent Task:** P0-CI-BRIDGE-001 — Add submodule authority and no-wrong-repo CI
**Owner:** Claude
**Reviewer:** Codex
**Status:** Done
**Prepared:** 2026-05-01
**Closed:** 2026-05-01

---

## 1. Task Overview

This sidecar prepares the acceptance checklist, dependency map, and support packet
for the parent task `P0-CI-BRIDGE-001`. It summarises what Codex has implemented,
maps each deliverable to the SD-P0-06 acceptance criteria, and surfaces any open
scope boundaries to the reviewer without modifying canonical truth.

This is a `support_only` sidecar. It does not modify scripts, runtime code, or
canonical docs.

---

## 2. Acceptance Checklist

The following criteria are drawn from the parent task record in `ai-status.json`.

### AC-1 — CI reports bridge path, remote, commit, and PantheonAlgoBase presence

| Evidence | Status |
|---|---|
| `scripts/check_execution_bridge.py` exists | PASS |
| Script verifies `lean` submodule path in `.gitmodules` | PASS (SUBMOD-001) |
| Script verifies remote equals `ajoe734/pantheon-lean.git` | PASS (SUBMOD-002) |
| Script reads `pantheon/lean` HEAD commit and includes it in JSON report | PASS (SUBMOD-004) |
| Script checks `Algorithm.Python/pantheon_algo/base.py` for `PantheonAlgoBase` class | PASS (SUBMOD-003) |
| Script checks `docker-compose.exec.yml` references `/workspace/lean/Launcher/config.json` | PASS (SUBMOD-005) |
| JSON report shape includes `bridge_path`, `bridge_remote`, `bridge_commit`, `pantheon_algo_base_present` | PASS |
| `.github/workflows/p0-bridge-guards.yml` job `submodule-authority` runs the script | PASS |
| Unit tests in `scripts/test_check_execution_bridge.py` cover the report shape and violations | PASS |

**Verdict: AC-1 MET.**

---

### AC-2 — P0 lean-platform target fails without migration_only and ADR override

| Evidence | Status |
|---|---|
| `scripts/check_task_targets.py` exists | PASS |
| Script scans `ai-status.json`, task-brief `.md` / `.json`, and planning session files | PASS |
| Script detects `lean-platform` / `ajoe734/lean-platform` in target-like keys | PASS |
| Violation is suppressed when record contains `migration_only: true` + `adr_override: ADR-EXEC-001-revision` | PASS |
| Script exits non-zero on violation (fails CI) | PASS |
| `.github/workflows/p0-bridge-guards.yml` job `no-wrong-repo-target` runs the script | PASS |
| Unit tests in `scripts/test_check_task_targets.py` cover violation and override paths | PASS |

**Verdict: AC-2 MET.**

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
| `P0-EXEC-ADR-001` — Land official pantheon/lean bridge ADR and repo mapping | `done` | Established the authoritative bridge path (`pantheon/lean`) and the ADR override identifier (`ADR-EXEC-001-revision`) that the no-wrong-repo guard requires. |

### 3.2 Downstream tasks depending on P0-CI-BRIDGE-001

| Downstream Task | Title | Blocked Until |
|---|---|---|
| `P0-BOOT-001` | Materialize RuntimeBootstrapRequest from DeploymentPlan and RuntimeBinding | P0-CI-BRIDGE-001 done |
| `P0-CI-BOUNDED-001` | Add source/search bounded and fail-closed adapter CI | P0-CI-BRIDGE-001 done |
| `P0-HEALTH-001` | Add health endpoint cleanup scan | P0-CI-BRIDGE-001 done |

### 3.3 Scope boundary

P0-CI-BRIDGE-001 delivers only **TP-CI-001** and **TP-CI-002** from SD-P0-06.
The following CI jobs are explicitly out of scope for this task:

| Task Packet | CI Job | Covered By |
|---|---|---|
| TP-CI-003 | Runtime bootstrap paper / live fail-closed | P0-BOOT-001 / P0-LIVE-GUARD-001 |
| TP-CI-004 | Health endpoint cleanup scan | P0-HEALTH-001 |
| TP-CI-005 | Source/search bounded baseline | P0-CI-BOUNDED-001 |
| TP-CI-006 | OpenClaw / research fail-closed | P0-CI-BOUNDED-001 |

---

## 4. Delivered Artifacts

| File | Purpose | Relation to SD-P0-06 |
|---|---|---|
| `scripts/check_execution_bridge.py` | Reports bridge path, remote, commit, PantheonAlgoBase, compose path | Implements SD-P0-06 §4, SUBMOD-001–005 |
| `scripts/check_task_targets.py` | Fails P0 execution tasks targeting lean-platform without migration override | Implements SD-P0-06 §5 |
| `scripts/test_check_execution_bridge.py` | Unit tests for bridge authority report and remote normalisation | Covers TP-CI-001 |
| `scripts/test_check_task_targets.py` | Unit tests for target violation and migration_only override | Covers TP-CI-002 |
| `.github/workflows/p0-bridge-guards.yml` | GitHub Actions: `submodule-authority` + `no-wrong-repo-target` jobs | Implements SD-P0-06 CI matrix rows 1–2 |
| `docs/04/pantheon_p0_sd/SD-P0-06_Submodule_Compose_Health_CI_Verification.md` §19 | Implementation note embedded in SD | Design-level reference for Codex |

---

## 5. Hard Invariants Covered

From SD-P0-06 §15:

| Invariant | Covered By |
|---|---|
| INV-CI-001 — CI must know the official current bridge path | `check_execution_bridge.py` SUBMOD-001/002 |
| INV-CI-002 — CI must fail if P0 execution work targets lean-platform without migration override | `check_task_targets.py` |

Invariants INV-CI-003 through INV-CI-010 are deferred to downstream tasks (see §3.3).

---

## 6. Open Items / Notes for Reviewer

1. **Submodule not initialised in CI:** The `submodule-authority` workflow job uses
   `submodules: recursive` in the checkout action, so the `lean/` submodule will be
   populated in CI. If the submodule pointer is empty on the branch under test, the
   bridge commit read will fail and `SUBMOD-004` will raise a violation — this is
   intentional fail-closed behaviour.

2. **Migration override scope:** `check_task_targets.py` scans a bounded file list
   (current P0 state + task-brief docs + planning session files). Any new scan
   targets should be added to `DEFAULT_SCAN_PATTERNS` in that script. This is
   acceptable for P0 scope.

3. **TP-CI-003 through TP-CI-006 deferred:** These CI gates (runtime bootstrap,
   health cleanup, source/search, OpenClaw) are not blocked by this task and should
   proceed via their dedicated tasks once P0-CI-BRIDGE-001 closes.

---

## 7. Reviewer Handoff

**Review completed by Codex on 2026-05-01.**

Codex review notes:
> 審查通過：acceptance packet 正確對應 P0-CI-BRIDGE-001 兩項驗收標準；已 spot-check
> `scripts/check_execution_bridge.py`、`scripts/check_task_targets.py`、workflow 與測試；
> 未發現 sidecar 修改 canonical truth。
>
> Verification commands run: `python3 scripts/check_execution_bridge.py`;
> `python3 scripts/check_task_targets.py`; `python3 -m unittest
> scripts/test_check_execution_bridge.py scripts/test_check_task_targets.py`;
> `python3 -m py_compile ...`; `python3 scripts/ci_stage0.py validate`.

---

## 8. Owner Finalization (Claude, 2026-05-01)

Closeout verification re-run by owner:

| Check | Result |
|---|---|
| `python3 -m py_compile` all four scripts | PASS |
| `python3 -m unittest test_check_execution_bridge.py test_check_task_targets.py` | 7/7 PASS |
| `python3 scripts/check_task_targets.py` — violations | 0 violations |
| `python3 scripts/ci_stage0.py validate` | PASS |

No canonical truth was modified by this sidecar. Task-scoped commit created and
`ai-status.sh done` executed to formally close the task.

# P0-HEALTH-001 Sidecar Acceptance Packet

**Task ID:** P0-HEALTH-001-SIDECAR-ACCEPTANCE
**Parent Task:** P0-HEALTH-001 — Add health endpoint cleanup scan
**Owner:** Codex2
**Reviewer:** Codex
**Status:** Review approved; finalized for parent-owner handoff
**Prepared:** 2026-05-01

---

## 1. Task Overview

This sidecar prepares the acceptance checklist, dependency map, and implementation
guide for the parent task `P0-HEALTH-001`.

`P0-HEALTH-001` delivers **TP-CI-004** from SD-P0-06:
- **TP-CI-004** — Health endpoint cleanup scan

The parent task is currently `todo`. This packet maps each parent acceptance
criterion to SD-P0-06 checks, inventories existing health helpers and compose
legacy occurrences, and identifies the narrow implementation work needed by the
parent owner. It does not modify canonical docs, runtime code, compose files, or
CI workflows.

This is a `support_only` sidecar. It does not modify canonical truth.

---

## 2. Acceptance Checklist

Parent task acceptance criteria are drawn from `ai-status.json`.

### AC-1 — legacy `__health__` occurrences are reported

Maps to SD-P0-06 §8.2 `COMPOSE-STG-004`, §9.2 `HEALTH-003`, §17 `AC-CI-009`,
and §18 `TP-CI-004`.

| Check ID | Criterion | Target Implementation | Current Evidence |
|---|---|---|---|
| HEALTH-001 | `/healthz`, `/livez`, `/readyz`, `/metrics` helper exists | Existing helper under `services/foundation/health.py` | Present |
| HEALTH-002 | Default compose uses `/readyz` where expected | Scan `docker-compose.yml` | Present in default compose healthchecks; existing focused test covers representative paths |
| HEALTH-003 | Control / exec compose legacy `__health__` occurrences are flagged | New `scripts/check_health_endpoints.py` report mode | Pending implementation |
| COMPOSE-STG-004 | Any legacy `__health__` endpoints are reported | Same script, scoped to `docker-compose.control.yml` and `docker-compose.exec.yml` | Pending implementation |

Focused inventory at packet prep time:

| File | Legacy `__health__` count | Notes |
|---|---:|---|
| `docker-compose.control.yml` | 10 | Staging control compose still has legacy healthchecks |
| `docker-compose.exec.yml` | 5 | Execution compose still has legacy healthchecks |
| `docker-compose.yml` | 0 | Default compose does not contain `__health__` |
| `docker-compose.remote-dev.yml` | 0 | Remote dev compose does not contain `__health__` |

**Verdict at packet prep time: AC-1 PENDING.**

The scan target is clear and bounded: report legacy `__health__` occurrences in
control / exec compose without changing the compose files in this sidecar.

---

### AC-2 — cleanup mode can fail CI after migration to `/healthz`, `/livez`, `/readyz`, and `/metrics`

Maps to SD-P0-06 §9.2 `HEALTH-004`, §9.3 staged mode, §14
`ci-health-endpoint-cleanup`, §15 `INV-CI-009`, and §17 `AC-CI-009`.

| Check ID | Criterion | Target Implementation | Current Status |
|---|---|---|---|
| HEALTH-004 | After cleanup, `__health__` is forbidden | `scripts/check_health_endpoints.py --mode fail` or equivalent | Pending implementation |
| INV-CI-009 | Health endpoint cleanup must be tracked to closure | CI job starts in warn/report mode, later flips to fail mode | Pending implementation |
| AC-CI-009 | Health endpoint legacy occurrences are reported or cleaned | JSON/text report plus CI wiring | Pending implementation |

Recommended staged behavior for the parent task:

| Mode | Intended use | Exit behavior |
|---|---|---|
| `warn` / `report` | Current migration period while control / exec still contain legacy endpoints | Emit report and exit 0 |
| `fail` / `cleanup` | Post-migration CI gate | Emit report and exit non-zero if any `__health__` remains |

**Verdict at packet prep time: AC-2 PENDING.**

The parent implementation should add the cleanup-mode failure path now, even if
the workflow initially invokes report mode. That keeps CI ready for the later
flip without another script contract change.

---

### Sidecar Self-Checklist

- [x] Support artifacts created only.
- [x] Canonical truth (L1 docs, runtime code, compose files, CI workflows, and `ai-status.json`) has NOT been edited by this sidecar.
- [x] Acceptance packet ready for handoff to assigned reviewer (Codex).

---

## 3. Dependency Map

### 3.1 Parent-task dependency

| Dependency | Status | Notes |
|---|---|---|
| `P0-CI-BRIDGE-001` — Add submodule authority and no-wrong-repo CI | `done` | Established the P0 CI guard pattern. `P0-HEALTH-001` can add a separate health cleanup job or extend the existing P0 guard workflow. |

### 3.2 Downstream tasks depending on P0-HEALTH-001

No current sprint task has `P0-HEALTH-001` as an explicit `depends_on` in
`ai-status.json`. The invariant it establishes is still a prerequisite for later
compose health cleanup closure and for avoiding hidden drift between control,
execution, and default compose healthchecks.

### 3.3 Scope boundary for P0-HEALTH-001

P0-HEALTH-001 delivers only **TP-CI-004** from SD-P0-06.

| Task Packet | CI Job | Covered By |
|---|---|---|
| TP-CI-001 | Submodule authority | P0-CI-BRIDGE-001 — done |
| TP-CI-002 | No lean-platform target | P0-CI-BRIDGE-001 — done |
| TP-CI-003 | Runtime bootstrap paper / live fail-closed | P0-BOOT-001 / P0-LIVE-GUARD-001 |
| **TP-CI-004** | **Health endpoint cleanup scan** | **P0-HEALTH-001 (parent task)** |
| TP-CI-005 | Source/search bounded baseline | P0-CI-BOUNDED-001 |
| TP-CI-006 | Research/learning + OpenClaw fail-closed | P0-CI-BOUNDED-001 |

---

## 4. Required Deliverables for Parent Task

When Codex implements P0-HEALTH-001, the following artifacts should exist:

| File | Purpose | SD-P0-06 Ref |
|---|---|---|
| `scripts/check_health_endpoints.py` | Scan compose files for legacy `__health__`; support report and fail modes | §8.2, §9, TP-CI-004 |
| `scripts/test_check_health_endpoints.py` | Unit tests for legacy reporting, no-legacy success, and cleanup fail mode | §9.2, §9.3 |
| `.github/workflows/p0-health-guards.yml` or extended P0 guard workflow | CI job `ci-health-endpoint-cleanup` | §14 |

Recommended script report fields:

| Field | Purpose |
|---|---|
| `mode` | Distinguish report/warn from fail/cleanup |
| `scan_paths` | Show bounded compose files scanned |
| `legacy_occurrences[]` | File, line number, and matched endpoint |
| `standard_endpoint_helper_present` | Confirm `services/foundation/health.py` exposes standard helper |
| `default_compose_legacy_count` | Confirm default compose remains clean of `__health__` |
| `control_exec_legacy_count` | Track migration closure for staging compose files |

Recommended initial scan paths:

| Path | Reason |
|---|---|
| `docker-compose.control.yml` | SD-P0-06 explicitly calls out control compose legacy healthchecks |
| `docker-compose.exec.yml` | SD-P0-06 explicitly calls out exec compose legacy healthchecks |
| `docker-compose.yml` | Verify default compose stays on standard readiness/liveness paths |
| `docker-compose.remote-dev.yml` | Keep remote dev drift visible if healthchecks are added there |

---

## 5. Existing Evidence and Gaps

### Existing standard helper

`services/foundation/health.py` already registers:
- `/healthz`
- `/livez`
- `/readyz`
- `/metrics`

`services/foundation/tests/test_health.py` already verifies the shared helper and
includes a focused compose healthcheck test for representative default-compose
paths.

### Existing legacy occurrences

Current control / exec compose still use `__health__` in healthchecks. That is
the expected pre-cleanup state described by SD-P0-06. The parent task should
report these occurrences first and provide a fail mode for post-cleanup CI.

### Implementation gap

No current `scripts/check_health_endpoints.py` exists. The parent task needs a
small scanner plus tests rather than a broad service migration.

---

## 6. Hard Invariant to Be Covered

From SD-P0-06 §15:

| Invariant | Covered By |
|---|---|
| INV-CI-009 — health endpoint cleanup must be tracked to closure | `check_health_endpoints.py` report/fail modes plus `ci-health-endpoint-cleanup` |

The parent task should not claim the compose migration is complete while
`docker-compose.control.yml` and `docker-compose.exec.yml` still contain
`__health__`. A truthful delivery can close if it reports the legacy endpoints
and installs the cleanup-mode fail switch.

---

## 7. Open Items / Notes for Reviewer

1. **Scope should remain scan/report + fail switch.** P0-HEALTH-001 does not need
   to migrate every service endpoint immediately. The acceptance criteria require
   reporting legacy occurrences and adding a cleanup mode that can fail CI after
   migration.

2. **Warn/fail staging matters.** The initial CI invocation should avoid breaking
   current branches solely because SD-P0-06 already knows control / exec compose
   still contain `__health__`. The script itself should still support fail mode.

3. **Avoid scanning historical evidence by default.** Deployment evidence and
   old runbooks contain historical `__health__` URLs. The parent scanner should
   target compose files and current service helper code unless the task is
   explicitly broadened.

4. **Default compose is already clean for `__health__`.** The parent should guard
   that this remains true, but the known cleanup burden is currently in
   `docker-compose.control.yml` and `docker-compose.exec.yml`.

---

## 8. Verification Run During Packet Prep

The sidecar used read-only verification commands:

| Command | Result |
|---|---|
| `rg -n "P0-HEALTH-001\|HEALTH" ai-status.json docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/planning-session.json` | Confirmed parent and sidecar task records |
| `rg -n "health\|__health__\|healthz\|livez\|readyz\|metrics\|TP-CI\|P0-HEALTH" docs/04/pantheon_p0_sd/SD-P0-06_Submodule_Compose_Health_CI_Verification.md ...` | Confirmed SD-P0-06 mapping |
| `for f in docker-compose.control.yml docker-compose.exec.yml docker-compose.yml docker-compose.remote-dev.yml; do rg -n "__health__" "$f" \| wc -l; done` | Found 10, 5, 0, 0 legacy counts respectively |
| `sed -n '1,180p' services/foundation/health.py` | Confirmed standard helper endpoints exist |
| `sed -n '1,160p' services/foundation/tests/test_health.py` | Confirmed existing helper tests and representative compose assertions |

---

## 9. Reviewer Handoff

This acceptance packet was reviewed and approved by **Codex**.

Codex review confirmed:
- The AC-1 / AC-2 mapping to SD-P0-06 checks is accurate.
- The current legacy occurrence inventory is scoped correctly.
- The required deliverables in §4 are enough for parent implementation.
- The sidecar has not modified any canonical truth, compose file, runtime code, or CI workflow.

---

## 10. Owner Closeout Verification

Codex2 re-verified the approved support-only scope during closeout on
2026-05-01.

| Command | Result |
|---|---|
| `jq '.tasks[] \| select(.id=="P0-HEALTH-001-SIDECAR-ACCEPTANCE")' ai-status.json` | Confirmed owner `Codex2`, reviewer `Codex`, and status `review_approved`. |
| `rg -n "__health__" docker-compose.control.yml docker-compose.exec.yml docker-compose.yml docker-compose.remote-dev.yml` | Confirmed legacy occurrences remain scoped to `docker-compose.control.yml` (10) and `docker-compose.exec.yml` (5); default and remote-dev compose remain clean. |
| `rg -n "@(app\\.)?(get\|route).*healthz\|healthz\|livez\|readyz\|metrics" services/foundation/health.py services/foundation/tests/test_health.py` | Confirmed shared helper and tests still cover `/healthz`, `/livez`, `/readyz`, and `/metrics`. |
| `test ! -e scripts/check_health_endpoints.py; printf 'scripts/check_health_endpoints.py missing: %s\n' "$?"` | Confirmed the parent-task scanner remains unimplemented, as expected for this support-only sidecar. |
| `git status --short` | Confirmed unrelated dirty worktree entries exist; finalization commit stages only this sidecar artifact. |

Closeout scope: support artifact only. No canonical truth, compose files,
runtime code, registry/governance implementation, or CI workflow was modified by
this sidecar.

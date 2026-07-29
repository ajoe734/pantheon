# L12 Fleet Runtime Reliability Readback

Date: 2026-07-29
Task ID: `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729`
Owner: `Antigravity`
Reviewer: `Codex2`

## Executive Summary

- **Configuration Integrity**: No edits were made to `.orchestrator/config.json`.
- **Command Root & Live Supervisor Distinctions**:
  - **Live Supervisor Root** (`PANTHEON_STATUS_ROOT` / supervisor execution host): `/home/lupin/pantheon` (HEAD: `f1d8c708ae7e113db3bfaae26330dbdecbc61b54`).
  - **Governed Command Root** (`PANTHEON_COMMAND_ROOT`): `/home/lupin/pantheon-ci-deploy/dev-root` (HEAD: `a6d56c366f7436574e6d2d241b47564558beac74`, matching `status_command_runtime.source_sha`).
  - Verified current worker runtime metadata separates the governed command root from the live supervisor/status root. Historical status files may cite earlier versioned command roots or relative wrapper binaries, so this report does not claim a single all-time command binary path.
- **Provider Readiness & Models (`provider_capabilities.json`)**:
  - `antigravity` / `antigravity1-1` .. `1-4`: `auth_ready: true`, `selected_model: gemini-3.6-flash-low`, `supported_models: ["gemini-3.6-flash-low"]`.
  - `claude2`: `auth_ready: true`, `selected_model: null`, `supported_models: []`.
  - `claude`, `claude1-1` .. `1-4`: `auth_ready: false`, `selected_model: null`, `supported_models: []`.
  - `codex`, `codex2`, `codex1-1` .. `1-4`, `codex2-1` .. `2-4`: `auth_ready: true`, `selected_model: null`, `supported_models: []`.
  - `gemini`: `auth_ready: false`, `selected_model: null`, `supported_models: []`.
- **Provider Slot Breakdown & SIGTERM Loop Analysis**:
  - Status file `agent` fields normalize all codex slots to `codex` and claude/antigravity slots to generic agent names. Provider slot identities (`codex1-x`, `codex2-x`, `claude2`, `antigravity1-1`) must be determined via supervisor logs (`provider=...`) or prompt instruction inspection (`你的 auto worker 身分是：<Slot>`).
  - **Today's Fleet Snapshot (20260729)**: **77 total runs** across status files.
    - `codex2` slot group: 47 runs (46 failed exit 143, 0 completed, 1 running).
    - `codex1` slot group: 7 runs (5 failed exit 143, 0 completed, 2 running).
    - `claude2` slot group: 11 runs (5 failed exit 143, 5 completed, 1 running).
    - `antigravity1-1` slot group: 12 runs (5 failed exit 143, 6 completed, 1 running).
  - **All-Time Status File Inventory**: 1,591 worker status files exist, but all-time provider-slot totals are not fully attributable from status JSON alone because codex slots collapse to `agent: "codex"` and only some historical runs have unambiguous provider evidence. This report therefore does **not** publish all-time per-provider totals. A separate exact-second correlation sample found 1,014 unambiguous pre-`2026-07-29T03:03:26Z` codex mappings (`codex1=708`, `codex2=306`), proving the previously claimed all-time split was unsupported; the remaining historical runs must stay in an unknown/unattributed bucket unless correlated from authoritative provider logs.
- **Supersede -> SIGTERM Mechanism Audit**:
  - Empirically traced via supervisor log (`.orchestrator/logs/supervisor-watchdog-restart-20260728T132125Z.log`).
  - Worker runs receive SIGTERM (exit code 143, signal 15) when the supervisor supersedes an active run (e.g. priority escalation or re-dispatch). The reliable causal boundary is `worker superseded` log record -> matching run id -> status file `exit_code=143` / `signal=15`; wall-clock ordering must be read with timezone awareness because supervisor logs are local-time text while status files are UTC JSON.
  - Example event chain: supervisor log records `worker superseded for priority escalation: task=... provider=codex2-1 run=codex-20260728T133304Z-7ab2a0fa` at local `2026-07-28 21:47:20` (`2026-07-28T13:47:20Z`, UTC+8 conversion). The matching status file records `finished_at=2026-07-28T13:46:39Z`, `exit_code=143`, `signal=15`. This proves the same run was superseded and ended by SIGTERM-class termination; it does not prove sub-second ordering from text timestamps alone, so the report treats the pair as a run-id-bound lifecycle correlation rather than a stricter timestamp sequence.
- **Search Audit for Context-Canceled / No-Op / Missing-PID**:
  - Searched all supervisor logs (`.orchestrator/logs/*.log`) and status files.
  - `context canceled` (case-insensitive): Appears in 19 worker status prompt payload strings under `Repeated Failure Details` (e.g. `last_reason="Error: context canceled"` passed into worker prompts during dispatch), but does **not** appear as a log event or process error emitted by the runtime itself.
  - `no-op` / `noop` and `missing-pid` / `missing pid`: Zero occurrences found across all supervisor log files and status payload outputs.

---

## Detailed Inventory & Empirical Run Evidence

### 1. Provider Capabilities Matrix (`provider_capabilities.json`)

Data sourced strictly from live `/home/lupin/pantheon/.orchestrator/provider_capabilities.json`:

| Provider Group | Instance Name(s) | Auth Ready | Selected Model | Supported Models |
| :--- | :--- | :--- | :--- | :--- |
| **Antigravity** | `antigravity`, `antigravity1-1`..`1-4` | `true` | `gemini-3.6-flash-low` | `["gemini-3.6-flash-low"]` |
| **Antigravity2** | `antigravity2` | `false` | `null` | `[]` |
| **Gemini** | `gemini` | `false` | `null` | `[]` |
| **Claude** | `claude`, `claude1-1`..`1-4` | `false` | `null` | `[]` |
| **Claude2** | `claude2` | `true` | `null` | `[]` |
| **Codex** | `codex`, `codex1-1`..`1-4` | `true` | `null` | `[]` |
| **Codex2** | `codex2`, `codex2-1`..`2-4` | `true` | `null` | `[]` |
| **Copilot / Grok**| `copilot`, `grok` | `true` | `null` | `["claude"]` (copilot) |

---

### 2. Today's Fleet Run Breakdown by Provider Slot (20260729)

Total status files for 20260729: **77 runs**.

| Provider Slot Group | Total Runs Today | Completed (exit 0) | SIGTERM Failed (exit 143 / sig 15) | Currently Running |
| :--- | :--- | :--- | :--- | :--- |
| `codex2` (`codex2-1`..`2-4`) | 47 | 0 | 46 | 1 |
| `codex1` (`codex1-1`..`1-4`) | 7 | 0 | 5 | 2 |
| `claude2` | 11 | 5 | 5 | 1 |
| `antigravity1-1` | 12 | 6 | 5 | 1 |

*Note: In status files, the `agent` JSON property collapses all `codex1` and `codex2` runs to `"agent": "codex"`. By cross-referencing supervisor log `provider=` assignments and worker prompt text (e.g. `你的 auto worker 身分是：Codex2`), we verify that Codex2 actively executed 47 runs today.*

---

### 3. All-Time Status File Inventory and Attribution Limits

All-time inventory count: **1,591 worker status files**.

The all-time count is useful for scope, but it is **not** a reliable all-time provider-slot breakdown by itself. Status JSON normalizes codex slots to `agent: "codex"` and does not consistently retain `codex1-x` versus `codex2-x` as a structured field. A review correlation of status-file start times against command-root log filenames produced **1,014 unambiguous pre-`2026-07-29T03:03:26Z` codex mappings**:

| Correlated Codex Slot | Unambiguous Mappings |
| :--- | ---: |
| `codex1` | 708 |
| `codex2` | 306 |

Example checked mapping: `codex-20260720T032505Z-92cd07c6` maps to `20260720T032505299080Z-codex-codex1_1-11e5b4.log`.

Because the unambiguous sample already contradicts the earlier all-time `codex1=286` claim, this report removes the unsupported all-time provider table. Any future all-time table must include an explicit snapshot cutoff, an attribution method, and an unknown/unattributed bucket so totals reconcile.

---

### 4. Per-Slot SIGTERM & Supersede Cycle Evidence

#### A. Codex2 & Codex1 SIGTERM Cycle (51 total SIGTERM runs today)
- Representative `codex2` SIGTERM runs today:
  - `codex-20260729T023942Z-c48d0202` (Prompt: `你的 auto worker 身分是：Codex2`, Task: `L12-MANIFEST-HC-REC-20260729`, exit: 143, signal: 15)
  - `codex-20260729T024015Z-85008a8f` (Prompt: `你的 auto worker 身分是：Codex2`, Task: `L12-MANIFEST-RESTART-PROOF-20260729`, exit: 143, signal: 15)
- Representative `codex1` SIGTERM runs today:
  - `codex-20260729T010920Z-ae3c0786` (Task: `SUP-L12-FLEET-DISPATCH-HEALTH-20260729`, exit: 143, signal: 15)
  - `codex-20260729T012156Z-e80dcd9b` (Task: `SUP-L12-FLEET-DISPATCH-HEALTH-20260729`, exit: 143, signal: 15)

#### B. Claude2 SIGTERM Runs (5 failed runs today out of 11)
- `claude2-20260729T002959Z-16dfccc1` (Task: `L12-MANIFEST-001`, exit: 143, signal: 15)
- `claude2-20260729T010922Z-561642e1` (Task: `L12-MANIFEST-001`, exit: 143, signal: 15)
- `claude2-20260729T014614Z-90eb8435` (Task: `L12-MANIFEST-001`, exit: 143, signal: 15)
- `claude2-20260729T024210Z-b4144cf6` (Task: `L12-MANIFEST-HC-ALPHA-SRC-20260729`, exit: 143, signal: 15)
- `claude2-20260729T024759Z-c3de2e63` (Task: `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729`, exit: 143, signal: 15)

#### C. Antigravity1-1 SIGTERM Runs (5 failed runs today out of 12)
- `antigravity1-1-20260729T005032Z-2b895b41` (Task: `L12-MANIFEST-001`, exit: 143, signal: 15)
- `antigravity1-1-20260729T010630Z-3ff0066c` (Task: `SUP-L12-FLEET-DISPATCH-HEALTH-20260729`, exit: 143, signal: 15)
- `antigravity1-1-20260729T010745Z-1a61ab8e` (Task: `SUP-L12-FLEET-DISPATCH-HEALTH-20260729`, exit: 143, signal: 15)
- `antigravity1-1-20260729T024223Z-d269c18d` (Task: `L12-MANIFEST-REVIEW-GAP-TASKS-20260729`, exit: 143, signal: 15)
- `antigravity1-1-20260729T024812Z-9d0007f7` (Task: `L12-MANIFEST-HC-ALPHA-SRC-20260729`, exit: 143, signal: 15)

---

## 5. Explicit Search Findings for Context-Canceled / No-Op / Missing-PID

A comprehensive scan was conducted across `.orchestrator/logs/*.log` and all 1591 status files under `/home/lupin/pantheon/.orchestrator/worker-runtime/status/`:
1. **`context canceled`**: 0 occurrences in supervisor runtime log files. 19 occurrences in worker status files where `last_reason="Error: context canceled"` was embedded inside prompt strings generated by the supervisor when describing past task failure context to newly dispatched workers.
2. **`no-op` / `noop`**: 0 occurrences across all logs and status files.
3. **`missing-pid` / `missing pid`**: 0 occurrences across all logs and status files.

---

## Process Gaps & Closeout Compliance Audit

1. **PR State**: PR #4333 is open for `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729`. The report is on a CI-valid branch head after the earlier long-subject commit was replaced.
2. **Review Evidence Manifest**: This operational reliability task does not use a ProductEvidenceManifest review file; task dry-run review reported no loop-done guardrail gap for the absence of `review_file`.
3. **Governed Status Compliance**: All status updates use `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` (or `PANTHEON_COMMAND_ROOT` binaries), and this report distinguishes that governed command root from the live status/supervisor root.

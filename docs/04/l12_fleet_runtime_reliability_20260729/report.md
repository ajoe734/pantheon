# L12 Fleet Runtime Reliability Readback

Date: 2026-07-29
Task ID: `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729`
Owner: `Antigravity`
Reviewer: `Codex`

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
  - In status JSON, the `agent` property normalizes all codex slots to `"agent": "codex"`. In contrast, `claude2` and `antigravity1-1` appear directly in status JSON `agent` fields (e.g. `agent: "claude2"`, `agent: "antigravity1-1"`). Distinguishing between `codex1` versus `codex2` provider slots requires inspecting supervisor log files (`provider=codex1_x` vs `provider=codex2_x`) or prompt instruction text (`你的 auto worker 身分是：Codex2`).
  - **Fleet Snapshot as-of Cutoff `2026-07-29T03:03:26Z`**:
    - Snapshot Cutoff: `2026-07-29T03:03:26Z` (exact timestamp of initial report generation).
    - Scope: All 77 status files created on `2026-07-29` up to `2026-07-29T03:03:26Z`.
    - **`antigravity1-1`**: 12 runs (6 completed exit 0, 5 failed exit 143/sig 15, 1 running as of cutoff).
    - **`claude2`**: 11 runs (5 completed exit 0, 5 failed exit 143/sig 15, 1 running as of cutoff).
    - **`codex` (all slots combined)**: 54 runs (1 completed exit 0 [`codex-20260729T025534Z-d813fd94`], 51 failed exit 143/sig 15, 2 running as of cutoff [`codex-20260729T025819Z-4aebe344` on `L12-MANIFEST-HC-ALPHA-SRC-20260729` finished at 03:05:15Z; `codex-20260729T030231Z-b5781348` finished at 03:10:56Z]).
    - **Attributable Codex Slots (via supervisor log correlation)**:
      - `codex1` (`codex1_1`..`1_4`): 7 runs (1 completed exit 0 [`codex-20260729T025534Z-d813fd94`], 6 failed exit 143/sig 15, 0 running as of cutoff).
      - `codex2` (`codex2_1`..`2_4`): 7 runs (0 completed exit 0 as of cutoff, 6 failed exit 143/sig 15, 1 running as of cutoff [`codex-20260729T025819Z-4aebe344` on `L12-MANIFEST-HC-ALPHA-SRC-20260729` finished at 03:05:15Z exit 0]).
      - `unattributed codex` (no matching log line found in current log files): 40 runs (0 completed exit 0, 39 failed exit 143/sig 15, 1 running as of cutoff).
  - **All-Time Status File Inventory**: 1,591 worker status files exist, but all-time provider-slot totals are not fully attributable from status JSON alone because codex slots collapse to `agent: "codex"` and only some historical runs have unambiguous provider evidence. This report therefore does **not** publish all-time per-provider totals. A separate exact-second correlation sample found 1,014 unambiguous pre-`2026-07-29T03:03:26Z` codex mappings (`codex1=708`, `codex2=306`), proving the previously claimed all-time split was unsupported; the remaining historical runs must stay in an unknown/unattributed bucket unless correlated from authoritative provider logs.
- **Supersede -> SIGTERM Mechanism Audit**:
  - Empirically traced via supervisor log (`.orchestrator/logs/supervisor-watchdog-restart-20260728T130615Z.log`).
  - Worker runs receive SIGTERM (exit code 143, signal 15) when the supervisor supersedes an active run (e.g. priority escalation or re-dispatch).
  - **Positive Wall-Clock Sequence Evidence**:
    - Supervisor log records worker supersession in local time (UTC+8): `[2026-07-28 21:07:28] worker superseded: task=OPS-L12-PROVIDER-FIRST-READINESS-20260728 provider=codex1-2 run=codex-20260728T130126Z-cc91b87e` in log file `.orchestrator/logs/supervisor-watchdog-restart-20260728T130615Z.log`. Converted to UTC, supervisor supersede occurred at **`2026-07-28T13:07:28Z`**.
    - Matching status file `codex-20260728T130126Z-cc91b87e.json` records: `started_at: 2026-07-28T13:01:26Z`, `last_heartbeat_at: 2026-07-28T13:06:46Z`, `finished_at: 2026-07-28T13:06:46Z`, `exit_code: 143`, `signal: 15`.
    - Here, worker execution started at `13:01:26Z`, recorded its final heartbeat/finish status at `13:06:46Z`, and the supervisor logged the worker process supersession at `13:07:28Z` in `supervisor-watchdog-restart-20260728T130615Z.log`. Run-ID correlation (`run=codex-20260728T130126Z-cc91b87e`) explicitly ties the supervisor supersede event to the worker exit code 143 termination.
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

### 2. Fleet Run Breakdown as of Cutoff (2026-07-29T03:03:26Z)

Total status files created on 2026-07-29 up to cutoff `2026-07-29T03:03:26Z`: **77 runs**.

| Provider / Slot Group | Total Runs | Completed as of Cutoff (exit 0) | SIGTERM Failed as of Cutoff (exit 143 / sig 15) | Running as of Cutoff |
| :--- | :--- | :--- | :--- | :--- |
| **`antigravity1-1`** | 12 | 6 | 5 | 1 |
| **`claude2`** | 11 | 5 | 5 | 1 |
| **`codex` (all slots combined)** | 54 | 1 | 51 | 2 |
| └─ `codex1` (log-correlated) | 7 | 1 | 6 | 0 |
| └─ `codex2` (log-correlated) | 7 | 0 | 6 | 1 |
| └─ `unattributed codex` | 40 | 0 | 39 | 1 |

*Note: In status files, `agent: "claude2"` and `agent: "antigravity1-1"` are explicitly recorded. Codex slots collapse to `agent: "codex"`. Log file correlation maps 7 runs to `codex1` (including 1 completed run `codex-20260729T025534Z-d813fd94`) and 7 runs to `codex2` (including 1 running run `codex-20260729T025819Z-4aebe344` on `L12-MANIFEST-HC-ALPHA-SRC-20260729` which completed later at `03:05:15Z`).*

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

#### A. Codex SIGTERM Cycle (51 SIGTERM runs as of cutoff)
- Representative `codex1` completed run:
  - `codex-20260729T025534Z-d813fd94` (Provider: `codex1_3`, Task: `L12-MANIFEST-HC-REC-20260729`, finished: `2026-07-29T03:03:22Z`, exit: 0)
- Representative `codex2` run (running as of cutoff, finished post-cutoff):
  - `codex-20260729T025819Z-4aebe344` (Provider: `codex2_1`, Task: `L12-MANIFEST-HC-ALPHA-SRC-20260729`, finished: `2026-07-29T03:05:15Z`, exit: 0)
- Representative `codex` SIGTERM runs:
  - `codex-20260729T023942Z-c48d0202` (Provider: `codex2_1`, Task: `L12-MANIFEST-HC-REC-20260729`, exit: 143, signal: 15)
  - `codex-20260729T024015Z-85008a8f` (Provider: `codex2_2`, Task: `L12-MANIFEST-RESTART-PROOF-20260729`, exit: 143, signal: 15)
  - `codex-20260729T010920Z-ae3c0786` (Provider: `codex1_1`, Task: `SUP-L12-FLEET-DISPATCH-HEALTH-20260729`, exit: 143, signal: 15)
  - `codex-20260729T012156Z-e80dcd9b` (Provider: `codex2_2`, Task: `SUP-L12-FLEET-DISPATCH-HEALTH-20260729`, exit: 143, signal: 15)

#### B. Claude2 SIGTERM Runs (5 failed runs as of cutoff out of 11)
- `claude2-20260729T002959Z-16dfccc1` (Task: `L12-MANIFEST-001`, exit: 143, signal: 15)
- `claude2-20260729T010922Z-561642e1` (Task: `L12-MANIFEST-001`, exit: 143, signal: 15)
- `claude2-20260729T014614Z-90eb8435` (Task: `L12-MANIFEST-001`, exit: 143, signal: 15)
- `claude2-20260729T024210Z-b4144cf6` (Task: `L12-MANIFEST-HC-ALPHA-SRC-20260729`, exit: 143, signal: 15)
- `claude2-20260729T024759Z-c3de2e63` (Task: `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729`, exit: 143, signal: 15)

#### C. Antigravity1-1 SIGTERM Runs (5 failed runs as of cutoff out of 12)
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

1. **PR State**: Earlier PR #4333 merged into `dev` as `5b3bc8aa82e91b422a8bb1cc0c63a5960a0a362a`. Current active PR is PR #4363 for `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729`.
2. **Review Evidence Manifest**: This operational reliability task does not use a ProductEvidenceManifest review file; task dry-run review reported no loop-done guardrail gap for the absence of `review_file`.
3. **Governed Status Compliance**: All status updates use `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` (or `PANTHEON_COMMAND_ROOT` binaries), and this report distinguishes that governed command root from the live status/supervisor root.

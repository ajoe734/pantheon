#!/usr/bin/env python3
"""Dispatch assistant control-mode and runtime-repair execution tasks.

Source packet:
  docs/04/pantheon_assistant_kernel_user_2026-05-31/CONTROL_MODE_REPAIR_EXECUTION_TASKS_2026-06-06.md

This dispatcher reuses the existing supervisor/autoworker queue machinery. It
materializes task rows through scripts/ai_status.py and intentionally does not
grant shell or VM access by itself.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "ai-status.json"

PACKET = "docs/04/pantheon_assistant_kernel_user_2026-05-31/CONTROL_MODE_REPAIR_EXECUTION_TASKS_2026-06-06.md"
USER_MODE = "docs/04/pantheon_assistant_kernel_user_2026-05-31/USER_MODE_CONTRACTION.md"
REPAIR_WORKFLOW = "docs/04/pantheon_assistant_kernel_user_2026-05-31/ASST_KERNEL_007_REPAIR_WORKFLOW.md"
ASSISTANT_CONTROL = "services/control-plane/bff/assistant/control_mode.py"
ASSISTANT_MODE = "services/control-plane/bff/assistant/mode_policy.py"
ASSISTANT_ROUTES = "services/control-plane/bff/assistant/routes.py"
BFF_MAIN = "services/control-plane/bff/main.py"
ACTION_CATALOG = "services/control-plane/bff/action_catalog.py"
COMMAND_EXECUTOR = "services/control-plane/bff/command_executor.py"
RUNTIME_MANAGER = "services/runtime-manager"
ADMIN_CLI = "scripts/pantheon-admin"

SPRINT_ID = "2026-06-06-assistant-control-mode-runtime-repair"
SPRINT_OBJECTIVE = (
    "Make Pantheon Management AI control mode operational for authorized operators while keeping user mode "
    "safe by default, then add governed runtime repair actions for stale paper runtime, monitoring sessions, "
    "telemetry bridge, and telemetry ingest recovery. The passphrase remains an activation factor only and "
    "must not bypass RBAC, MFA, explicit capabilities, TTL, audit, redaction, or command policy."
)

PHASE_DEPLOY = "Sprint ASST-CTRL / Control-mode deployability"
PHASE_AUTH = "Sprint ASST-CTRL / Activation authority"
PHASE_UX = "Sprint ASST-CTRL / Management AI control UX"
PHASE_CATALOG = "Sprint ASST-RUNTIME / Runtime repair action catalog"
PHASE_EXEC = "Sprint ASST-RUNTIME / Runtime repair execution"
PHASE_SECURITY = "Sprint ASST-SEC / Security regression"


# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts)
TASKS = [
    (
        "ASST-CTRL-001",
        "Make assistant kernel control-mode deployable by configuration",
        "補齊 operator-bff 的 kernel/control-mode env 與 passphrase store path，讓 dev/staging 能顯式啟用但預設仍安全關閉。",
        "Codex",
        "Claude",
        PHASE_DEPLOY,
        "",
        "operator-bff exposes PANTHEON_ASSISTANT_KERNEL_ENABLED with default false;"
        "operator-bff exposes PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH under durable BFF data storage;"
        "PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS is configurable and documented;"
        "nonprod deploy script can set the env values without plaintext passphrase;"
        "provider enabled does not imply kernel enabled;"
        "tests assert compose env contract and kernel-disabled default",
        f"{PACKET};{USER_MODE};docker-compose.yml;docker-compose.staging-full.yml;scripts/deploy_nonprod_vm.sh;services/control-plane/bff/tests/test_assistant_dev_compose_flags.py;{ASSISTANT_MODE};{ASSISTANT_CONTROL}",
    ),
    (
        "ASST-CTRL-002",
        "Add explicit assistant kernel activation capability plumbing",
        "新增 assistant.kernel.activate 權限與 dev/staging capability claims，讓 operator/admin 可啟動 control mode，同時保留 reviewer 預設拒絕。",
        "Codex2",
        "Claude",
        PHASE_AUTH,
        "ASST-CTRL-001",
        "explicit activation capability such as assistant.kernel.activate is introduced;"
        "stub/dev auth can carry assistant.kernel capability claims;"
        "production JWT auth still uses real capability claims;"
        "reviewer without activation capability is rejected even with passphrase and MFA;"
        "authorized operator/admin with MFA capability passphrase and kernel flag activates kernel_debug;"
        "errors distinguish missing role MFA capability kernel flag passphrase config bad passphrase and TTL/idle expiry",
        f"{PACKET};{USER_MODE};{ASSISTANT_CONTROL};{ASSISTANT_ROUTES};{BFF_MAIN};services/control-plane/bff/tests/test_assistant_sessions.py;services/control-plane/bff/tests/test_assistant_security.py;services/control-plane/bff/tests/test_management_nl_assistant_provider.py",
    ),
    (
        "ASST-CTRL-003",
        "Improve Management AI control-mode status and frontend-visible posture",
        "讓 Management AI 與管理前端能清楚顯示 user/control/kernel 狀態、缺少哪個啟動條件，並確保暗語永不外洩。",
        "Claude",
        "Codex2",
        PHASE_UX,
        "ASST-CTRL-001,ASST-CTRL-002",
        "/bff/assistant/mode and /bff/assistant/control-mode expose machine-readable inactive reasons;"
        "Management NL /control status /control off explicit passphrase and direct passphrase commands return redacted questions;"
        "UI does not imply passphrase alone grants authority;"
        "failed explicit passphrase attempts are redacted before provider conversation and audit persistence;"
        "context packs switch to kernel_debug only while a valid same-session activation is active",
        f"{PACKET};{USER_MODE};{ASSISTANT_ROUTES};{BFF_MAIN};apps/management/src;services/control-plane/bff/tests/test_management_nl_assistant_provider.py",
    ),
    (
        "ASST-RUNTIME-001",
        "Define governed runtime recovery actions for paper runtime and telemetry",
        "把 stale paper runtime、monitoring session、telemetry bridge/ingest recovery 變成 BFF/action catalog 可治理的 action，而不是靠口頭建議。",
        "Gemini",
        "Codex",
        PHASE_CATALOG,
        "ASST-CTRL-002",
        "actions RestartPaperRuntime RestartTelemetryBridge TerminateStalePaperMonitoringSession StartPaperMonitoringSession and ProbeTelemetryIngest are specified;"
        "each action declares required role/capability confirmation idempotency audit and BFF-down fallback;"
        "each action states read-only restart-only or session-mutating scope;"
        "no action grants live broker or capital authority;"
        "contract states heartbeat freshness is the first recovery success condition even when totalTrades remains zero",
        f"{PACKET};OPERATOR_ACCEPTANCE_MATRIX.md;services/control-plane/bff/BFF_API_CONTRACT.md;{ACTION_CATALOG};services/control-plane/bff/models.py;docs/deployment/runtime-repair-control-mode-2026-06-06.md",
    ),
    (
        "ASST-RUNTIME-002",
        "Wire runtime recovery actions to audited runtime-manager or admin CLI execution",
        "把核准的 runtime recovery action 接到 runtime-manager protected API 或 admin CLI，補 audit receipt、idempotency 與 stale-session guard。",
        "Codex",
        "Claude",
        PHASE_EXEC,
        "ASST-RUNTIME-001",
        "runtime repair actions dispatch through runtime-manager protected API or admin CLI not raw BFF shell;"
        "each command writes audit receipt with actor action target idempotency key stage and trace id;"
        "stale monitoring sessions terminate only when heartbeat/session evidence proves staleness;"
        "restart/probe flows update or verify telemetry projection freshness;"
        "tests cover success replay stale guard unauthorized missing confirmation and dependency degraded fallback;"
        "smoke proves heartbeat freshness recovery to staleness.age_seconds below 90",
        f"{PACKET};{COMMAND_EXECUTOR};services/control-plane/bff/test_command_executor.py;services/control-plane/bff/test_bff_write_gap_2026_05_28.py;{ADMIN_CLI};{RUNTIME_MANAGER}",
    ),
    (
        "ASST-SEC-002",
        "Add focused security regression for control mode and runtime repair",
        "補 user-mode、control-mode、暗語 redaction、command broker denylist、runtime repair audit 的安全回歸。",
        "Claude2",
        "Codex",
        PHASE_SECURITY,
        "ASST-CTRL-002,ASST-CTRL-003,ASST-RUNTIME-002",
        "user mode cannot access shell repo write raw logs docker secret store provider sessions or command broker;"
        "control mode fails closed for disabled kernel env missing/bad passphrase no MFA no activation capability and invalid TTL;"
        "explicit and direct passphrase attempts never persist raw passphrase text;"
        "command broker denies env token cookie key paths docker socket root shell destructive git and direct production DB writes;"
        "runtime repair commands require high-risk confirmation and audit receipts;"
        "prompt injection cannot expand the tool allowlist",
        f"{PACKET};{USER_MODE};{REPAIR_WORKFLOW};services/control-plane/bff/tests/test_assistant_security.py;services/control-plane/bff/assistant/tests/test_user_mode_regression.py;services/openclaw-gateway-adapter/tests;services/control-plane/bff/tests/test_management_nl_assistant_provider.py",
    ),
]


def update_sprint_metadata() -> None:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    state["sprint"] = SPRINT_ID
    state["sprint_started_at"] = "2026-06-06T00:00:00Z"
    state["objective"] = SPRINT_OBJECTIVE
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Sprint metadata updated: {SPRINT_ID}")


def dispatch_one(
    task_id: str,
    title: str,
    summary_zh: str,
    owner: str,
    reviewer: str,
    phase: str,
    depends_on: str,
    acceptance: str,
    artifacts: str,
) -> None:
    env = os.environ.copy()
    env["AI_NAME"] = "Operator"
    env["TASK_SUMMARY_ZH"] = summary_zh
    env["TASK_PHASE"] = phase
    env["TASK_DEPENDS_ON"] = depends_on
    env["TASK_ACCEPTANCE"] = acceptance
    env["TASK_ARTIFACTS"] = artifacts
    env["TASK_CLASS"] = "assistant_control_runtime_repair"
    env["TASK_AUTO_CREATED_BY"] = "dispatch_assistant_control_mode_repair_2026-06-06"
    env["TASK_AUTO_GENERATED"] = "true"
    cmd = [sys.executable, "scripts/ai_status.py", "assign", task_id, owner, reviewer, title]
    result = subprocess.run(cmd, env=env, cwd=str(REPO_ROOT), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"FAIL {task_id}: {result.stderr.strip() or result.stdout.strip()}", file=sys.stderr)
        sys.exit(result.returncode)
    print(f"OK   {task_id}  owner={owner}  reviewer={reviewer}  phase={phase}")


def main() -> int:
    expected_ids = [task[0] for task in TASKS]
    if len(expected_ids) != len(set(expected_ids)):
        print("Duplicate task IDs in dispatch list", file=sys.stderr)
        return 1
    update_sprint_metadata()
    print(f"Dispatching {len(TASKS)} assistant control/runtime repair tasks ...")
    for task in TASKS:
        dispatch_one(*task)
    print(
        f"\nDispatched {len(TASKS)} tasks for {SPRINT_ID}. "
        "The existing ai_status sync path refreshed dashboard and task-brief outputs."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

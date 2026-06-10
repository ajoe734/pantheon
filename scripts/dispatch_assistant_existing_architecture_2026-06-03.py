#!/usr/bin/env python3
"""Dispatch Pantheon assistant existing-architecture integration tasks.

Source plan:
  docs/04/pantheon_assistant_kernel_user_2026-05-31/EXISTING_ARCHITECTURE_INTEGRATION_PLAN_2026-06-03.md

This dispatcher intentionally reuses the repository's existing supervisor and
auto-worker queue machinery. It does not introduce a new assistant gateway and
does not mutate execution-plans FE code.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "ai-status.json"

PLAN = "docs/04/pantheon_assistant_kernel_user_2026-05-31/EXISTING_ARCHITECTURE_INTEGRATION_PLAN_2026-06-03.md"
TASK_DOC = "docs/04/pantheon_assistant_kernel_user_2026-05-31/EXISTING_ARCHITECTURE_EXECUTION_TASKS_2026-06-03.md"
ASSISTANT_ROUTES = "services/control-plane/bff/assistant/routes.py"
ASSISTANT_STORE = "services/control-plane/bff/assistant/transcript_store.py"
ASSISTANT_CONTEXT = "services/control-plane/bff/assistant/context_composer.py"
MGMT_STORE = "services/control-plane/bff/management_ai_store.py"
MGMT_TESTS = "services/control-plane/bff/tests/test_bff_b6_management_nl_ask.py"
OPENCLAW_CLIENT = "services/control-plane/bff/openclaw_ops_client.py"
OPENCLAW_ADAPTER = "services/openclaw-gateway-adapter/main.py"
ACTION_CATALOG = "services/control-plane/bff/action_catalog.py"
COMMAND_EXECUTOR = "services/control-plane/bff/command_executor.py"
AI_STATUS = "scripts/ai_status.py"
SUPERVISOR = ".orchestrator/supervisor.py"
WORKER_RUNNER = ".orchestrator/worker_runner.py"
PERMISSION_BROKER = ".orchestrator/permission_broker.py"
TASK_BRIEFS = ".orchestrator/task-briefs"

SPRINT_ID = "2026-06-03-pantheon-assistant-existing-architecture"
SPRINT_OBJECTIVE = (
    "Integrate Pantheon Management AI with existing BFF assistant surfaces plus OpenClaw adapter plus "
    "supervisor/autoworker orchestration. This wave explicitly reuses /bff/management/nl/ask durable "
    "conversation persistence plus /bff/assistant session/context/mode routes plus OpenClaw adapter "
    "provider/tool policy plus scripts/ai_status.py task dispatch. It must not create a second assistant "
    "gateway. It must not expose provider credentials to FE. It must not let Web API shell into the VM. "
    "Deliverables cover durable conversation truth alignment context mesh real provider routing governed "
    "operation tools SA/SD generator signed dev collaboration bridge orchestrator status readback FE follow-up "
    "brief and security/mode regression."
)

PHASE_TRUTH = "Sprint ASST-INTEG / Durable conversation truth"
PHASE_CONTEXT = "Sprint ASST-INTEG / Context mesh"
PHASE_PROVIDER = "Sprint ASST-INTEG / Provider routing"
PHASE_TOOLS = "Sprint ASST-INTEG / Governed operation tools"
PHASE_DOCS = "Sprint ASST-INTEG / SA-SD generator"
PHASE_BRIDGE = "Sprint ASST-INTEG / Dev collaboration bridge"
PHASE_STATUS = "Sprint ASST-INTEG / Orchestrator status readback"
PHASE_FE = "Sprint ASST-INTEG / FE follow-up brief"
PHASE_SECURITY = "Sprint ASST-INTEG / Security and mode regression"


# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts)
TASKS = [
    (
        "ASST-INTEG-001",
        "Unify Management AI durable conversation truth with assistant transcripts",
        "把 Management AI 的 durable conversation store 與 /bff/assistant transcript/session surface 對齊，避免 dev/prod 仍有另一份 in-memory 對話真相。",
        "Codex",
        "Claude",
        PHASE_TRUTH,
        "",
        "/bff/management/nl/ask and /bff/management/ai/conversations remain canonical ask/readback;"
        "/bff/assistant/sessions/{sessionId}/transcript does not create a separate in-memory truth in dev/prod;"
        "Unknown session ids return 404;"
        "Idempotency-Key replay does not duplicate user or assistant turns;"
        "BFF restart preserves conversation history in dev;"
        "Tests cover management ask plus assistant transcript readback",
        f"{PLAN};{TASK_DOC};{ASSISTANT_ROUTES};{ASSISTANT_STORE};{MGMT_STORE};{MGMT_TESTS};services/control-plane/bff/tests/test_assistant_sessions.py",
    ),
    (
        "ASST-INTEG-002",
        "Extend BFF assistant context mesh with UI hints BFF reads and docs citations",
        "擴充既有 context composer，讓小幫手同時吃 UI hint、RBAC-filtered BFF read surfaces、以及 docs/RAG citation。",
        "Codex2",
        "Claude2",
        PHASE_CONTEXT,
        "ASST-INTEG-001",
        "Context pack has separate UI hint BFF read and docs/RAG sections;"
        "FE context cannot grant access and remains hint-only;"
        "BFF read sources enforce actor RBAC and tenant visibility;"
        "Provider context includes source_refs for docs and API snapshots;"
        "Redaction runs before persistence and provider invocation;"
        "Tests cover denied source plus redacted source refs",
        f"{PLAN};{TASK_DOC};{ASSISTANT_CONTEXT};services/control-plane/bff/assistant/models.py;services/control-plane/bff/tests/test_assistant_context_pack.py",
    ),
    (
        "ASST-INTEG-003",
        "Route real assistant providers through existing OpenClaw adapter contracts",
        "沿用 OpenClaw gateway adapter 的 readiness/provider invoke，不另建 gateway，並讓 dev 對 real provider 與 degraded 狀態誠實呈現。",
        "Codex",
        "Claude",
        PHASE_PROVIDER,
        "ASST-INTEG-001,ASST-INTEG-002",
        "BFF calls existing OpenClaw adapter provider readiness and invoke routes;"
        "Dev uses real provider when configured;"
        "Missing credentials return explicit degraded provider status;"
        "Mock fallback is labelled local_or_ci_fallback only;"
        "FE-visible payloads never include credentials or local session paths;"
        "Smoke script proves readiness plus degraded cases",
        f"{PLAN};{TASK_DOC};{OPENCLAW_CLIENT};{OPENCLAW_ADAPTER};services/openclaw-gateway-adapter/assistant_command_policy.py;scripts/openclaw-assistant-provider-smoke.sh;services/control-plane/bff/tests/test_management_nl_assistant_provider.py",
    ),
    (
        "ASST-INTEG-004",
        "Implement governed assistant operation tool contracts on existing BFF actions",
        "把小幫手的系統操作能力接到既有 action_catalog/command_executor/audit receipt，而不是直接操作 DOM 或 shell。",
        "Claude",
        "Codex2",
        PHASE_TOOLS,
        "ASST-INTEG-002",
        "Tools follow preview validation confirmation execute receipt;"
        "Low-risk actions execute only within RBAC;"
        "Medium and high-risk actions require reason and confirmation;"
        "Every execution writes audit receipt and trace id;"
        "Assistant cannot use hidden DOM submit as authoritative mutation path;"
        "Tests cover allowlist denial and receipt shape",
        f"{PLAN};{TASK_DOC};{ACTION_CATALOG};{COMMAND_EXECUTOR};services/control-plane/bff/assistant;services/control-plane/bff/tests/test_assistant_security.py",
    ),
    (
        "ASST-INTEG-005",
        "Add SA SD requirement capture and execution task generator",
        "讓小幫手能從對話生成 requirement capture、SA、SD、execution task packet，並歸檔到既有 docs 與 task brief 位置。",
        "Claude2",
        "Codex",
        PHASE_DOCS,
        "ASST-INTEG-001,ASST-INTEG-002",
        "Requirement capture is generated from a Management AI conversation;"
        "SA includes current state roles flows data risk and acceptance scenarios;"
        "SD includes architecture API DB/migration UI tool/action tests rollout and rollback;"
        "Generated docs include source citations;"
        "Execution tasks include owner reviewer dependencies artifacts and acceptance;"
        "Generated artifacts land in existing docs and task-brief locations",
        f"{PLAN};{TASK_DOC};docs/02-architecture;docs/03;docs/04;docs/05-ui;{TASK_BRIEFS}",
    ),
    (
        "ASST-INTEG-006",
        "Bridge assistant-generated task packets into supervisor autoworker dispatch",
        "建立 signed task packet 到既有 ai_status/supervisor/autoworker 的橋接，不讓 Web API 直接 shell 到 VM。",
        "Codex2",
        "Claude",
        PHASE_BRIDGE,
        "ASST-INTEG-005",
        "BFF emits or stores signed task packet and never shells directly;"
        "Packet includes actor mode source conversation source turns docs tasks constraints and signature;"
        "Replay protection rejects duplicate signed packets;"
        "Dispatcher materializes tasks through scripts/ai_status.py;"
        "Supervisor and autoworker can pick up generated tasks;"
        "Audit links packet id to conversation and generated docs",
        f"{PLAN};{TASK_DOC};{AI_STATUS};{SUPERVISOR};{WORKER_RUNNER};{PERMISSION_BROKER};{TASK_BRIEFS}",
    ),
    (
        "ASST-INTEG-007",
        "Expose orchestrator worker PR CI and deploy status readback to assistant",
        "讓小幫手可從既有 orchestrator/GitHub 狀態讀回 task、worker、PR、CI、deploy 進度，用於閉環回覆。",
        "Gemini",
        "Codex",
        PHASE_STATUS,
        "ASST-INTEG-006",
        "Assistant can read task owner reviewer blocker next action and task brief path;"
        "Assistant can read worker dispatch state without provider credentials;"
        "Assistant can report PR CI merge and deploy status when available;"
        "Status payloads include source refs and snapshot timestamps;"
        "Tests or probe cover unavailable GitHub status degradation",
        f"{PLAN};{TASK_DOC};{AI_STATUS};.orchestrator/runtime_state.py;{SUPERVISOR};services/control-plane/bff/assistant",
    ),
    (
        "ASST-INTEG-008",
        "Prepare execution-plans FE context registry and stale-session UX follow-up",
        "產出跨 repo FE follow-up brief：assistant-readable form registry、BFF 404 stale session UX、SSE degraded 診斷；本任務不直接修改 FE。",
        "Copilot",
        "Claude2",
        PHASE_FE,
        "ASST-INTEG-001,ASST-INTEG-002",
        "FE brief specifies route form table filter selected-row and validator context;"
        "Brief preserves BFF conversation readback as source of truth;"
        "Brief covers BFF 404 stale local-only session recovery;"
        "Brief covers SSE failure diagnosis by auth network path or server stream;"
        "No execution-plans code is changed by this dispatch task",
        f"{PLAN};{TASK_DOC};docs/05-ui;docs/pantheon-handoffs;{TASK_BRIEFS}",
    ),
    (
        "ASST-INTEG-009",
        "Add security mode and tool-boundary regression suite for assistant integration",
        "補 user-mode contraction、control-mode TTL/passphrase、tool allowlist、redaction、provider credential non-exposure 的安全回歸。",
        "Codex",
        "Claude",
        PHASE_SECURITY,
        "ASST-INTEG-003,ASST-INTEG-004,ASST-INTEG-006",
        "User mode cannot access shell repo write raw logs docker secret store provider session or command broker;"
        "Control/kernel mode requires RBAC MFA capability passphrase TTL and idle timeout;"
        "Passphrase change requires admin plus MFA;"
        "OpenClaw tool policy remains deny-first;"
        "Prompt injection cannot expand tools or expose secrets;"
        "Regression suite runs in local validation and CI",
        f"{PLAN};{TASK_DOC};services/control-plane/bff/tests/test_assistant_security.py;services/control-plane/bff/assistant/tests/test_user_mode_regression.py;services/openclaw-gateway-adapter;docs/04/pantheon_assistant_kernel_user_2026-05-31/ASST_KERNEL_007_REPAIR_WORKFLOW.md",
    ),
]


def update_sprint_metadata() -> None:
    """Update sprint metadata before assigning tasks."""
    state = json.loads(STATE_PATH.read_text())
    state["sprint"] = SPRINT_ID
    state["sprint_started_at"] = "2026-06-03T00:00:00Z"
    state["objective"] = SPRINT_OBJECTIVE
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
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
    env["TASK_CLASS"] = "assistant_integration"
    env["TASK_AUTO_CREATED_BY"] = "dispatch_assistant_existing_architecture_2026-06-03"
    env["TASK_AUTO_GENERATED"] = "true"
    cmd = [sys.executable, "scripts/ai_status.py", "assign", task_id, owner, reviewer, title]
    result = subprocess.run(cmd, env=env, cwd=str(REPO_ROOT), capture_output=True, text=True)
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
    print(f"Dispatching {len(TASKS)} assistant integration tasks ...")
    for task in TASKS:
        dispatch_one(*task)
    print(
        f"\nDispatched {len(TASKS)} tasks for {SPRINT_ID}. "
        "The existing ai_status sync path refreshed dashboard and task-brief outputs."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

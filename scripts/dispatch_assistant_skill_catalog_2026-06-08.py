#!/usr/bin/env python3
"""Dispatch the ASST-SKILL EPIC: catalog-driven assistant capabilities.

Premise (operator decision 2026-06-08): assistant capabilities such as SA/SD
generation, control-mode, resync, and provider re-auth must NOT be hardcoded
frontend buttons or bespoke per-capability routes. A capability is a governed
OpenClaw skill, resolved deny-by-default through the existing tool/workflow
policy, discovered via the effective-tools endpoint, and rendered generically by
the frontend. The backend already has the machinery (first-class governed `skill`
entity lifecycle in BFF_COMMAND_API_CONTRACT.md, tool_workflow_bridge deny-first
policy, /api/openclaw-adapter/tools discovery); this EPIC realigns the surfaces
with it.

This dispatcher reuses the existing supervisor/autoworker dispatch path
(scripts/ai_status.py assign) and the live ASST-INTEG sprint. It creates NO new
gateway, exposes NO provider credentials to FE, and lets NO Web API shell the VM.

Plan shape: (b) SA/SD pilot is the first end-to-end template inside (c) the full
EPIC that rolls the pattern across the remaining toolbar entries plus
assistant.provider.reauth.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Decision-of-record this EPIC executes against.
DECISION_DOC = "docs/decisions/assistant-capability-skill-catalog-ownership.md"

# Reused real surfaces.
OPENCLAW_ADAPTER = "services/openclaw-gateway-adapter/main.py"
TOOL_BRIDGE = "services/openclaw-gateway-adapter/tool_workflow_bridge.py"
OPENCLAW_CLIENT = "services/control-plane/bff/openclaw_ops_client.py"
ACTION_CATALOG = "services/control-plane/bff/action_catalog.py"
COMMAND_CONTRACT = "services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md"
ASSISTANT_BFF = "services/control-plane/bff/assistant"
CODEX_PROVIDER = "services/openclaw-gateway-adapter/assistant_codex_provider.py"
CRED_MOUNTS = "services/openclaw-gateway-adapter/assistant_credential_mounts.py"
FE_TOOLBAR = "execute-plans/src"  # FE generic-renderer target (toolbar/command/card)

PHASE_FOUNDATION = "EPIC ASST-SKILL / Descriptor + catalog foundation"
PHASE_PILOT = "EPIC ASST-SKILL / SA-SD pilot (template)"
PHASE_RENDERER = "EPIC ASST-SKILL / FE generic renderer"
PHASE_MIGRATE = "EPIC ASST-SKILL / Remaining toolbar migration"
PHASE_REAUTH = "EPIC ASST-SKILL / Provider re-auth skill"
PHASE_REGRESSION = "EPIC ASST-SKILL / Policy + audit regression"

# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts)
TASKS = [
    (
        "ASST-SKILL-001",
        "Define assistant-skill descriptor schema and effective-catalog resolver",
        "定義 assistant-skill descriptor（id/title/surface/mode_gate/role/confirm_policy/input_schema/handler_ref/result_surface），"
        "並讓 OpenClaw tool/workflow policy 以 deny-by-default 解析每個 operator/agent/mode 的 effective skills，沿用既有 /api/openclaw-adapter/tools 發現端點，不另建 registry。",
        "Codex",
        "Claude",
        PHASE_FOUNDATION,
        "",
        "Descriptor schema covers id title surface mode_gate role confirm_policy input_schema handler_ref result_surface;"
        "Effective-skill resolution is deny-by-default and reuses the existing tool/workflow policy layer;"
        "GET /api/openclaw-adapter/tools returns effective descriptors per operator/agent/mode;"
        "Unknown or disallowed skills fail closed;"
        "No second registry or gateway is introduced;"
        "Tests cover allow, deny, and per-mode differentiation",
        f"{DECISION_DOC};{TOOL_BRIDGE};{OPENCLAW_ADAPTER};{OPENCLAW_CLIENT};{COMMAND_CONTRACT}",
    ),
    (
        "ASST-SKILL-002",
        "Pilot: migrate SA/SD button to governed skill assistant.sa_sd.generate",
        "把 SA/SD 從寫死的工具列按鈕改成 catalog-driven skill assistant.sa_sd.generate：handler_ref 指向既有 dev-docs/generate handler（不改 handler 邏輯），"
        "經 effective catalog 曝光，FE 從 descriptor 渲染這一顆。這是整個 EPIC 的端到端樣板。",
        "Claude",
        "Codex",
        PHASE_PILOT,
        "ASST-SKILL-001",
        "assistant.sa_sd.generate is registered as a governed skill with mode_gate kernel;"
        "Its handler_ref points at the existing dev-docs/generate handler with no handler logic change;"
        "The skill appears in GET /api/openclaw-adapter/tools effective catalog when permitted;"
        "FE renders the SA/SD affordance from the descriptor, not from a hardcoded button;"
        "Invocation enforces the descriptor gate and writes one audit record;"
        "Tests prove catalog presence, gate enforcement, and parity with the prior route",
        f"{DECISION_DOC};{ASSISTANT_BFF};{OPENCLAW_ADAPTER};{FE_TOOLBAR}",
    ),
    (
        "ASST-SKILL-003",
        "Frontend generic renderer: surfaces driven by the effective skill catalog",
        "把 Management AI 工具列/命令/降級卡 action 改成遍歷 effective catalog 動態渲染（button/command/card_action 由 surface 決定，enable/confirm/輸入表單由 descriptor 決定），"
        "達到 parity 後移除寫死按鈕；FE 不得在原始碼列舉能力。",
        "Claude2",
        "Codex2",
        PHASE_RENDERER,
        "ASST-SKILL-002",
        "Toolbar command-palette and degraded-card actions are rendered by iterating the effective catalog;"
        "Enablement confirm steps and input modals derive from descriptor fields only;"
        "Hardcoded capability buttons are removed once catalog parity is confirmed;"
        "FE source enumerates no capability and calls no uncatalogued route;"
        "FE shows nothing the policy did not advertise for the current context;"
        "Tests or snapshot cover catalog-driven render and removal of hardcoded entries",
        f"{DECISION_DOC};{FE_TOOLBAR}",
    ),
    (
        "ASST-SKILL-004",
        "Migrate remaining toolbar capabilities (control-mode, resync, openclaw) to skills",
        "把剩下的工具列能力（Control / Resync / OpenClaw 等）依樣板包成 catalog skill，handler_ref 指向既有 route/handler，門禁改由 descriptor+policy 統一解析。",
        "Codex2",
        "Claude2",
        PHASE_MIGRATE,
        "ASST-SKILL-003",
        "Control-mode resync and openclaw affordances are registered as governed skills;"
        "Each handler_ref points at an existing handler with no behavior change;"
        "Per-route ad hoc gate checks are replaced by descriptor plus policy resolution;"
        "All migrated capabilities render from the catalog only;"
        "Deny-first policy and audit are preserved for every migrated skill;"
        "Tests cover catalog presence and gate parity for each migrated capability",
        f"{DECISION_DOC};{ASSISTANT_BFF};{OPENCLAW_ADAPTER};{FE_TOOLBAR}",
    ),
    (
        "ASST-SKILL-005",
        "Add provider re-auth as device-flow skill assistant.provider.reauth",
        "新增 assistant.provider.reauth skill（kernel + control-mode gated）：adapter 以 service-user mount 的 CODEX_HOME 跑 codex login --device-auth，"
        "擷取 verification_uri/user_code 回前端、背景輪詢直到 token 寫入掛載目錄、成功後自動 re-probe readiness。憑證只在 operator 瀏覽器與 IdP 間交換，不經 BFF/FE。先做 device-auth headless 擷取 spike。",
        "Claude",
        "Codex",
        PHASE_REAUTH,
        "ASST-SKILL-003",
        "Spike proves codex login --device-auth verification_uri and user_code can be captured headlessly;"
        "assistant.provider.reauth is a governed skill gated by kernel mode and control-mode passphrase;"
        "Surface carries only verification_uri user_code and poll status and never provider credential material;"
        "Token is written by the provider CLI into the service-user mount only;"
        "On success the adapter re-probes readiness and upstream returns to healthy;"
        "Tests cover start poll cancel expiry and credential-non-exposure",
        f"{DECISION_DOC};{CODEX_PROVIDER};{CRED_MOUNTS};{OPENCLAW_ADAPTER};{ASSISTANT_BFF}",
    ),
    (
        "ASST-SKILL-006",
        "Consolidate gating and audit into descriptor policy and add EPIC regression",
        "把散落各 route 的 mode/role/confirm-token 門禁收斂到 descriptor+policy 一次解析，補 EPIC 回歸：deny-first 保持、未授權 skill fail-closed、每次 invoke 一筆 audit、provider 憑證不外洩。",
        "Codex",
        "Claude",
        PHASE_REGRESSION,
        "ASST-SKILL-004,ASST-SKILL-005",
        "Mode role and confirm-token gating is resolved once from descriptor plus policy;"
        "OpenClaw tool policy remains deny-first and unknown skills fail closed;"
        "Every skill invocation writes exactly one audit record with trace id;"
        "No skill path lets provider credentials transit BFF or FE;"
        "Prompt injection cannot expand the effective skill set;"
        "Regression suite runs in local validation and CI",
        f"{DECISION_DOC};{TOOL_BRIDGE};{ASSISTANT_BFF};services/control-plane/bff/tests/test_assistant_security.py",
    ),
]


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
) -> bool:
    env = os.environ.copy()
    env["TASK_SUMMARY_ZH"] = summary_zh
    env["TASK_PHASE"] = phase
    env["TASK_DEPENDS_ON"] = depends_on
    env["TASK_ACCEPTANCE"] = acceptance
    env["TASK_ARTIFACTS"] = artifacts
    env["TASK_AUTO_CREATED_BY"] = "dispatch_assistant_skill_catalog_2026-06-08"
    cmd = [sys.executable, "scripts/ai_status.py", "assign", task_id, owner, reviewer, title]
    result = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAIL {task_id}: {result.stderr.strip() or result.stdout.strip()}", file=sys.stderr)
        return False
    print(f"OK   {task_id}  owner={owner}  reviewer={reviewer}  phase={phase}")
    return True


def main() -> int:
    ids = [t[0] for t in TASKS]
    if len(ids) != len(set(ids)):
        print("Duplicate task IDs in dispatch list", file=sys.stderr)
        return 2
    ok = True
    for task in TASKS:
        ok = dispatch_one(*task) and ok
    print("Done. EPIC ASST-SKILL dispatched into the live ASST-INTEG sprint." if ok else "Completed with failures.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

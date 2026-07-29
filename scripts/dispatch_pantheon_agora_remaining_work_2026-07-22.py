#!/usr/bin/env python3
"""Register the 2026-07-22 Pantheon/Agora remaining-work packet.

This dispatcher is a governed-command client. It never writes canonical state
files directly. Run --dry-run before the packet is merged; run the mutation
path only from an explicit Human/Ops context after merge to dev.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = REPO_ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from common import validate_status_command_runtime


PACKET_DIR = "docs/bff/execution-tasks/2026-07-22-pantheon-agora-remaining-work"
SOURCE_DOC = "docs/04/pantheon_agora_remaining_work_2026-07-22/REMAINING_WORK_GAP.md"
DISPATCHER = "dispatch_pantheon_agora_remaining_work_2026-07-22"
STATUS_ROOT_ENV = "PANTHEON_STATUS_ROOT"
COMMAND_ROOT_ENV = "PANTHEON_COMMAND_ROOT"
COMMAND_SHA_ENV = "PANTHEON_COMMAND_RUNTIME_SHA"
COMMAND_REMOTE_ENV = "PANTHEON_COMMAND_REMOTE"
COMMAND_BASE_REF_ENV = "PANTHEON_COMMAND_BASE_REF"
TASK_STATE_STORE_MODE_ENV = "PANTHEON_TASK_STATE_STORE_MODE"
TASK_STATE_EVENT_LOG_ENV = "PANTHEON_TASK_STATE_EVENT_LOG"
TERMINAL_TASK_STATUSES = {"done", "superseded"}


def _brief(name: str) -> str:
    return f"{PACKET_DIR}/{name}.md"


def _new_task(
    task_id: str,
    title: str,
    summary: str,
    owner: str,
    reviewer: str,
    priority: str,
    repository_id: str,
    phase: str,
    depends_on: list[str],
    scope: list[str],
    lane: str,
    next_step: str,
) -> dict[str, Any]:
    brief = _brief(task_id)
    slug = "ajoe734/pantheon" if repository_id == "pantheon" else "ajoe734/execute-plans"
    registry_id = "pantheon" if repository_id == "pantheon" else "execute_plans"
    artifacts = [SOURCE_DOC, f"{PACKET_DIR}/INDEX.md", brief]
    if registry_id == "execute_plans":
        artifacts.extend(f"execute-plans/{path}" for path in scope)
    return {
        "id": task_id,
        "title": title,
        "summary_zh": summary,
        "owner": owner,
        "reviewer": reviewer,
        "phase": phase,
        "depends_on": depends_on,
        "artifacts": artifacts,
        "acceptance": [
            f"all acceptance criteria in {brief}",
            "task branch is merged to dev with reviewer approval and required delivery metadata",
        ],
        "next": next_step,
        "priority": priority,
        "task_class": "execution",
        "auto_created_by": DISPATCHER,
        "auto_generated": True,
        "delivery_layer": "primary",
        "mutates_canonical": True,
        "repository_id": registry_id,
        "target_repo": slug,
        "merge_target": "dev",
        "declared_scope": scope,
        "fleet_lane": lane,
        "source_ref": {
            "doc": SOURCE_DOC,
            "packet": f"{PACKET_DIR}/INDEX.md",
            "brief": brief,
            "merge_target": "dev",
            "target_repo": slug,
        },
    }


TASKS: list[dict[str, Any]] = [
    _new_task(
        "OPS-DISPATCH-LEASE-SYNC-001",
        "Restore governed dispatch status sync",
        "收斂 #3936/#3948，讓 supervisor 把已啟動 worker run id 以 ORCH_RUN_ID 傳給 governed status command，並以完整 lifecycle smoke 證明不再因缺 lease 反覆退出。",
        "Codex", "Claude", "P0", "pantheon", "Pantheon/Agora Recovery / Wave 0 control",
        [], [".orchestrator/supervisor.py", ".orchestrator/test_supervisor.py"],
        "ops-dispatch-lease", "Converge the duplicate lease PRs and deploy one governed repair.",
    ),
    _new_task(
        "PAN-LIFECYCLE-RECOVERY-001",
        "Recover lifecycle projection and freshness health",
        "修復 ENOSPC 後仍停更的 lifecycle projector，加入安全 generation retention、可恢復 publish 與 readiness/freshness truth，並把 live rescue 正式經 PR 部署。",
        "Codex2", "Antigravity", "P0", "pantheon", "Pantheon/Agora Recovery / Wave 0 lifecycle",
        ["OPS-DISPATCH-LEASE-SYNC-001"], ["services/trade_journey/lifecycle_projector.py", "services/control-plane/bff", "docker-compose.yml", "docs/deployment/evidence"],
        "runtime-lifecycle-recovery", "Recover the projector, then deliver retention and readiness through dev.",
    ),
    _new_task(
        "AG-PERF-TRUTH-001-BE",
        "Governed Agora performance projection and actions",
        "新增真實績效/介入/執行歷史/調整建議 projection 與 governed apply/reject/return receipt；缺資料回 unavailable，不得造數或造結論。",
        "Codex", "Claude", "P0", "pantheon", "Agora Truth / Wave 0 performance backend",
        ["OPS-DISPATCH-LEASE-SYNC-001"], ["services/control-plane/bff", "services/control-plane/specs/agora", "services/control-plane/openapi"],
        "agora-performance-backend", "Implement authoritative performance reads and durable suggestion receipts.",
    ),
    _new_task(
        "AG-CAND-TRUTH-001-BE",
        "Complete Agora candidate provenance projection",
        "讓 candidate DTO 的理由、疑慮、事件、證據與細節都屬於同一真實 candidate 並帶 provenance/as-of；缺欄位明確 unavailable。",
        "Claude", "Codex2", "P0", "pantheon", "Agora Truth / Wave 0 candidate backend",
        ["OPS-DISPATCH-LEASE-SYNC-001"], ["services/control-plane/bff", "services/research", "services/control-plane/specs/agora"],
        "agora-candidate-backend", "Deliver candidate-field provenance and honest missing-field semantics.",
    ),
    _new_task(
        "AG-WS-OPS-001",
        "Durable Workshop versions and selection",
        "實作 workshop versions list/create/select 三條 deferred API，含 durable StrategySpec version、lineage、idempotency、ETag CAS、tenant isolation 與 restart persistence。",
        "Claude", "Antigravity", "P1", "pantheon", "Agora Completion / Wave 0 workshop versions",
        ["OPS-DISPATCH-LEASE-SYNC-001"], ["services/control-plane/bff/agora/strategy_workshop", "services/control-plane/specs/agora"],
        "agora-workshop-versions", "Implement the three version/selection operations with durable CAS semantics.",
    ),
    _new_task(
        "PAN-SOURCE-FRESH-001",
        "Formalize guarded source refresh and Agora freshness",
        "把 deny-all egress 緊急修補正式交付，建立 HTTPS allowlist/SSRF guard、bounded scheduler、ingest receipt 與 Agora freshness/stale truth。",
        "Antigravity", "Codex2", "P1", "pantheon", "Pantheon Data / Wave 0 source freshness",
        ["OPS-DISPATCH-LEASE-SYNC-001"], ["services/external_egress.py", "services/source_ingestion", "services/research/adapters", "docker-compose.yml", "scripts/deploy_nonprod_vm.sh"],
        "source-refresh-governance", "Reconcile the live rescue and deliver deny-by-default bounded refresh.",
    ),
    _new_task(
        "OPS-PROMOTE-CONFLICT-RECOVERY-001",
        "Recover publish-to-master promote train",
        "修復 publish-promote 在第一個 historical conflict 即中止的行為，逐筆分類候選並保留 protected checks、tag immutability 與 rollback safety。",
        "Codex2", "Claude", "P1", "pantheon", "Delivery Control / Wave 0 promote recovery",
        ["OPS-DISPATCH-LEASE-SYNC-001"], ["scripts/git/publish_promote.py", ".github/workflows/publish-promote.yml"],
        "ops-promote-recovery", "Reproduce v2026.07.15.0 conflict and make candidate handling deterministic.",
    ),
    _new_task(
        "OPS-TASK-PR-TRIAGE-001",
        "Evidence-based overdue PR and branch triage",
        "把 29 個 overdue task PR 與 no-open-PR branches 依 dev reachability、PR history、archive evidence 分類；只關閉明確 superseded PR，僅產生 branch deletion dry run。",
        "Antigravity", "Codex", "P2", "pantheon", "Delivery Control / Wave 0 repository triage",
        ["OPS-DISPATCH-LEASE-SYNC-001"], ["scripts/git", "docs/operations", "docs/deployment/evidence"],
        "ops-repository-triage", "Build the read-only inventory and disposition every overdue PR.",
    ),
    _new_task(
        "OPS-SECURITY-DEPENDENCY-001",
        "Reconcile and remediate current dependency alerts",
        "重新綁定 20 個 Dependabot alerts 到 current dev reachable graph；修復或 fail-closed 隔離 MLflow/Ray/Torch critical/high，並以 commit/path evidence 清掉已刪除 FE manifest 的歷史 alert。",
        "Codex", "Claude", "P1", "pantheon", "Security / Wave 1 dependency remediation",
        ["OPS-DISPATCH-LEASE-SYNC-001"], ["services/research/mlflow/requirements.txt", "services/research/rllib/requirements.txt", "services/research/finrl/requirements.txt", "docker-compose.yml", ".github/workflows"],
        "security-dependency-remediation", "Re-query all alerts and remove or isolate every reachable critical/high dependency risk.",
    ),
]


PPL_ADDENDUM = "docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-009-2026-07-22-acceptance-addendum.md"
TJ_ADDENDUM = "docs/bff/execution-tasks/2026-07-11-trade-journey-e2e/TJ-E2E-012-2026-07-22-acceptance-addendum.md"

TASKS.extend(
    [
        {
            "id": "PPL-ALLOC-009",
            "title": "Closeout and dev publish",
            "summary_zh": "credentials/strict deploy 已清除；完成同一 governed identity 的 B1 allocation chain、authenticated desktop/mobile B3 與 B5 IA reviewer decision。",
            "owner": "Codex", "reviewer": "Codex2",
            "phase": "Persona Promotion Allocation / Wave 3 closeout",
            "depends_on": ["PPL-ALLOC-002", "PPL-ALLOC-003", "PPL-ALLOC-004", "PPL-ALLOC-005", "PPL-ALLOC-006", "PPL-ALLOC-007", "PPL-ALLOC-008", "OPS-DISPATCH-LEASE-SYNC-001", "PAN-LIFECYCLE-RECOVERY-001"],
            "artifacts": ["docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/archive", "services/control-plane/bff", "execute-plans:src", PPL_ADDENDUM],
            "acceptance": [f"all remaining B1/B3/B5 acceptance criteria in {PPL_ADDENDUM}"],
            "next": "Credential gate is cleared. After lease and lifecycle recovery, execute one correlated B1 chain, same-chain B3 desktop/mobile proof, then B5 review.",
            "priority": "P0",
            "source_ref": {
                "doc": "docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md",
                "packet": "docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/INDEX.md",
                "acceptance_addendum": PPL_ADDENDUM,
                "merge_target": "dev",
                "target_repo": "pantheon+execute-plans",
            },
            "existing": True,
        },
        {
            "id": "TJ-E2E-012",
            "title": "Hosted acceptance and closeout",
            "summary_zh": "保留 7/21 hosted proof，補齊 12 scenario immutable ledger、security/performance/SSE/rebuild mapping 與獨立 Human/Ops verdict。",
            "owner": "Codex2", "reviewer": "Claude",
            "phase": "Trade Journey E2E / Wave 5",
            "depends_on": ["TJ-E2E-001", "TJ-E2E-002", "TJ-E2E-003", "TJ-E2E-004", "TJ-E2E-005", "TJ-E2E-006", "TJ-E2E-007", "TJ-E2E-008", "TJ-E2E-009", "TJ-E2E-010", "TJ-E2E-011", "OPS-DISPATCH-LEASE-SYNC-001", "PAN-LIFECYCLE-RECOVERY-001"],
            "artifacts": ["docs/04/pantheon_trade_journey_e2e_observability_gap_2026-07-11/TRADE_JOURNEY_E2E_OBSERVABILITY_GAP.md", "docs/bff/execution-tasks/2026-07-11-trade-journey-e2e/TJ-E2E-012-hosted-acceptance-closeout.md", TJ_ADDENDUM],
            "acceptance": [f"all remaining scenario-ledger and independent-verdict criteria in {TJ_ADDENDUM}"],
            "next": "After lease and lifecycle recovery, preserve run 29856622315 and add per-scenario immutable evidence plus independent Human/Ops verdict.",
            "priority": "P0",
            "source_ref": {
                "doc": "docs/04/pantheon_trade_journey_e2e_observability_gap_2026-07-11/TRADE_JOURNEY_E2E_OBSERVABILITY_GAP.md",
                "packet": "docs/bff/execution-tasks/2026-07-11-trade-journey-e2e/TJ-E2E-012-hosted-acceptance-closeout.md",
                "acceptance_addendum": TJ_ADDENDUM,
                "merge_target": "dev",
                "target_repo": "pantheon+execute-plans",
            },
            "existing": True,
        },
        _new_task(
            "AG-PERF-TRUTH-001-FE", "Remove simulated Strategy Performance product data",
            "移除 getSimulatedDetails 與 local-only success；依 BFF availability/provenance 顯示真實/unknown 狀態，suggestion action 只在 receipt readback 後成功。",
            "Antigravity", "Codex", "P0", "execute-plans", "Agora Truth / Wave 1 performance frontend",
            ["AG-PERF-TRUTH-001-BE"], ["src/agora/pages/strategy-performance", "src/bff"],
            "agora-performance-frontend", "Consume the governed performance contract and remove all product-path simulation.",
        ),
        _new_task(
            "AG-CAND-TRUTH-001-FE", "Stop mixing live candidates with sample fields",
            "移除 live candidate + DEFAULT_CANDIDATES 混合；每欄只顯示同一 identity 的真實值、明確 unknown/stale，或整張清楚標示 sample。",
            "Codex", "Claude", "P0", "execute-plans", "Agora Truth / Wave 1 candidate frontend",
            ["AG-CAND-TRUTH-001-BE"], ["src/agora/pages/trading-room", "src/agora/trading-room", "src/bff"],
            "agora-candidate-frontend", "Wire candidate provenance and remove static live-field fallback.",
        ),
        _new_task(
            "AG-WS-OPS-002", "Governed Workshop research consultation and conclusion",
            "實作 research-runs、consultations、conclude 三條 deferred API，綁定 durable workshop version、真實 downstream lineage、idempotency 與 atomic terminal transition。",
            "Claude", "Antigravity", "P1", "pantheon", "Agora Completion / Wave 1 workshop operations",
            ["AG-WS-OPS-001"], ["services/control-plane/bff/agora/strategy_workshop", "services/research", "services/consultation", "services/control-plane/specs/agora"],
            "agora-workshop-operations", "Implement the remaining three deferred operations on the version contract.",
        ),
        _new_task(
            "AG-COMPAT-001-BE", "Regenerate complete Agora backend contract bundle",
            "彙整 performance/candidate/workshop 新契約，重產 additive bundle、OpenAPI 與 deterministic hashes，提供 FE generator input；在 FE evidence 前維持 pending。",
            "Codex", "Claude", "P1", "pantheon", "Agora Compatibility / Wave 2 backend bundle",
            ["AG-PERF-TRUTH-001-BE", "AG-CAND-TRUTH-001-BE", "AG-WS-OPS-002"], ["services/control-plane/specs/agora", "services/control-plane/openapi", "docs/contracts/agora"],
            "agora-compat-backend", "Regenerate the additive contract bundle and deterministic frontend input.",
        ),
        _new_task(
            "AG-COMPAT-001-FE", "Generate Agora frontend types and bind runtime identity",
            "由 exact backend contract 生成 FE types/client，CI 驗證無 drift，輸出 non-zero runtime/contract/type hashes 給 final manifest gate。",
            "Antigravity", "Codex2", "P1", "execute-plans", "Agora Compatibility / Wave 3 frontend types",
            ["AG-PERF-TRUTH-001-FE", "AG-CAND-TRUTH-001-FE", "AG-COMPAT-001-BE"], ["src", "scripts", ".github/workflows"],
            "agora-compat-frontend", "Generate and consume exact Agora types, then emit the compatibility handoff.",
        ),
        _new_task(
            "AG-COMPAT-002-GATE", "Finalize Agora cross-repository compatibility gate",
            "把 pending/zero placeholder manifest 換成 exact FE/BFF pair，部署前驗證 commits/hashes/dev reachability，失配 gate-before-switch 並測 rollback。",
            "Claude", "Codex", "P1", "pantheon", "Agora Compatibility / Wave 4 acceptance gate",
            ["AG-COMPAT-001-FE"], ["docs/contracts/agora/dev-compatibility-manifest.json", "scripts/agora_compat_manifest.py", ".github/workflows"],
            "agora-compat-gate", "Consume both handoffs and enforce the exact compatible pair before switch.",
        ),
        _new_task(
            "AG-HOSTED-CLOSE-001", "Replacement-VM Agora hosted acceptance and closeout",
            "在 replacement VM 重跑 durable stores restart proof、六 API、performance receipt、candidate truth、source freshness、desktop/mobile/a11y/RBAC，釘 exact accepted pair。",
            "Antigravity", "Claude", "P1", "pantheon", "Agora Completion / Wave 5 hosted closeout",
            ["AG-COMPAT-002-GATE", "PAN-SOURCE-FRESH-001"], ["docs/deployment/evidence/agora", "docs/04/pantheon_agora_remaining_work_2026-07-22"],
            "agora-hosted-closeout", "Run replacement-VM restart, strict hosted, and safe-restore acceptance.",
        ),
    ]
)


def _status_root() -> Path:
    return Path(os.environ.get(STATUS_ROOT_ENV, str(REPO_ROOT))).expanduser().resolve()


def _load_state(root: Path) -> dict[str, Any]:
    return json.loads((root / "ai-status.json").read_text(encoding="utf-8"))


def _archived_ids(root: Path) -> set[str]:
    task_dir = root / "ai-task-archive" / "tasks"
    return {path.stem for path in task_dir.glob("*.json")} if task_dir.exists() else set()


def validate_specs(
    root: Path | None = None,
    *,
    refuse_archived_reuse: bool = False,
) -> list[str]:
    errors: list[str] = []
    root = root or REPO_ROOT
    ids = [str(task["id"]) for task in TASKS]
    if len(ids) != len(set(ids)):
        errors.append("duplicate task IDs")
    id_set = set(ids)
    active = {str(task.get("id")) for task in _load_state(root).get("tasks", [])}
    archived = _archived_ids(root)
    if refuse_archived_reuse:
        for task_id in sorted(set(ids) & archived):
            errors.append(f"{task_id}: archived task ID cannot be reused by bulk dispatch")
    known = id_set | active | archived
    for task in TASKS:
        task_id = str(task["id"])
        if task["owner"] == task["reviewer"]:
            errors.append(f"{task_id}: owner equals reviewer")
        for dep in task.get("depends_on", []):
            if dep not in known:
                errors.append(f"{task_id}: unknown dependency {dep}")
        if not task.get("existing"):
            brief = REPO_ROOT / str(task["source_ref"]["brief"])
            if not brief.exists():
                errors.append(f"{task_id}: missing brief {brief.relative_to(REPO_ROOT)}")
            if task.get("target_repo") == "pantheon+execute-plans":
                errors.append(f"{task_id}: new cross-repository task must be split")
    if "EP5" in " ".join(ids):
        errors.append("EP5 live/capital work must not be auto-dispatched")
    return errors


def _metadata(task: dict[str, Any]) -> dict[str, Any]:
    excluded = {"id", "title", "summary_zh", "owner", "reviewer", "phase", "existing"}
    return {key: value for key, value in task.items() if key not in excluded}


def _required_absolute_path(name: str) -> Path:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        raise RuntimeError(f"{name} is required for mutation dispatch")
    path = Path(os.path.expanduser(raw))
    if not path.is_absolute():
        raise RuntimeError(f"{name} must be an absolute path")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise RuntimeError(f"{name} cannot include a symlink component: {current}")
    return path.resolve()


def _dirty_command_runtime_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"failed to inspect governed command runtime: {detail}")
    dirty: list[str] = []
    for line in result.stdout.splitlines():
        path = line[3:].strip().strip("\"'")
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1].strip().strip("\"'")
        if path.endswith((".py", ".sh", ".pyc", ".so", ".pl", ".rb")):
            dirty.append(path)
    return dirty


def _governed_status_context() -> tuple[Path, Path, dict[str, str]]:
    if str(os.environ.get(TASK_STATE_STORE_MODE_ENV) or "").strip().lower() != "authoritative":
        raise RuntimeError(
            f"{TASK_STATE_STORE_MODE_ENV}=authoritative is required for mutation dispatch"
        )
    _required_absolute_path(TASK_STATE_EVENT_LOG_ENV)
    status_root = _required_absolute_path(STATUS_ROOT_ENV)
    command_root = _required_absolute_path(COMMAND_ROOT_ENV)
    expected_sha = str(os.environ.get(COMMAND_SHA_ENV) or "").strip()
    if not expected_sha:
        raise RuntimeError(f"{COMMAND_SHA_ENV} is required for mutation dispatch")
    expected_remote = str(os.environ.get(COMMAND_REMOTE_ENV) or "ajoe734/pantheon").strip()
    base_ref = str(os.environ.get(COMMAND_BASE_REF_ENV) or "origin/dev").strip() or "origin/dev"
    runtime = validate_status_command_runtime(
        command_root,
        expected_sha=expected_sha,
        expected_remote=expected_remote,
        base_ref=base_ref,
        require_merged=True,
    )
    dirty_runtime_files = _dirty_command_runtime_files(Path(runtime["root"]))
    if dirty_runtime_files:
        raise RuntimeError(
            "governed command runtime contains dirty executable/import files: "
            + ", ".join(dirty_runtime_files)
        )
    script = Path(runtime["root"]) / "scripts" / "ai_status.py"
    if script.is_symlink() or not script.is_file():
        raise RuntimeError(f"governed status command is not a regular file: {script}")
    env = os.environ.copy()
    env["AI_NAME"] = "Human/Ops"
    env[STATUS_ROOT_ENV] = str(status_root)
    return script, status_root, env


def _run_governed_status_command(command: str, *args: str, env: dict[str, str] | None = None) -> None:
    script, status_root, base_env = _governed_status_context()
    command_env = dict(base_env)
    if env:
        command_env.update(env)
    result = subprocess.run(
        [sys.executable, str(script), command, *args],
        cwd=status_root,
        env=command_env,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{command} failed: {detail}")


def _recover_authoritative_projection() -> None:
    _run_governed_status_command("recover")


def _assert_task_not_archived(root: Path, task_id: str) -> None:
    archive_path = root / "ai-task-archive" / "tasks" / f"{task_id}.json"
    if archive_path.is_symlink():
        raise RuntimeError(f"{task_id}: archived task leaf cannot be a symlink")
    if archive_path.exists():
        raise RuntimeError(f"{task_id}: archived task ID cannot be reused by bulk dispatch")


def _run_status(task: dict[str, Any], command: str, *args: str, reviewer: str | None = None) -> None:
    command_env = {
        "TASK_SUMMARY_ZH": str(task["summary_zh"]),
        "TASK_PHASE": str(task["phase"]),
        "TASK_METADATA_JSON": json.dumps(_metadata(task), ensure_ascii=False, separators=(",", ":")),
    }
    command_args: list[str]
    if command == "assign":
        command_args = [
            str(task["id"]),
            str(task["owner"]),
            reviewer or str(task["reviewer"]),
            str(task["title"]),
        ]
    else:
        command_args = list(args)
    try:
        _run_governed_status_command(command, *command_args, env=command_env)
    except RuntimeError as exc:
        raise RuntimeError(f"{command} {task['id']} failed: {exc}") from exc


def _task_matches(current: dict[str, Any], spec: dict[str, Any]) -> bool:
    required = {
        "title": spec["title"], "summary_zh": spec["summary_zh"],
        "owner": spec["owner"], "reviewer": spec["reviewer"], "phase": spec["phase"],
        **_metadata(spec),
    }
    return all(current.get(key) == value for key, value in required.items())


def _register(task: dict[str, Any]) -> str:
    root = _status_root()
    _assert_task_not_archived(root, str(task["id"]))
    state = _load_state(root)
    current = next((item for item in state.get("tasks", []) if item.get("id") == task["id"]), None)
    if current is not None and str(current.get("status") or "") in TERMINAL_TASK_STATUSES:
        raise RuntimeError(
            f"{task['id']}: terminal task status {current.get('status')} cannot be reused by bulk dispatch"
        )
    if current is not None and _task_matches(current, task):
        return "SKIP"
    _run_status(task, "assign")
    _run_status(task, "note", str(task["id"]), str(task["next"]))
    return "UPDATE" if current is not None else "CREATE"


def _resume_ppl() -> None:
    task = next(item for item in TASKS if item["id"] == "PPL-ALLOC-009")
    state = _load_state(_status_root())
    current = next(item for item in state.get("tasks", []) if item.get("id") == task["id"])
    if current.get("status") != "blocked":
        return
    # Human/Ops is the authority that cleared the credential gate. Temporarily
    # make that actor the reviewer so the governed reopen command can resolve
    # the stale blocker, then restore the independent fleet reviewer.
    _run_status(task, "assign", reviewer="Human/Ops")
    _run_status(
        task,
        "reopen",
        str(task["id"]),
        "Human/Ops credential/strict-deploy gate cleared on 2026-07-21; resume only after the new lease and lifecycle dependencies complete.",
    )
    _run_status(task, "assign")
    _run_status(task, "note", str(task["id"]), str(task["next"]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the DAG without canonical mutations.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.dry_run:
        validation_root = REPO_ROOT
    else:
        if os.environ.get("AI_NAME") != "Human/Ops":
            raise RuntimeError("mutation dispatch requires explicit AI_NAME=Human/Ops")
        _recover_authoritative_projection()
        validation_root = _status_root()
    errors = validate_specs(
        validation_root,
        refuse_archived_reuse=not args.dry_run,
    )
    if errors:
        for error in errors:
            print(f"ERROR {error}", file=sys.stderr)
        return 2
    for task in TASKS:
        deps = ",".join(task.get("depends_on", [])) or "-"
        if args.dry_run:
            print(f"PLAN   {task['id']:32} owner={task['owner']:8} repo={task.get('repository_id', 'existing'):13} deps={deps}")
            continue
        action = _register(task)
        print(f"{action:6} {task['id']:32} owner={task['owner']:8} reviewer={task['reviewer']:8}")
    if not args.dry_run:
        _resume_ppl()
        print(f"Dispatched {len(TASKS) - 2} new tasks and reconciled 2 existing canonical tasks.")
    else:
        print(f"Dry run passed for {len(TASKS) - 2} new tasks plus 2 existing task updates; no writes made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

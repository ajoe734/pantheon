#!/usr/bin/env python3
"""Dispatch MGMT-GAP execution tasks for the 2026-06-30 management-console gap.

Spec: docs/04/pantheon_management_console_gap_2026-06-30/README.md
Packet: docs/bff/execution-tasks/2026-06-30-management-console-production-gap/INDEX.md
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_PATH = Path(REPO_ROOT) / "ai-status.json"
LOG_PATH = Path(REPO_ROOT) / "ai-activity-log.jsonl"
AUTO_BY = "dispatch_management_console_gap_2026-06-30"
SOURCE_REF = {
    "doc": "docs/04/pantheon_management_console_gap_2026-06-30/README.md",
    "archive": "docs/04/pantheon_management_console_gap_2026-06-30/archive/live-audit-2026-06-30.md",
    "reaudit": "docs/04/pantheon_management_console_gap_2026-06-30/archive/full-reaudit-addendum-2026-07-01.md",
    "route_control_reaudit": "docs/04/pantheon_management_console_gap_2026-06-30/archive/route-control-reaudit-2026-07-01.md",
    "route_control_raw": "docs/04/pantheon_management_console_gap_2026-06-30/archive/route-control-reaudit-2026-07-01.json",
    "packet": "docs/bff/execution-tasks/2026-06-30-management-console-production-gap/INDEX.md",
}
PROGRESS_FIELDS = {
    "status",
    "branch",
    "next",
    "updated_at",
    "last_update",
    "started_at",
    "completed_at",
    "closed_at",
    "pr",
    "pr_number",
    "pr_url",
    "merge_commit",
    "merge_sha",
    "review",
    "reviewer_approval",
    "closeout_ref",
    "evidence_ref",
}
TERMINAL_STATUSES = {"done", "superseded", "cancelled"}
NEXT_BY_TASK = {
    "MGMT-GAP-001": "Closed by execute-plans PR #120; keep as evidence input for MGMT-GAP-006.",
    "MGMT-GAP-002": "Closed by execute-plans PR #124/#126; keep endpoint capture evidence as input for MGMT-GAP-006.",
    "MGMT-GAP-003": "Closed by PR #2649; keep BFF OpenAPI/curl evidence as input for MGMT-GAP-008/009/006.",
    "MGMT-GAP-004": "Start durable write truth from the 93-route/510-button re-audit: burn down high-density CTAs, toast/local-only success, writeOverlay fallback, and prove command/audit receipts or explicit disabled states.",
    "MGMT-GAP-005": "Start studios/capabilities hardening from the mock-visible route list: make Formula Studio, Skill Sandbox, Alpha Factory, Tools/MCP/Skills runtime-backed or demote/fixture-gate them.",
    "MGMT-GAP-006": "Wait for MGMT-GAP-004/005/008/009/010, then build the hosted strict-live harness that reproduces or supersedes the 93-route/510-button route-control crawl plus endpoint/mock/write/auth/load detectors.",
    "MGMT-GAP-007": "Wait for MGMT-GAP-006, then archive final PR/deploy/BFF/OpenAPI/hosted-harness evidence and reconcile every route-control re-audit finding.",
    "MGMT-GAP-008": "Start detail honesty hardening: fix live-id detail undefined/blank/NaN states, direct-render detail aliases, empty capability registries, and evidence-source degraded copy.",
    "MGMT-GAP-009": "Start session/RBAC and provider-auth contract hardening: align /bff/me, tenant, roles, LLM Provider Auth degraded state, and management reads for the documented dev gate token path.",
    "MGMT-GAP-010": "Start load-gate work: enforce management bundle budgets, build-warning gates, route-ready markers, shell request-count evidence, and no network-idle-only readiness.",
}
PRIMARY_AGENT_NEXT_TASK = {
    "Claude": "MGMT-GAP-008",
    "Claude2": "MGMT-GAP-009",
    "Gemini": "MGMT-GAP-005",
    "Gemini2": "MGMT-GAP-010",
    "Codex": "MGMT-GAP-004",
}


# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts, metadata)
TASKS = [
    (
        "MGMT-GAP-001",
        "Management route and IA cleanup",
        "清除 management console 真重複入口: control-room-legacy 不再 render 舊 ControlRoom; "
        "deployment/deployment/:id 改 canonical deployments redirect; 收斂一級 nav 中非 production 的 studios/empty registry/loop 子頁。",
        "Codex2",
        "Claude",
        "MGMT Console Production Gap / Batch 1 route IA",
        "",
        "control-room-legacy 不再 render 舊頁; deployment singular routes redirect; nav 收斂或每項有 production readiness; route tests + hosted probe 綠",
        "frontend-checkout:src/App.tsx,frontend-checkout:src/management/ManagementLayout.tsx,frontend-checkout:e2e,docs/04/pantheon_management_console_gap_2026-06-30",
        {"batch": 1, "fleet_lane": "frontend-route-ia"},
    ),
    (
        "MGMT-GAP-003",
        "BFF management DTO contract hardening",
        "為 /bff/management/data-sources、permissions、memory-governance、consult-rules、/bff/lineage、/bff/workflows、/bff/hooks、/bff/knowledge 補齊 DTO 契約、degraded envelope、OpenAPI schema 與 contract tests。",
        "Claude2",
        "Codex",
        "MGMT Console Production Gap / Batch 2 BFF contracts",
        "",
        "所有端點 OpenAPI + contract test + hosted curl 200; 空資料為明確 degraded/unavailable 而不是 ambiguous []",
        "services/control-plane/bff,docs/04/pantheon_management_console_gap_2026-06-30",
        {"batch": 2, "fleet_lane": "bff-contract"},
    ),
    (
        "MGMT-GAP-002",
        "Frontend canonical management read wiring",
        "將 Data Sources、permissions、memory、consult、lineage、workflows、hooks、ranking 改接 canonical management endpoints；移除 strict live seed/mock 偽裝。",
        "Claude",
        "Codex",
        "MGMT Console Production Gap / Batch 2 FE canonical reads",
        "MGMT-GAP-003",
        "hosted browser probe 捕捉每頁 intended endpoint; success/degraded 測試綠; 無 seed/mock 被顯示為 live truth",
        "frontend-checkout:src/lib/bff-v1,frontend-checkout:src/management,frontend-checkout:e2e,docs/04/pantheon_management_console_gap_2026-06-30",
        {"batch": 2, "fleet_lane": "frontend-bff-integration"},
    ),
    (
        "MGMT-GAP-004",
        "Management command receipts and write truth",
        "依 93-route/510-button 重盤點結果盤點 ranking/governance/workflows/hooks/settings/detail panels 的 write-like CTA；全部改為 governed command receipt/audit flow，或明確 disabled non-production。",
        "Codex",
        "Claude2",
        "MGMT Console Production Gap / Batch 3 command truth",
        "MGMT-GAP-002,MGMT-GAP-003",
        "無 in-scope CTA 只靠 toast/local state 成功; high-density route/control 熱點有 command/audit evidence 或 disabled reason; writeOverlay/writeFallback 不被宣稱為 production persistence; dry-run/real-writes-off probe 證明無隱性 side effect",
        "frontend-checkout:src/management,frontend-checkout:src/lib/bff,services/control-plane/bff,docs/04/pantheon_management_console_gap_2026-06-30",
        {"batch": 3, "fleet_lane": "command-governance"},
    ),
    (
        "MGMT-GAP-005",
        "Studios and capabilities to production level",
        "Formula Studio/Skill Sandbox/Alpha Factory/mock-visible capability routes 接真 backtest/skill runner/readback 或降級/fixture-gate；Tools/MCP/Skills create/import/publish/retire actions 全部 governed 或 disabled。",
        "Gemini",
        "Claude",
        "MGMT Console Production Gap / Batch 4 studios capabilities",
        "MGMT-GAP-003",
        "hosted probe 無 mock trace/backtest 被標成 live success; 10 個 mock-visible route findings 被 demote、fixture-gate 或 runtime-backed; runner contracts 有 tests/limits; capability actions 有 command/job id 或 disabled",
        "frontend-checkout:src/management/pages/studios,frontend-checkout:src/management/pages,services/control-plane/bff,docs/04/pantheon_management_console_gap_2026-06-30",
        {"batch": 4, "fleet_lane": "runtime-capability"},
    ),
    (
        "MGMT-GAP-006",
        "Hosted management production acceptance harness",
        "建立 hosted management probe: visible nav、hidden aliases、canonical/detail final paths、endpoint capture、strict-live no seed fallback、write CTA mock detection、console/CORS failures、button/disabled counts、load/build signals。",
        "Gemini2",
        "Codex",
        "MGMT Console Production Gap / Batch 5 acceptance harness",
        "MGMT-GAP-001,MGMT-GAP-002,MGMT-GAP-004,MGMT-GAP-005,MGMT-GAP-008,MGMT-GAP-009,MGMT-GAP-010",
        "probe 覆蓋所有 visible management nav + hidden/detail aliases; 輸出 JSON/Markdown evidence; reproduces or supersedes 93-route/510-button crawl; release gate 可 fail legacy render/missing endpoint/mock write success/undefined/NaN/session mismatch/load regression",
        "frontend-checkout:scripts,frontend-checkout:e2e,scripts/aggregate-release-gate.mjs,docs/04/pantheon_management_console_gap_2026-06-30/archive",
        {"batch": 5, "fleet_lane": "integration-qa"},
    ),
    (
        "MGMT-GAP-007",
        "Management production closeout and archive proof",
        "緊盯 MGMT-GAP 全任務到 done/superseded；確認 PR/merge/deploy/probe evidence；逐項 reconcile route/control re-audit；歸檔最終 production proof 與 residual risk owner/expiry。",
        "Codex",
        "Claude",
        "MGMT Console Production Gap / Batch 5 oversight closeout",
        "MGMT-GAP-006",
        "所有 MGMT-GAP 任務 done 或 reviewed superseded; final archive 含 FE deploy/BFF/OpenAPI/hosted probe/PR SHA; route-control re-audit 每個 finding 都列 fixed/superseded/blocked; closeout 清楚列 completion 或 blocker",
        "ai-status.json,docs/04/pantheon_management_console_gap_2026-06-30/archive,docs/bff/execution-tasks/2026-06-30-management-console-production-gap",
        {"batch": 5, "fleet_lane": "oversight-closeout"},
    ),
    (
        "MGMT-GAP-008",
        "Management detail DTO and render honesty",
        "修正 2026-07-01 全量再盤點發現的 detail DTO/render 誠實度問題: status.undefined、risk.undefined、blank h1/owner/update、NaN%、direct-render detail aliases、empty capability registry seed ids。",
        "Claude",
        "Codex",
        "MGMT Console Production Gap / Batch 2.5 detail render honesty",
        "MGMT-GAP-002,MGMT-GAP-003",
        "live-id detail pages 不再顯示 undefined/blank/NaN; capital-pools/ranking-formulas/rebalances/research detail aliases redirect or canonicalize; empty Tools/MCP/Skills registries show explicit live-empty state; hosted probe records live ids and screenshots/logs",
        "frontend-checkout:src/management,frontend-checkout:src/lib/bff-v1,frontend-checkout:e2e,docs/04/pantheon_management_console_gap_2026-06-30/archive/full-reaudit-addendum-2026-07-01.md",
        {"batch": 2.5, "fleet_lane": "frontend-detail-honesty"},
    ),
    (
        "MGMT-GAP-009",
        "Management session auth and RBAC contract consistency",
        "修正 /bff/me 403 但其他 management BFF reads 200 的 session/RBAC 契約裂縫；dev gate token、tenant、roles、LLM Provider Auth degraded state 與 FE session bootstrap 必須一致 fail-closed。",
        "Claude2",
        "Codex",
        "MGMT Console Production Gap / Batch 2.5 session auth contract",
        "MGMT-GAP-003",
        "/bff/me/provider-auth 與 management reads 對同一 token/tenant 的 auth 結果一致; privileged pages 不可在 session 403 時顯示 live data; hosted-origin probe 不以 localhost CORS 作為 production proof; BFF + hosted browser tests cover success/403/role-missing paths",
        "services/control-plane/bff,frontend-checkout:src/lib/bff,frontend-checkout:e2e,docs/04/pantheon_management_console_gap_2026-06-30/archive/full-reaudit-addendum-2026-07-01.md",
        {"batch": 2.5, "fleet_lane": "bff-session-rbac"},
    ),
    (
        "MGMT-GAP-010",
        "Management console load and release gate performance",
        "落實 management load-gap follow-up: code split management routes、defer shell fanout、aggregate counts、remove duplicate jobs reads、把 bundle/build warning/load regression 納入 release gate。",
        "Gemini2",
        "Codex",
        "MGMT Console Production Gap / Batch 5 load release gate",
        "MGMT-GAP-001,MGMT-GAP-002",
        "management initial bundle/runtime chunk budget documented and enforced; CSS minify warning/static+dynamic import conflicts/large chunks fail or receive reviewed waiver; shell no longer fans out duplicate heavyweight reads before route readiness; hosted probe avoids network-idle false readiness and records load evidence",
        "docs/04/pantheon_management_console_load_gap_2026-07-01,frontend-checkout:src,frontend-checkout:scripts,frontend-checkout:e2e",
        {"batch": 5, "fleet_lane": "frontend-performance-gate"},
    ),
]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def load_state() -> dict:
    return json.loads(STATUS_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATUS_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_log(entry: dict) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def upsert_task(state: dict, task: dict) -> tuple[bool, str]:
    tasks = state.setdefault("tasks", [])
    for index, existing in enumerate(tasks):
        if existing.get("id") == task["id"]:
            merged = {**existing, **task}
            if existing.get("status") and existing.get("status") != "todo":
                for key in PROGRESS_FIELDS:
                    if key in existing:
                        merged[key] = existing[key]
            tasks[index] = merged
            return False, str(merged.get("status") or "")
    tasks.append(task)
    return True, str(task.get("status") or "")


def remove_terminal_task_from_agents(state: dict, task_id: str) -> None:
    for agent in state.get("agents", []):
        ids = agent.get("current_task_ids")
        if not isinstance(ids, list):
            continue
        agent["current_task_ids"] = [item for item in ids if item != task_id]


def assign_agent(state: dict, owner: str, task_id: str, timestamp: str, next_note: str, inserted: bool) -> None:
    for agent in state.get("agents", []):
        if agent.get("name") != owner:
            continue
        ids = agent.setdefault("current_task_ids", [])
        if task_id not in ids:
            ids.append(task_id)
        should_update = (
            inserted
            or agent.get("next") in (None, "", "Assignment created")
            or PRIMARY_AGENT_NEXT_TASK.get(owner) == task_id
        )
        if should_update:
            agent["status"] = "waiting"
            agent["next"] = next_note
            agent["last_update"] = timestamp
        return


def main() -> int:
    state = load_state()
    timestamp = iso_now()
    for task_id, title, summary, owner, reviewer, phase, deps, acceptance, artifacts, metadata in TASKS:
        task_metadata = {
            "source_ref": SOURCE_REF,
            "delivery_layer": "primary",
            "mutates_canonical": True,
            **metadata,
        }
        task = {
            "id": task_id,
            "title": title,
            "summary_zh": summary,
            "phase": phase,
            "owner": owner,
            "reviewer": reviewer,
            "status": "todo",
            "depends_on": split_csv(deps),
            "artifacts": split_csv(artifacts),
            "acceptance": split_csv(acceptance),
            "next": NEXT_BY_TASK.get(task_id, "Assignment created"),
            "last_update": timestamp,
            "TASK_CLASS": "execution",
        }
        task.update(task_metadata)
        task["task_class"] = "execution"
        task["auto_created_by"] = AUTO_BY
        task["auto_generated"] = True
        task["mutates_canonical"] = True
        task.pop("TASK_CLASS", None)
        inserted, status_after = upsert_task(state, task)
        if status_after in TERMINAL_STATUSES:
            remove_terminal_task_from_agents(state, task_id)
        else:
            assign_agent(state, owner, task_id, timestamp, task["next"], inserted)
        if inserted:
            append_log(
                {
                    "ts": timestamp,
                    "agent": os.environ.get("AI_NAME", "Codex"),
                    "type": "assign",
                    "task_id": task_id,
                    "message": f"Assigned {task_id} to {owner} with reviewer {reviewer}",
                }
            )
        action = "CREATE" if inserted else "UPSERT"
        print(f"{action} {task_id:13} owner={owner:8} reviewer={reviewer:8} deps={deps or '-'}")
    state["updated_at"] = timestamp
    save_state(state)
    print("Done. Existing active tasks were preserved; dashboard sync is intentionally not run here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

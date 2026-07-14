#!/usr/bin/env python3
"""Dispatch Evolution generative-loop gap tasks for 2026-07-14."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS_ROOT = Path(os.path.expanduser(os.environ.get("PANTHEON_STATUS_ROOT", str(REPO_ROOT)))).resolve()
STATUS_PATH = STATUS_ROOT / "ai-status.json"
LOG_PATH = STATUS_ROOT / "ai-activity-log.jsonl"

AUTO_BY = "dispatch_evolution_generative_loop_gap_2026-07-14"
PACKET = "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/INDEX.md"
SPEC = "docs/04/pantheon_evolution_generative_loop_gap_2026-07-14/EVOLUTION_GENERATIVE_LOOP_GAP.md"
SOURCE_REF = {
    "doc": SPEC,
    "packet": PACKET,
    "extends": "docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/INDEX.md",
    "trigger": (
        "2026-07-14 generative half-loop has never fired: dispatch worker undeployed, "
        "research plane starved, bindings run rescue placeholders fed by host cron signals, "
        "pnl=0.0 with 7325 trades, drawdown never emitted, baselines empty"
    ),
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
GENERIC_NEXT_MESSAGES = {
    None,
    "",
    "Assignment created",
    "Assignment created from Evolution generative loop gap packet",
}
PRIMARY_AGENT_NEXT_TASK = {
    "Codex": "EVOLOOP-001",
    "Claude": "EVOLOOP-002",
    "Codex2": "EVOLOOP-005",
}
NEXT_BY_TASK = {
    "EVOLOOP-001": "Deploy evolution-dispatch-worker as a default-on dev compose service; prove an approved decision auto-executes.",
    "EVOLOOP-002": "Fix PnL mark-to-market supply and add drawdown computation; emit pnl/drawdown_snapshot events with as-of stamps.",
    "EVOLOOP-003": "Define the minimal evolvable strategy artifact contract and register one genuine v1 for an existing persona binding.",
    "EVOLOOP-004": "Consume the dispatched retrain in the research plane; produce artifact v2 with real lineage and parameter delta.",
    "EVOLOOP-005": "Populate expected_drawdown baseline for v1 and enable calibrated rolling_pnl_floor once 002 lands (live config + policy_source).",
    "EVOLOOP-006": "Run the promote pipeline: registry -> deployment plan -> replace one rescue binding; document and test rollback.",
    "EVOLOOP-007": "Make the promoted binding trade on strategy-emitted signals via normal ingest; disable the generic feeder for that binding only.",
    "EVOLOOP-008": "Build the full-cycle live verifier with per-segment failure reporting; wire into run_e2e_verifiers.sh.",
    "EVOLOOP-009": "Deploy everything to dev, verify hosted console + live curl evidence, close out with residual risks.",
}


TASKS = [
    (
        "EVOLOOP-001",
        "Deploy evolution dispatch worker",
        "把已寫好的 services/evolution/dispatch_worker.py(LOOP-AUTO-EVO-004)部署成 dev 預設啟動的 compose 服務:輪詢 approved EvolutionDecision、走 gated execute 路徑派執行。自帶 interval env、fail-closed、不碰任何既有 cadence。證據:一筆 approved decision 不經人工 curl 自動轉 executed 並帶 dispatch metadata。",
        "Codex",
        "Claude",
        "Evolution Generative Loop / Wave 0 dispatch plumbing",
        [],
        [
            "compose service runs dispatch_worker by default on dev with its own interval env and healthcheck",
            "an approved decision transitions to executed with dispatch metadata without manual curl",
            "worker is idempotent across restarts and does not double-dispatch a decision",
            "failure to reach the evolution API logs diagnostics and dispatches nothing",
        ],
        [
            "services/evolution/dispatch_worker.py",
            "docker-compose.yml",
            "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-001-dispatch-worker-deploy.md",
        ],
        {"wave": 0, "fleet_lane": "be-evolution-dispatch-deploy"},
    ),
    (
        "EVOLOOP-002",
        "Real performance telemetry supply (PnL + drawdown)",
        "修 paper 績效遙測的根:per-binding rolling PnL 要用 fills+行情做 mark-to-market(查明並修掉現在 14 個 binding 全部 pnl=0.0 的原因,其中一個有 7325 筆成交),並計算 rolling drawdown;以 schema-valid 的 pnl_snapshot/drawdown_snapshot telemetry events 發出、帶 as-of 時戳。fail-closed:沒有 marks 就只出 diagnostic,不得造數。收斂 EVOCHAIN-001 裁決文件(.orchestrator/task-briefs/evochain_001_upstream_decision.md)指出的上游缺口;取代原議的 EVOCHAIN-012。",
        "Claude",
        "Codex",
        "Evolution Generative Loop / Wave 0 telemetry supply",
        [],
        [
            "root cause of pnl=0.0 across 14 summaries is documented and fixed",
            "active bindings show numeric moving pnl and drawdown in runtime summaries with per-field as-of stamps",
            "pnl_snapshot and drawdown_snapshot events validate against telemetry_event.schema.json",
            "missing marks produce diagnostics and no snapshot",
            "threshold sweep producer tick evaluates real values instead of skipping (its diagnostics prove it)",
        ],
        [
            "services/telemetry",
            "services/execution",
            "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-002-performance-telemetry.md",
        ],
        {"wave": 0, "fleet_lane": "be-performance-telemetry-supply"},
    ),
    (
        "EVOLOOP-003",
        "Minimal evolvable strategy artifact (contract + v1)",
        "定義「最小可演化策略 artifact」契約:LEAN 相容的演算法參照 + 具名參數集 + 版本與 lineage 欄位,註冊進 registry;並為一個現役 persona binding 產出一個貨真價實的 v1 artifact(可用已驗證的 TW 動能邏輯參數化,品質不是重點,可被程式化變異才是)。取代該 binding 的佔位 artifact 命名。",
        "Claude",
        "Codex2",
        "Evolution Generative Loop / Wave 0 artifact contract",
        [],
        [
            "strategy artifact contract documents algorithm reference parameter set version and lineage fields",
            "one genuine v1 artifact is registered in the registry and linked to an existing persona binding",
            "the parameter set is programmatically mutable (documented mutation surface for retrain)",
            "contract does not break existing registry consumers",
        ],
        [
            "services/registry",
            "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-003-strategy-artifact-contract.md",
        ],
        {"wave": 0, "fleet_lane": "be-strategy-artifact-contract"},
    ),
    (
        "EVOLOOP-004",
        "Research plane produces artifact v2",
        "讓 research plane 真的產出演化產物:dispatch worker 派來的 retrain 進 research-orchestrator 成 work item,由 training-session/optimizer 執行一次真實的最小 retrain(參數變異),產出 artifact v2 註冊進 registry,lineage 帶 {v1, decision_id, work_item_id, session_id}。v2 的參數必須與 v1 有真實差異,不得假輸出。",
        "Codex",
        "Claude",
        "Evolution Generative Loop / Wave 1 research production",
        ["EVOLOOP-001", "EVOLOOP-003"],
        [
            "dispatched retrain creates a research work item traceable to the decision id",
            "a real training or optimizer session runs and records its id",
            "artifact v2 is registered with lineage to v1 decision_id work_item_id and session_id",
            "v2 parameters differ from v1 and the delta is recorded",
        ],
        [
            "services/research-orchestrator",
            "services/training-session",
            "services/optimizer-svc",
            "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-004-research-artifact-v2.md",
        ],
        {"wave": 1, "fleet_lane": "be-research-artifact-production"},
    ),
    (
        "EVOLOOP-005",
        "Governed baselines + threshold activation",
        "為 v1 artifact 依文件化治理流程填入 expected_drawdown baseline(threshold_sweep_baselines.json,live config);EVOLOOP-002 落地後以校準值啟用 rolling_pnl_floor。兩者都要帶 policy_source 註記,不得改 image。",
        "Codex2",
        "Codex",
        "Evolution Generative Loop / Wave 1 governance config",
        ["EVOLOOP-002", "EVOLOOP-003"],
        [
            "expected_drawdown baseline for the v1 artifact is populated via the documented governance flow",
            "rolling_pnl_floor is enabled with a calibrated value derived from real PnL data",
            "both config edits carry policy_source notes and require no image rebuild",
            "threshold sweep producer evaluates the v1 binding without baseline-missing diagnostics",
        ],
        [
            "services/evolution/config/threshold_sweep_baselines.json",
            "services/evolution/config/threshold_sweep_thresholds.json",
            "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-005-governed-baselines.md",
        ],
        {"wave": 1, "fleet_lane": "ops-governed-baselines"},
    ),
    (
        "EVOLOOP-006",
        "Promote pipeline: registry to LEAN binding",
        "跑通 promote 管線:registry artifact → deployment plan → 以管線(非手動改 store)替換一個 rescue 佔位 binding 成 pipeline-managed binding。遵守 RuntimeBinding 契約(runtime_id 必須等於容器 PANTHEON_RUNTIME_ID;參照 paper-binding-rescue runbook)。rollback 路徑要文件化並實測(re-bind 前一個 artifact)。",
        "Codex2",
        "Claude",
        "Evolution Generative Loop / Wave 1 promote pipeline",
        ["EVOLOOP-003"],
        [
            "a deployment plan created from the registry artifact replaces one rescue binding through service APIs only",
            "runtime_id matches the container PANTHEON_RUNTIME_ID and the binding is active in the paper runtime",
            "rollback to the previous artifact is documented and demonstrated",
            "no hand-edits to RuntimeBinding or read stores anywhere in the evidence",
        ],
        [
            "services/deployment",
            "services/registry",
            "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-006-promote-pipeline.md",
        ],
        {"wave": 1, "fleet_lane": "be-promote-pipeline"},
    ),
    (
        "EVOLOOP-007",
        "Strategy-driven signals for the promoted binding",
        "讓被 promote 的 binding 的訊號來自它的策略 artifact(參數化邏輯),走正常 signal ingest 進 signal-store;僅對該 binding 停用通用 cron feeder(feed_signals*.sh 對其他 binding 維持不動)。證據:該 binding 的成交可追溯到策略發出的 signal。",
        "Claude",
        "Codex",
        "Evolution Generative Loop / Wave 2 strategy signals",
        ["EVOLOOP-006"],
        [
            "signals for the promoted binding originate from its strategy artifact logic through normal ingest",
            "generic cron feeder is disabled for that binding only and other bindings are unaffected",
            "trades on the binding trace back to strategy-emitted signal ids",
            "signal production is fail-closed on missing market data",
        ],
        [
            "services/signal-store",
            "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-007-strategy-signals.md",
        ],
        {"wave": 2, "fleet_lane": "be-strategy-signal-producer"},
    ),
    (
        "EVOLOOP-008",
        "Full-cycle live verifier",
        "寫全圈 live 驗證:breach(真實或經 producer 正式入口注入)→ incident → sweep proposal → approve → dispatch worker 自動 execute → research work item → artifact v2 → promote → binding v2 上線 → 交易 → 演化日誌記錄完整一圈(每段有 linked id)。每段失敗要能分辨;冪等可重跑;納入 scripts/run_e2e_verifiers.sh。注入指引見 .orchestrator/task-briefs/evochain_001_upstream_decision.md。與 EVOCHAIN-010(觀測半圈 verifier)分工不重疊。",
        "Codex",
        "Claude",
        "Evolution Generative Loop / Wave 2 verification",
        ["EVOLOOP-002", "EVOLOOP-004", "EVOLOOP-006"],
        [
            "verifier drives the full generative cycle on live dev with linked ids at every stage",
            "each segment failure is reported distinctly",
            "verifier is idempotent and does not pollute stores across re-runs",
            "wired into scripts/run_e2e_verifiers.sh",
        ],
        [
            "scripts/run_e2e_verifiers.sh",
            "scripts",
            "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-008-full-cycle-verifier.md",
        ],
        {"wave": 2, "fleet_lane": "verify-generative-loop"},
    ),
    (
        "EVOLOOP-009",
        "Dev deploy + packet closeout",
        "整包部署到 dev 並收尾:所有 PR merge、服務重佈、hosted 管理台證據(演化日誌顯示 executed decision 全圈、Persona Fleet 最近 MUTATION 連到 formal entry、promoted binding 顯示 artifact v2)、live curl 驗證、殘餘風險含 owner/expiry。deploy 未經 live 驗證不得宣告完成(babysit rule)。",
        "Codex2",
        "Human/Ops",
        "Evolution Generative Loop / Wave 3 closeout",
        ["EVOLOOP-005", "EVOLOOP-007", "EVOLOOP-008"],
        [
            "dev redeployed with all merged packet PRs and all loop services running",
            "hosted console shows the executed decision cycle formal entries and the promoted binding on artifact v2",
            "live curl evidence archived for every stage surface",
            "closeout lists every PR merge SHA and residual risk with owner and expiry",
        ],
        [
            "docs/04/pantheon_evolution_generative_loop_gap_2026-07-14/archive",
            "docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-009-closeout.md",
        ],
        {"wave": 3, "fleet_lane": "ops-evoloop-closeout"},
    ),
]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_state() -> dict:
    if not STATUS_PATH.exists():
        raise FileNotFoundError(f"status file not found: {STATUS_PATH}")
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


def assign_agent(state: dict, owner: str, task_id: str, timestamp: str, next_note: str) -> None:
    for agent in state.get("agents", []):
        if agent.get("name") != owner:
            continue
        ids = agent.setdefault("current_task_ids", [])
        if task_id not in ids:
            ids.append(task_id)
        if agent.get("next") in GENERIC_NEXT_MESSAGES or PRIMARY_AGENT_NEXT_TASK.get(owner) == task_id:
            agent["status"] = "waiting"
            agent["next"] = next_note
            agent["last_update"] = timestamp
        return


def build_task(
    task_id: str,
    title: str,
    summary: str,
    owner: str,
    reviewer: str,
    phase: str,
    deps: list[str],
    acceptance: list[str],
    artifacts: list[str],
    metadata: dict,
    timestamp: str,
) -> dict:
    task = {
        "id": task_id,
        "title": title,
        "summary_zh": summary,
        "phase": phase,
        "owner": owner,
        "reviewer": reviewer,
        "status": "todo",
        "depends_on": deps,
        "artifacts": artifacts,
        "acceptance": acceptance,
        "next": NEXT_BY_TASK.get(task_id, "Assignment created from Evolution generative loop gap packet"),
        "last_update": timestamp,
        "task_class": "execution",
        "auto_created_by": AUTO_BY,
        "auto_generated": True,
        "source_ref": SOURCE_REF,
        "delivery_layer": "primary",
        "mutates_canonical": True,
        "helper_kind": "evolution_generative_loop_gap_execution_slice",
    }
    task.update(metadata)
    return task


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print creates/upserts without writing ai-status.json or ai-activity-log.jsonl.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = load_state()
    timestamp = iso_now()
    inserted_logs: list[dict] = []

    for task_tuple in TASKS:
        task_id, title, summary, owner, reviewer, phase, deps, acceptance, artifacts, metadata = task_tuple
        task = build_task(
            task_id,
            title,
            summary,
            owner,
            reviewer,
            phase,
            deps,
            acceptance,
            artifacts,
            metadata,
            timestamp,
        )
        inserted, status_after = upsert_task(state, task)
        if status_after in TERMINAL_STATUSES:
            remove_terminal_task_from_agents(state, task_id)
        else:
            assign_agent(state, owner, task_id, timestamp, task["next"])
        if inserted:
            inserted_logs.append(
                {
                    "ts": timestamp,
                    "agent": os.environ.get("AI_NAME", "Human/Ops"),
                    "type": "assign",
                    "task_id": task_id,
                    "message": f"Assigned {task_id} to {owner} with reviewer {reviewer}",
                }
            )
        print(
            f"{'CREATE' if inserted else 'UPSERT'} {task_id:12} "
            f"owner={owner:8} reviewer={reviewer:10} wave={metadata.get('wave')} deps={','.join(deps) if deps else '-'}"
        )

    state["updated_at"] = timestamp
    if args.dry_run:
        print(f"Dry run only. No writes made. status_root={STATUS_ROOT}")
        return 0

    save_state(state)
    for entry in inserted_logs:
        append_log(entry)
    print(f"Done. Updated {STATUS_PATH}.")
    print("Run `PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py sync` to refresh generated status views.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Dispatch Evolution Journal producer-chain gap tasks for 2026-07-13."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from canonical_writer_guard import assert_isolated_legacy_write_target

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS_ROOT = Path(os.path.expanduser(os.environ.get("PANTHEON_STATUS_ROOT", str(REPO_ROOT)))).resolve()
STATUS_PATH = STATUS_ROOT / "ai-status.json"
LOG_PATH = STATUS_ROOT / "ai-activity-log.jsonl"

AUTO_BY = "dispatch_evolution_journal_producer_gap_2026-07-13"
PACKET = "docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/INDEX.md"
SPEC = "docs/04/pantheon_evolution_journal_producer_gap_2026-07-13/EVOLUTION_JOURNAL_PRODUCER_GAP.md"
SOURCE_REF = {
    "doc": SPEC,
    "packet": PACKET,
    "extends": "docs/bff/execution-tasks/2026-07-10-persona-fleet-mutation-evolution-gap/INDEX.md",
    "trigger": "2026-07-13 Evolution Journal live content is seed-only (evo-vslice-1); producer chain never fires; freeze/rollback surfaces missing cause permanent degraded badge",
    "live_evidence": "dev /bff/management/evolution-journal = 2 seed items; /bff/incidents = 1 seed incident open since 2026-06-15",
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
    "Assignment created from Evolution Journal producer gap packet",
}
PRIMARY_AGENT_NEXT_TASK = {
    "Claude": "EVOCHAIN-001",
    "Codex": "EVOCHAIN-002",
    "Codex2": "EVOCHAIN-003",
}
NEXT_BY_TASK = {
    "EVOCHAIN-001": "Build threshold-breach producer: telemetry aggregates -> incidents consumer; idempotent, fail-closed, daily interval env.",
    "EVOCHAIN-002": "Enable evolution-daily-sweep-scheduler on dev and prove one sweep tick converts the open seed incident into a proposal.",
    "EVOCHAIN-003": "Add postmortem publisher on incident resolve; route via postmortem_bridge into POST /api/evolution/proposals.",
    "EVOCHAIN-004": "Give freeze_orders/rollbacks a governance canonical store + service read API; add BFF service_client path.",
    "EVOCHAIN-005": "Persist BFF freeze/rollback governance writes into the canonical store with audit fields.",
    "EVOCHAIN-006": "Wire console mutation review actions to evolution proposal APIs via BFF commands with journal projection.",
    "EVOCHAIN-007": "Add server-side persona/mutation filters + paging to /bff/management/evolution-journal; mark origin:seed entries.",
    "EVOCHAIN-008": "Fix FE data-source badge: live-degraded (named surfaces) vs true snapshot; stop mislabeling live data as SNAPSHOT DATA.",
    "EVOCHAIN-009": "Render formal-entry fields and fixture badge on Evolution Journal cards; keep 2026-07-10 fallback contract.",
    "EVOCHAIN-010": "Ship producer-chain live verifier (breach->incident->proposal->journal) and add it to run_e2e_verifiers.sh.",
    "EVOCHAIN-011": "Deploy to dev, enable sweep, verify all journal surfaces ok via live curl + hosted screenshots; closeout.",
}


TASKS = [
    (
        "EVOCHAIN-001",
        "Threshold-breach producer (telemetry -> incidents)",
        "新增 threshold-breach producer worker：週期讀 per-binding/per-persona paper 績效彙總，用 governance ThresholdEvaluator schema（值放 live config，改值不用重建 image）評估 drawdown/PnL 閾值，命中就 POST 到 incidents consumer。必須冪等（binding+metric+window dedupe key）且 fail-closed（telemetry 缺就只記 diagnostic，不得捏造 breach）。自帶每日 interval env，不碰任何既有 supervisor cadence。",
        "Claude",
        "Codex",
        "Evolution Journal Producer Gap / Wave 0 producer",
        [],
        [
            "producer evaluates live paper telemetry aggregates against governance-schema thresholds from live config",
            "breach POSTs canonical payload accepted by ThresholdTelemetryIncidentConsumer and creates an IncidentCase",
            "re-runs do not duplicate open incidents for the same binding metric window (dedupe key recorded)",
            "missing or ambiguous telemetry emits diagnostics and produces no incident",
            "compose service ships with EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS default 86400 and its own logs",
        ],
        [
            "services/evolution",
            "services/incidents",
            "docker-compose.yml",
            "docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/EVOCHAIN-001-threshold-breach-producer.md",
        ],
        {"wave": 0, "fleet_lane": "be-threshold-breach-producer"},
    ),
    (
        "EVOCHAIN-002",
        "Enable evolution daily sweep on dev",
        "解除（或以 committed override 取代）docker-compose.yml 中 evolution-daily-sweep-scheduler 的 profiles gate，讓 dev `docker compose up -d` 預設啟動 daily sweep。用 scheduler tick log 以及既有 open seed incident 被掃成 decision proposal 作為證據。interval 沿用既有 env 預設，不改 cadence 設計。",
        "Codex",
        "Claude",
        "Evolution Journal Producer Gap / Wave 0 sweep activation",
        [],
        [
            "docker compose up -d on dev starts evolution-daily-sweep-scheduler without extra profile flags",
            "scheduler tick evidence recorded from dev",
            "existing open incident inc-87c655c3e3c9 is swept into an EvolutionDecision proposal visible in the journal",
            "runbook note documents how to disable the scheduler intentionally",
        ],
        [
            "docker-compose.yml",
            "services/evolution/scheduler_worker.py",
            "docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/EVOCHAIN-002-sweep-activation.md",
        ],
        {"wave": 0, "fleet_lane": "ops-evolution-sweep-activation"},
    ),
    (
        "EVOCHAIN-003",
        "Postmortem publisher on incident resolution",
        "補上 postmortem 事件鏈缺的呼叫端：incident resolve/close 時產生 postmortem record，經 services/evolution/postmortem_bridge.on_postmortem_published 轉成 proposal，並經 POST /api/evolution/proposals 入庫。bridge 本身保持純函式不動。",
        "Codex2",
        "Claude",
        "Evolution Journal Producer Gap / Wave 0 postmortem publisher",
        [],
        [
            "resolving an incident produces a postmortem record visible on the postmortems surface",
            "published postmortem events route through postmortem_bridge and admit proposals via the existing endpoint",
            "bridge module remains a pure transformation with unchanged contract",
            "duplicate resolution events do not create duplicate postmortems or proposals",
        ],
        [
            "services/incidents",
            "services/incident",
            "services/evolution/postmortem_bridge.py",
            "docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/EVOCHAIN-003-postmortem-publisher.md",
        ],
        {"wave": 0, "fleet_lane": "be-postmortem-publisher"},
    ),
    (
        "EVOCHAIN-004",
        "Canonical store + read API for freeze orders and rollbacks",
        "給 freeze_orders 與 rollbacks 一個 canonical 後端：governance service 持有 dataset 與 service read API；BFF read_store 對這兩個 dataset 增加 service_client 讀取路徑，local snapshot 降為 fallback-only。目標是 strict/live 模式下 surface 從 missing 變 ok。",
        "Codex",
        "Claude",
        "Evolution Journal Producer Gap / Wave 0 canonical store",
        [],
        [
            "governance service exposes list/read APIs for freeze_orders and rollbacks backed by its data dir",
            "BFF read_store list_freeze_orders and list_all_rollbacks read via service_client first and local snapshot only as fallback",
            "dataset surface status for freeze_orders and all_rollbacks reports ok when the service is healthy even with zero records",
            "contract tests cover empty-store ok state and populated state",
        ],
        [
            "services/governance",
            "services/control-plane/bff/read_store.py",
            "services/control-plane/bff/tests",
            "docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/EVOCHAIN-004-freeze-rollback-store.md",
        ],
        {"wave": 0, "fleet_lane": "be-freeze-rollback-store"},
    ),
    (
        "EVOCHAIN-005",
        "Governance write endpoints persist to canonical store",
        "把 BFF 的 freeze/rollback 治理 write endpoints（approve/execute/reject 路徑）改為寫入 EVOCHAIN-004 的 canonical store，含完整審計欄位（actor、identity、時間、來源 command）。寫入後 read 面即可見。",
        "Codex2",
        "Codex",
        "Evolution Journal Producer Gap / Wave 1 governance writes",
        ["EVOCHAIN-004"],
        [
            "freeze/rollback lifecycle writes land in the canonical store and are immediately readable via the service API",
            "audit fields include actor identity timestamps and originating command reference",
            "journal aggregate includes freeze order and rollback entries after writes",
            "write paths remain gated by existing MFA/approval rules unchanged",
        ],
        [
            "services/control-plane/bff/main.py",
            "services/governance",
            "services/control-plane/bff/tests",
            "docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/EVOCHAIN-005-governance-writes.md",
        ],
        {"wave": 1, "fleet_lane": "be-governance-write-persistence"},
    ),
    (
        "EVOCHAIN-006",
        "Console mutation review wiring to evolution APIs",
        "把管理台對 proposal 的 review/approve/reject/execute 操作接到 evolution service 既有 API（/api/evolution/proposals/{id}/...），走 BFF command 層，結果投影回演化日誌同一 formal entry 的狀態轉移。",
        "Claude",
        "Codex2",
        "Evolution Journal Producer Gap / Wave 0 review wiring",
        [],
        [
            "console actions on a proposal call the existing evolution service endpoints through BFF commands",
            "action outcomes project back onto the same formal journal entry as status transitions",
            "unauthorized or un-MFA identities are rejected by existing gates unchanged",
            "command audit records reference the evolution decision id",
        ],
        [
            "services/control-plane/bff/main.py",
            "services/evolution/main.py",
            "docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/EVOCHAIN-006-review-wiring.md",
        ],
        {"wave": 0, "fleet_lane": "be-mutation-review-wiring"},
    ),
    (
        "EVOCHAIN-007",
        "Server-side journal filtering + seed origin marker",
        "在 /bff/management/evolution-journal 實作 server-side persona / mutation_review / decision 過濾與正確分頁（目前帶 ?persona= 回傳不變，全靠 FE 過濾）。同時對來自已註冊 seed（evo-vslice-1 等）的 entries 標記 origin: seed。",
        "Codex2",
        "Codex",
        "Evolution Journal Producer Gap / Wave 0 journal read API",
        [],
        [
            "persona filter returns only entries targeting that persona or its artifacts",
            "mutation_review and decision filters return the exact formal entry",
            "page_info totals reflect filtered counts",
            "seed-derived entries carry origin seed in the payload",
            "contract tests cover filtered empty filtered hit and seed marking",
        ],
        [
            "services/control-plane/bff/main.py",
            "services/control-plane/bff/tests",
            "docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/EVOCHAIN-007-journal-read-api.md",
        ],
        {"wave": 0, "fleet_lane": "be-journal-read-api"},
    ),
    (
        "EVOCHAIN-008",
        "FE data-source badge semantics (live-degraded vs snapshot)",
        "修正 execute-plans 管理台的資料來源徽章語意：degraded 且 source 為 live 組合（bff_composed/service_client）時顯示「LIVE（部分降級）」並可看到是哪些 surface 降級；「SNAPSHOT DATA」只保留給真的由快照供資料的情況。跨 repo task：repo 是 execute-plans。",
        "Claude",
        "Codex",
        "Evolution Journal Producer Gap / Wave 0 FE badge",
        [],
        [
            "degraded live-composed responses render a live-degraded badge naming the degraded surfaces",
            "SNAPSHOT DATA appears only when data is actually served from a snapshot source",
            "zh-TW and en-US locales updated consistently",
            "npm run audit:render passes and hosted evidence shows the new badge on the Evolution Journal page",
        ],
        [
            "execute-plans:src/platform/components/TopBar.tsx",
            "execute-plans:src/i18n/locales/zh-TW.ts",
            "execute-plans:src/i18n/locales/en-US.ts",
            "docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/EVOCHAIN-008-fe-badge-semantics.md",
        ],
        {"wave": 0, "fleet_lane": "fe-data-source-badge", "repo": "execute-plans"},
    ),
    (
        "EVOCHAIN-009",
        "FE journal formal-entry fields + fixture badge",
        "演化日誌卡片渲染 formal entry 的完整欄位（risk_level、action_type、target version、approval 狀態），並對 origin: seed 的 entries 顯示 fixture 徽章。2026-07-10 的 fallback 卡契約（persona-fleet-summary 卡）保持不變。跨 repo task：repo 是 execute-plans。",
        "Claude",
        "Codex2",
        "Evolution Journal Producer Gap / Wave 0 FE journal cards",
        [],
        [
            "formal entries render risk_level action_type target version and approval state without NaN or raw i18n keys",
            "seed-derived entries show a fixture badge",
            "fallback persona-fleet-summary card behavior is unchanged",
            "hosted evidence covers formal seed and fallback card states",
        ],
        [
            "execute-plans:src/management/pages/oversight/_core.tsx",
            "execute-plans:src/i18n/locales/zh-TW.ts",
            "execute-plans:src/i18n/locales/en-US.ts",
            "docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/EVOCHAIN-009-fe-journal-cards.md",
        ],
        {"wave": 0, "fleet_lane": "fe-journal-cards", "repo": "execute-plans"},
    ),
    (
        "EVOCHAIN-010",
        "Producer-chain live verifier",
        "寫 producer-chain live 驗證（test the verb）：對 dev 注入或觸發一筆真 threshold breach，斷言 incident（含 dedupe key）出現、sweep 產出 proposal、journal 出現 formal entry、Persona Fleet 最近 MUTATION 連到該 entry。納入 scripts/run_e2e_verifiers.sh。驗證失敗要能分辨斷在哪一段。",
        "Codex",
        "Claude",
        "Evolution Journal Producer Gap / Wave 2 verification",
        ["EVOCHAIN-001", "EVOCHAIN-002"],
        [
            "verifier drives breach to incident to proposal to formal journal entry on live dev",
            "each chain segment failure is reported distinctly",
            "verifier is idempotent and safe to re-run without polluting incidents",
            "wired into scripts/run_e2e_verifiers.sh",
        ],
        [
            "scripts/run_e2e_verifiers.sh",
            "scripts",
            "docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/EVOCHAIN-010-producer-chain-verifier.md",
        ],
        {"wave": 2, "fleet_lane": "verify-producer-chain"},
    ),
    (
        "EVOCHAIN-011",
        "Dev deploy + packet closeout",
        "部署整包到 dev（compose 更新、sweep scheduler 啟用、BFF/governance 重佈），live curl 驗證 freeze_orders/rollbacks/journal aggregate surface 全 ok、SNAPSHOT DATA 徽章消失，hosted 截圖歸檔，彙整所有 PR 與 residual risks。deploy 未經 live 驗證不得宣告完成（babysit rule）。",
        "Codex2",
        "Human/Ops",
        "Evolution Journal Producer Gap / Wave 3 closeout",
        [
            "EVOCHAIN-003",
            "EVOCHAIN-005",
            "EVOCHAIN-006",
            "EVOCHAIN-007",
            "EVOCHAIN-008",
            "EVOCHAIN-009",
            "EVOCHAIN-010",
        ],
        [
            "dev redeployed with all merged packet PRs and sweep scheduler running",
            "live curl shows freeze_orders rollbacks and journal aggregate surfaces ok",
            "hosted screenshots show formal entries and no SNAPSHOT DATA badge on the journal page",
            "closeout lists every PR merge SHA and residual risk with owner and expiry",
        ],
        [
            "docs/04/pantheon_evolution_journal_producer_gap_2026-07-13/archive",
            "docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/EVOCHAIN-011-closeout.md",
        ],
        {"wave": 3, "fleet_lane": "ops-evochain-closeout"},
    ),
]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_state() -> dict:
    if not STATUS_PATH.exists():
        raise FileNotFoundError(f"status file not found: {STATUS_PATH}")
    return json.loads(STATUS_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    assert_isolated_legacy_write_target(STATUS_PATH, tool=AUTO_BY)
    STATUS_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_log(entry: dict) -> None:
    assert_isolated_legacy_write_target(LOG_PATH, tool=AUTO_BY)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        assert_isolated_legacy_write_target(LOG_PATH, tool=AUTO_BY)
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
        "next": NEXT_BY_TASK.get(task_id, "Assignment created from Evolution Journal producer gap packet"),
        "last_update": timestamp,
        "task_class": "execution",
        "auto_created_by": AUTO_BY,
        "auto_generated": True,
        "source_ref": SOURCE_REF,
        "delivery_layer": "primary",
        "mutates_canonical": True,
        "helper_kind": "evolution_journal_producer_gap_execution_slice",
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
                    "agent": os.environ.get("AI_NAME", "Codex"),
                    "type": "assign",
                    "task_id": task_id,
                    "message": f"Assigned {task_id} to {owner} with reviewer {reviewer}",
                }
            )
        print(
            f"{'CREATE' if inserted else 'UPSERT'} {task_id:14} "
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

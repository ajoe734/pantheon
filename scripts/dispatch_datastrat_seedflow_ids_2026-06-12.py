#!/usr/bin/env python3
"""Dispatch EPIC DATASTRAT-SEEDFLOW (seed pipeline close-out) and
EPIC DATASTRAT-IDS (interaction-derived strategy seeds) into the live sprint.

Plan source (merged to dev in PR #1329):
  docs/04/pantheon_data_strategy_source_design_2026-06-09/
    - GAP_ADDENDUM_2026-06-12.md
    - DISPATCH_SEED_PIPELINE_CLOSEOUT.md
    - EPIC_INTERACTION_DERIVED_SEEDS.md

Calls scripts/ai_status.py assign for each task. Creates NO code; only
registers todo tasks with owner / reviewer / phase / depends_on / acceptance /
artifacts so the supervisor can auto-dispatch them.

Dependency DAG enforces the safety-first rule from the addendum: the
redaction / intent / negative-memory guard (IDS-002/003/007) must land before
any Trainer/Agora->seed bridge (IDS-004/005).
"""
from __future__ import annotations

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DISPATCHER = "dispatch_datastrat_seedflow_ids_2026-06-12"

DESIGN_DIR = "docs/04/pantheon_data_strategy_source_design_2026-06-09"

# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts)
TASKS = [
    # ---- EPIC DATASTRAT-SEEDFLOW: make the delivered seed backbone usable ----
    (
        "DATASTRAT-SEEDFLOW-001",
        "Seed-to-Replication bridge",
        "把 promoted StrategySpecSeed 接到 research replication / ExperimentTask 提交，"
        "回寫 replication_ref 到 seed lineage；不得建立 execution route 或 approved artifact。",
        "Codex",
        "Claude",
        "EPIC DATASTRAT-SEEDFLOW / Replication bridge",
        "",
        "promoted seed 可提交並取得真實 replication/ExperimentTask ref;"
        "draft/rejected seed 被 typed error 擋下;"
        "bridge 產物 registry_write_performed=false 且 execution_route=none(有測試);"
        "重複提交同一 seed 為 idempotent",
        f"{DESIGN_DIR}/DISPATCH_SEED_PIPELINE_CLOSEOUT.md,"
        "services/source_ingestion/strategy_seed_store.py,"
        "services/research/strategy_spec/conversion.py",
    ),
    (
        "DATASTRAT-SEEDFLOW-002",
        "Strategy Seed Review Inbox (read model + actions)",
        "為 StrategySpecSeed 加治理審查面:accept/reject/merge/request-evidence/"
        "convert-to-spec-seed/submit-replication;BFF read model(seed inbox)+command endpoints;"
        "把 persona promote_seed_candidate 顯示為建議而非自動晉升。",
        "Claude",
        "Codex",
        "EPIC DATASTRAT-SEEDFLOW / Seed review inbox",
        "DATASTRAT-SEEDFLOW-001",
        "inbox 可依 status/source_kind/strategy_family 篩選並回傳 seed card;"
        "每個 review action 依狀態機轉移並寫 SeedReviewDecision audit;"
        "accept->convert 後可被 SEEDFLOW-001 提交;reject/archive 為終態;"
        "read-role 可讀不可執行;persona 建議僅為 suggestion 不自動晉升",
        f"{DESIGN_DIR}/DISPATCH_SEED_PIPELINE_CLOSEOUT.md,"
        "services/source_ingestion/strategy_seed_store.py,"
        "services/control-plane/bff/main.py",
    ),
    # ---- EPIC DATASTRAT-IDS: interaction-derived strategy seeds ----
    (
        "DATASTRAT-IDS-001",
        "InteractionSourceRecord schema + store",
        "新增 interaction_source_record contract + JSONL dev store(沿用 registry-split 模式);"
        "raw 內容只存 raw_ref 不 inline;visibility 與 redaction_status 必填。",
        "Codex2",
        "Claude2",
        "EPIC DATASTRAT-IDS / InteractionSourceRecord",
        "",
        "可從任一 surface 建立 record;raw_ref 指向 evidence 不內嵌原文;"
        "visibility(private/persona/desk/shared)與 redaction_status 必填",
        f"{DESIGN_DIR}/EPIC_INTERACTION_DERIVED_SEEDS.md,"
        "docs/contracts,services/source_ingestion",
    ),
    (
        "DATASTRAT-IDS-002",
        "Redaction / visibility / scope guard (safety)",
        "在 InteractionSourceRecord 邊界做 redaction:依 scope(tenant/user/persona)去除"
        "PII/憑證/資金額度/broker ref/私人筆記,設定 redaction_status;失敗即擋 SeedCandidate。",
        "Claude2",
        "Codex2",
        "EPIC DATASTRAT-IDS / Redaction guard",
        "DATASTRAT-IDS-001",
        "redaction_status=failed 擋下所有下游 SeedCandidate(typed refusal+測試);"
        "private/persona visibility 不外洩到 shared;raw prompt 永不落 seed store",
        f"{DESIGN_DIR}/EPIC_INTERACTION_DERIVED_SEEDS.md,services/source_ingestion",
    ),
    (
        "DATASTRAT-IDS-003",
        "Intent classification (safety)",
        "deterministic-first 分類器把互動歸到 primary_intent(strategy_hypothesis/risk_overlay/"
        "execution_policy/persona_policy/negative_memory/non_strategy 等)含 confidence 與 requires_human_review。",
        "Codex",
        "Claude",
        "EPIC DATASTRAT-IDS / Intent classification",
        "DATASTRAT-IDS-001",
        "style/coaching->persona_policy 非 strategy;investable hypothesis->strategy_hypothesis;"
        "低信心->needs_review;non_strategy->archive only;測試表對齊 SA test cases",
        f"{DESIGN_DIR}/EPIC_INTERACTION_DERIVED_SEEDS.md,services/source_ingestion",
    ),
    (
        "DATASTRAT-IDS-007",
        "Negative-memory matcher (safety)",
        "把 SeedCandidate 對 retired/rejected/failed/postmortem 做相似度比對,輸出 negative_memory_match"
        "(warning_level info|warning|blocking);blocking 擋 seed acceptance。v1 deterministic/keyword 即可。",
        "Codex2",
        "Claude2",
        "EPIC DATASTRAT-IDS / Negative-memory matcher",
        "DATASTRAT-IDS-001",
        "blocking match 阻止 seed acceptance;warning 顯示在 seed card;"
        "v1 deterministic/keyword 比對可接受(embedding 後續)",
        f"{DESIGN_DIR}/EPIC_INTERACTION_DERIVED_SEEDS.md,services/source_ingestion",
    ),
    (
        "DATASTRAT-IDS-004",
        "Trainer-to-seed bridge",
        "消費 committed Trainer event(trainer_commit/explicit_seed_submission,絕不收 raw)->"
        "InteractionSourceRecord->classify->redact->SeedCandidate;支援多種 seed_kind;記 TrainerSeedExtractionRef。",
        "Claude",
        "Codex",
        "EPIC DATASTRAT-IDS / Trainer bridge",
        "DATASTRAT-IDS-002,DATASTRAT-IDS-003,DATASTRAT-IDS-007",
        "只有 committed 內容進入;raw teaching log 被拒;產出的 SeedCandidate 進 review inbox;"
        "TrainerSeedExtractionRef lineage 記錄",
        f"{DESIGN_DIR}/EPIC_INTERACTION_DERIVED_SEEDS.md,services/source_ingestion,services/control-plane",
    ),
    (
        "DATASTRAT-IDS-005",
        "Agora/Committee/Postmortem-to-seed bridge",
        "ConsultMemo/CommitteeVerdict/RedTeamMemo/SeedProposal->SeedCandidate(raw transcript 僅 evidence);"
        "Postmortem/Evolution->Risk/Execution/Negative seed;記 AgoraSeedExtractionRef。",
        "Codex",
        "Claude2",
        "EPIC DATASTRAT-IDS / Agora bridge",
        "DATASTRAT-IDS-002,DATASTRAT-IDS-003,DATASTRAT-IDS-007",
        "raw transcript 不得進 seed queue;只有 memo/verdict/proposal 可;"
        "AgoraSeedExtractionRef lineage 記錄;依來源 trust 區分處理",
        f"{DESIGN_DIR}/EPIC_INTERACTION_DERIVED_SEEDS.md,services/source_ingestion,services/control-plane",
    ),
    (
        "DATASTRAT-IDS-006",
        "Seed-kind taxonomy + inbox integration",
        "把 Seed Review Inbox 的 seed card 擴充 seed_kind/source surface/negative-memory 警示與"
        "convert-to-risk/convert-to-negative actions;讓非新策略互動以自身 kind 被審查。",
        "Claude2",
        "Codex2",
        "EPIC DATASTRAT-IDS / Seed-kind taxonomy",
        "DATASTRAT-IDS-004,DATASTRAT-SEEDFLOW-002",
        "risk constraint 等非新策略互動可以自身 seed_kind 被審查而非被塞成 new strategy;"
        "negative-memory 警示顯示於 seed card",
        f"{DESIGN_DIR}/EPIC_INTERACTION_DERIVED_SEEDS.md,services/control-plane/bff/main.py",
    ),
    (
        "DATASTRAT-IDS-008",
        "Audit + lineage + end-to-end tests",
        "每一步(record/redact/classify/negative-match/candidate/accept/reject)發 audit event;"
        "由 Trainer commit 與 committee memo 各跑一條端到端到可審查 SeedCandidate;隱私測試確認 raw prompt 不外洩。",
        "Codex2",
        "Claude",
        "EPIC DATASTRAT-IDS / Audit and e2e",
        "DATASTRAT-IDS-004,DATASTRAT-IDS-005,DATASTRAT-IDS-006",
        "seed 可完整 replay/lineage 回溯到 source event;隱私測試斷言 raw prompt 永不出現;"
        "Trainer 與 committee memo 各有端到端測試",
        f"{DESIGN_DIR}/EPIC_INTERACTION_DERIVED_SEEDS.md,services/source_ingestion,services/control-plane",
    ),
]


def dispatch_one(task) -> bool:
    (task_id, title, summary_zh, owner, reviewer, phase,
     depends_on, acceptance, artifacts) = task
    env = os.environ.copy()
    env["TASK_SUMMARY_ZH"] = summary_zh
    env["TASK_PHASE"] = phase
    env["TASK_DEPENDS_ON"] = depends_on
    env["TASK_ACCEPTANCE"] = acceptance
    env["TASK_ARTIFACTS"] = artifacts
    env["TASK_AUTO_CREATED_BY"] = DISPATCHER
    cmd = [sys.executable, "scripts/ai_status.py", "assign", task_id, owner, reviewer, title]
    result = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAIL {task_id}: {result.stderr.strip() or result.stdout.strip()}", file=sys.stderr)
        return False
    dep = depends_on or "-"
    print(f"OK   {task_id}  owner={owner}  reviewer={reviewer}  depends_on={dep}")
    return True


def main() -> int:
    ids = [t[0] for t in TASKS]
    if len(ids) != len(set(ids)):
        print("Duplicate task IDs in dispatch list", file=sys.stderr)
        return 2
    ok = True
    for task in TASKS:
        ok = dispatch_one(task) and ok
    print("Done. EPIC DATASTRAT-SEEDFLOW + DATASTRAT-IDS dispatched." if ok
          else "Completed with failures.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

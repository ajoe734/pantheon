#!/usr/bin/env python3
"""Dispatch Agora cross-repo Phase 0 + Phase 1 foundation tasks.

Spec: docs/04/pantheon_agora_cross_repo_2026-06-20/ (SA / SD / INDEX + README + DISPATCH)

Phase 0 (Cross-repo Foundation) + Phase 1 (Private Servant & Identity) only.
These are the unblocking critical path; everything in Phase 2-7 depends on the
identity / contract / StrategySpec foundation laid here, so later phases are held
in DISPATCH_PHASE0_PHASE1_2026-06-20.md and dispatched as deps clear.

Repo routing is artifact-prefix based (.orchestrator/multi_repo_registry.py):
artifacts starting with "execute-plans/" route to the execute_plans repo; all
other paths default to pantheon.

Owner policy: Claude -> Claude2 -> Codex. Never Codex2 / Antigravity (disabled).
"""
from __future__ import annotations

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTO_BY = "dispatch_agora_cross_repo_2026-06-20"

# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts)
TASKS = [
    # ---------------- Phase 0 — Cross-repo Foundation ----------------
    (
        "AG-XR-001",
        "Agora v1 contract manifest / OpenAPI / schema bundle",
        "依 SD §22.1 在 pantheon 建立 Agora v1 合約基礎:services/control-plane/specs/agora/ 下的 13 個 JSON schema"
        "(agora_user_scope / servant_profile / strategy_workshop / strategy_completeness / research_plan / "
        "research_run_summary / candidate_pool / dashboard_recipe / widget_spec / trading_event / trading_intent / "
        "shadow_decision / personalization_event),更新 OpenAPI 與 BFF capability manifest(agora.identity.v1 等 7 個 capability)。"
        "這是所有 Agora 任務的依賴根,先把 schema 與 capability 名稱凍結。execute-plans 端的 type 生成由 AG-XR-002 接。",
        "Claude", "Claude2", "EPIC AGORA-XR / Phase 0", "",
        "13 個 schema 通過 JSON-schema 驗證;OpenAPI 含 §17 全部 Agora route;capability manifest 列出 agora.*.v1 7 項;schema bundle sha256 可重現",
        "services/control-plane/specs/agora/,services/control-plane/openapi,docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md",
    ),
    (
        "AG-BE-000",
        "Agora BFF router package and capability manifest",
        "依 SD §22.2 在既有 BFF 新增 services/control-plane/bff/agora/ package(router.py/models.py + identity/servant/"
        "strategy_workshop/research/trading_room/dashboard/shadow/personalization/management_projection 骨架),以 package "
        "router 機制掛載,禁止把 Agora endpoint 塞進單一 main.py。先建 router 骨架 + envelope(§18)+ typed error,實際 handler 由後續 ID/SW 任務填。",
        "Claude2", "Claude", "EPIC AGORA-XR / Phase 0", "AG-XR-001",
        "Agora router 以 package 機制掛上既有 BFF;/bff/agora/me 回 §18 envelope;capability 由 audience 限制;不破壞既有 BFF route;單元測試通過",
        "services/control-plane/bff/agora/router.py,services/control-plane/bff/agora/models.py",
    ),
    (
        "AG-FE-000",
        "Separate Agora/Management entry, build, auth audience",
        "依 SD §3.1/§23.1 在 execute-plans 把 Agora 與 Management 拆成兩個獨立 app entry/build:新增 agora-main.tsx/"
        "management-main.tsx、agora.html/management.html、vite.agora.config.ts/vite.management.config.ts 與 dev/build/test/gate "
        "npm scripts;設定 VITE_APP_KIND 與 VITE_AUTH_AUDIENCE。Agora production bundle 不得含 /management route code。先做 Phase 1 不搬目錄,Phase 2 monorepo 另議。",
        "Claude", "Codex", "EPIC AGORA-FE / Phase 0", "AG-XR-001",
        "build:agora 與 build:management 各自產出;Agora bundle 經檢查不含 management routes;兩 app auth audience 分離;既有 Management 行為不變",
        "execute-plans/src/entries/agora-main.tsx,execute-plans/vite.agora.config.ts,execute-plans/agora.html,execute-plans/package.json",
    ),
    (
        "AG-XR-002",
        "Cross-repo generated types and drift CI",
        "依 SD §24/§23.5 從 AG-XR-001 的 OpenAPI/schema bundle 在 execute-plans 生成 src/lib/bff-v1/agora/types.ts,"
        "並加一個 contract-drift CI check:當 pantheon OpenAPI/schema sha256 與 execute-plans 生成快照不一致時 CI 紅。確保跨 repo 以 contract version 對齊。",
        "Codex", "Claude", "EPIC AGORA-XR / Phase 0", "AG-XR-001",
        "types.ts 由 schema 自動生成且可重生;drift check 在 schema 改動但未重生時失敗;CI job 綠燈於一致狀態",
        "execute-plans/src/lib/bff-v1/agora/types.ts,execute-plans/scripts/contract-drift-check.mjs",
    ),
    (
        "AG-XR-003",
        "Dev deployment compatibility manifest",
        "依 SD §2.3 在兩 repo 落地 compatibility-manifest.yaml(contract_family=agora.v1、frontend_commit/backend_commit、"
        "required_bff_capabilities、openapi_sha256、schema_bundle_sha256),並加一支可在 dev 部署前比對兩端 checksum 一致的腳本。",
        "Codex", "Claude2", "EPIC AGORA-XR / Phase 0", "AG-XR-002",
        "兩 repo 各有 compatibility-manifest.yaml;checksum 比對腳本在不一致時非零退出;dev 部署文件引用此 gate",
        "docs/contracts/agora/compatibility-manifest.yaml,scripts/agora_compat_manifest.py",
    ),
    # ---------------- Phase 1 — Private Servant & Identity ----------------
    (
        "AG-BE-ID-001",
        "Agora user scope + servant persona metadata/policy",
        "依 SD §5.1/§21.2 落地 Agora user scope 與 agora_servant persona metadata/policy:persona_class=agora_servant、"
        "owner_scope=user、visibility_scope=user_private、memory_scope=self_only、capital_authority=none;所有 Agora read model 強制 "
        "(tenant_id,user_id) mandatory predicate(fail-closed),不能只靠前端 filter。實作 identity.py + agora_user_scope/servant_profile schema。",
        "Claude", "Claude2", "EPIC AGORA-ID / Phase 1", "AG-BE-000",
        "agora_servant persona 以既有 Persona Registry 建立(不新建 agent service);跨 user 查詢 fail-closed;capability 不含 runtime_binding/broker/capital;附 scope 單元測試",
        "services/control-plane/bff/agora/identity.py,services/control-plane/specs/agora/agora_user_scope.schema.json,services/control-plane/specs/agora/servant_profile.schema.json",
    ),
    (
        "AG-BE-ID-002",
        "OpenClaw ensure/provision/reconcile servant",
        "依 SD §5.2 實作 POST /bff/agora/servant/ensure 流程:解析 user-private persona,缺則建立 agora_servant registry object "
        "並透過既有 OpenClaw adapter 建/更新 agent(private workspace + §5.4 capability set);存在則 reconcile status/capabilities。"
        "回 ServantProfileDTO。沿用既有 OpenClaw lifecycle 與 deny-by-default policy,不新建 runtime。",
        "Claude2", "Claude", "EPIC AGORA-ID / Phase 1", "AG-BE-ID-001",
        "ensure 對新 user 建立私有 servant、對既有 user reconcile;effective capability 與 §5.4 allow/deny 一致;永久拒絕清單被 enforce;附 provisioning 測試",
        "integrations/openclaw/adapter/agora_servant.py,services/control-plane/bff/agora/servant.py",
    ),
    (
        "AG-BE-ID-003",
        "Interactive/trainer/research session BFF facade",
        "依 SD §5.3/§17.1 實作 servant session BFF facade:POST sessions(interactive/trainer/research_task)、GET session、"
        "POST messages、terminate、GET stream(SSE)。session type 映射到既有 OpenClaw session,所有 read/write 帶 §8.2 audit 欄位"
        "(trace_id/request_id/actor_id/user_id/persona_id/session_id)。",
        "Claude", "Codex", "EPIC AGORA-ID / Phase 1", "AG-BE-ID-002",
        "可建立 interactive/trainer/research session 並收發訊息;stream 以 SSE 回傳;每筆操作有 §8.2 audit 欄位;OpenClaw 降級時回 OPENCLAW_UPSTREAM_DEGRADED",
        "services/control-plane/bff/agora/servant.py,services/control-plane/specs/agora/strategy_workshop.schema.json",
    ),
    (
        "AG-BE-ID-004",
        "ContextBundle redaction and central persona boundary",
        "依 SD §5.6/§21.3 實作中央 Persona consult 的 ContextBundle:只帶 strategy_spec_draft_ref/question/symbols/evidence_refs/"
        "data_cutoff/required_output_schema,raw_prompt_included=false、user_identity_included=false(除非該次 consult 使用者明確授權)。"
        "中央人格永遠拿不到原始私人對話與身份。違反時回 RAW_PRIVATE_CONTENT_FORBIDDEN。",
        "Claude2", "Claude", "EPIC AGORA-ID / Phase 1", "AG-BE-ID-002",
        "ContextBundle 預設不含 raw prompt 與身份;測試斷言中央 consult 拿不到私人原文;授權旗標需明確開啟才帶文;redaction 失敗 fail-closed",
        "integrations/openclaw/adapter/agora_context_bundle.py,services/control-plane/bff/agora/management_projection.py",
    ),
    (
        "AG-FE-ID-001",
        "Agora auth/session/servant status shell",
        "依 SD §23 在 execute-plans 建立 Agora app shell:Agora-scoped auth(audience=pantheon-agora)、servant ensure/status、"
        "BFF client identity.ts/servant.ts(禁止頁面直接 fetch、strict no-fallback)。AgoraApp.tsx 顯示 servant 狀態與命令列骨架,"
        "不揭露 Management/資金池/RuntimeBinding。",
        "Claude", "Codex", "EPIC AGORA-FE / Phase 1", "AG-FE-000,AG-BE-ID-003",
        "登入後 ensure servant 並顯示狀態;BFF client 走 live strict;Agora bundle 不引用 management;跨 audience token 被拒;附前端測試",
        "execute-plans/src/agora/AgoraApp.tsx,execute-plans/src/lib/bff-v1/agora/identity.ts,execute-plans/src/lib/bff-v1/agora/servant.ts",
    ),
    (
        "AG-TEST-ID-001",
        "Cross-user and Management route isolation E2E",
        "依 SD §24.3/§26 寫跨 repo 隔離 E2E:(1) user A 不能讀 user B 的 servant/strategy/journal(CROSS_USER_ACCESS_FORBIDDEN);"
        "(2) Agora token 不能打 /bff/management/*(AGORA_SCOPE_VIOLATION);(3) Management projection 只回 redacted,拿不到 raw prompt。"
        "作為 Phase 1 的驗收門,Phase 2 之前必綠。",
        "Codex", "Claude2", "EPIC AGORA-ID / Phase 1", "AG-BE-ID-004,AG-FE-ID-001",
        "三條隔離 E2E 全通過;raw prompt 與跨 user 存取在測試中證明不可能;CI 收錄此 gate",
        "services/control-plane/tests/agora/test_cross_user_isolation.py",
    ),
]


def run(cmd, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)


def main() -> int:
    ok = True
    for task_id, title, summary, owner, reviewer, phase, deps, acc, arts in TASKS:
        env_extra = {
            "TASK_SUMMARY_ZH": summary, "TASK_PHASE": phase, "TASK_DEPENDS_ON": deps,
            "TASK_ACCEPTANCE": acc, "TASK_ARTIFACTS": arts, "TASK_AUTO_CREATED_BY": AUTO_BY,
        }
        r = run([sys.executable, "scripts/ai_status.py", "assign", task_id, owner, reviewer, title],
                env_extra=env_extra)
        if r.returncode != 0:
            print(f"ASSIGN FAIL {task_id}: {r.stderr.strip() or r.stdout.strip()}", file=sys.stderr)
            ok = False
        else:
            print(f"ASSIGN  {task_id:16} owner={owner:8} reviewer={reviewer:8} deps={deps or '-'}")
    print("Done." if ok else "Completed with failures.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

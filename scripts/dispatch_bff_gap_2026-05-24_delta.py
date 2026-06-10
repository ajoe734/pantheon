#!/usr/bin/env python3
"""One-shot dispatcher for the 2026-05-24 BFF API GAP delta audit tasks.

Source audit: execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md
Spec doc:    docs/04/pantheon_bff_api_gap_2026-05-24_delta/BFF_API_GAP_delta_audit_spec.md

Emits 22 `python3 scripts/ai_status.py assign` calls with
TASK_PHASE / TASK_DEPENDS_ON / TASK_ACCEPTANCE / TASK_ARTIFACTS env per task.

Also updates ai-status.json `sprint` to `2026-05-24-pantheon-bff-p0-delta`
before dispatching, recording the rebaseline objective.

Three classes:
  Class A (deploy lag, 1 task): Gemini2 / Claude  - EPIC-BFF-DELTA-INFRA
  Class B PM-Live (12 tasks):   Codex   / Claude  - EPIC-BFF-DELTA-MGMT-LIVE
  Class B PM12-sub (7 tasks):   Codex2  / Claude2 - EPIC-BFF-DELTA-PM12-SUB
  Class C infra (2 tasks):      Claude/Codex      - EPIC-BFF-DELTA-INFRA
                                Codex/Claude
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "ai-status.json"

SPEC = "docs/04/pantheon_bff_api_gap_2026-05-24_delta/BFF_API_GAP_delta_audit_spec.md"
AUDIT = "execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md"
BE_BFF = "services/control-plane/bff/main.py"
FE_MGMT = "execute-plans/src/lib/bff-v1/management.ts"

PHASE_INFRA = "Sprint BFF-DELTA / EPIC-BFF-DELTA-INFRA"
PHASE_MGMT = "Sprint BFF-DELTA / EPIC-BFF-DELTA-MGMT-LIVE"
PHASE_PM12 = "Sprint BFF-DELTA / EPIC-BFF-DELTA-PM12-SUB"

SPRINT_ID = "2026-05-24-pantheon-bff-p0-delta"
SPRINT_OBJECTIVE = (
    "Pantheon BFF P0 Delta — closing 28 gaps from Lovable 2026-05-24 live probe "
    "against lupin dev BFF. Three classes: (A) 7 routes already in master code but "
    "404 on live (root cause = deploy lag, single OPS task `OPS-BFF-LUPIN-DEV-REDEPLOY-20260524` "
    "re-deploys image + curls all 7); (B) 19 routes truly missing — 12 PM-Live §8 endpoints "
    "(persona-league/movers, heatmap, strategy-allocation, capital-flow, risk-radar, "
    "incident-timeline, governance-ledger, cost-attribution, sentinel-pulse, loop-throughput, "
    "hiq-backlog, intervention-stream) under EPIC-BFF-DELTA-MGMT-LIVE plus 7 PM-12 sub-paths "
    "(quarterly-ranking/drilldown, performance-attribution/by-{persona,strategy,pool}, "
    "portfolio-book/{positions,exposure}, board-pack) under EPIC-BFF-DELTA-PM12-SUB; "
    "(C) 2 infra fixes — CORS preflight regression (`BFF-B1-001-DELTA`, B1-001 already done "
    "but live OPTIONS still 400) and error envelope shape deviation (`BFF-INFRA-ENVELOPE-001`, "
    "backend returns `{detail:{error:...}}` instead of canonical `{error:..., meta:{correlationId}}`). "
    "Baseline spec: docs/04/pantheon_bff_api_gap_2026-05-23/. Delta spec: "
    "docs/04/pantheon_bff_api_gap_2026-05-24_delta/BFF_API_GAP_delta_audit_spec.md. "
    "fail-closed rules unchanged; production live broker / capital binding still gated."
)

# (task_id, title, owner, reviewer, phase, depends_on, acceptance, artifacts)
TASKS = [
    # ---------- Class A — single redeploy task ----------
    (
        "OPS-BFF-LUPIN-DEV-REDEPLOY-20260524",
        "Re-deploy lupin dev BFF and verify 7 already-coded delta routes go live",
        "Gemini2", "Claude", PHASE_INFRA,
        "BFF-B1-001-DELTA,BFF-INFRA-ENVELOPE-001",
        "Rebuild lupin dev BFF image from pantheon@master HEAD;"
        "Push image and roll out service; new pod ready;"
        "CORS preflight from 4 Lovable origins returns 204 with full ACAO ACAH ACAM ACEH;"
        "7 path live curl with Authorization Bearer pantheon-dev-browser:reviewer all return 200 (POST /bff/approvals/batch-decide; GET /bff/command-confirmations/{token}; GET /bff/management/cockpit; GET /bff/management/persona-league/rankings; GET /bff/management/quarterly-ranking?quarter=2026-Q2; GET /bff/management/performance-attribution; GET /bff/management/portfolio-book);"
        "Evidence written to support/evidence/bff-delta-20260524/redeploy-curl-results.md and committed",
        f"{SPEC};{AUDIT};{BE_BFF};support/evidence/bff-delta-20260524/redeploy-curl-results.md",
    ),

    # ---------- Class C — infra fixes ----------
    (
        "BFF-B1-001-DELTA",
        "CORS preflight regression — live OPTIONS still 400 despite B1-001 done",
        "Claude", "Codex", PHASE_INFRA,
        "",
        "OPTIONS preflight from 4 Lovable origins to /bff/me /bff/openapi.json /health and any /bff/management/* returns 204;"
        "ACAO echoes origin from allowed list (preview UUID lovableproject pantheon-dev);"
        "ACAH includes Authorization X-Correlation-Id X-Request-Id X-Idempotency-Key X-Confirm-Token X-MFA-Token Accept Accept-Language Content-Type;"
        "ACAM includes GET POST PATCH PUT DELETE OPTIONS; ACEH includes X-Correlation-Id X-Request-Id X-BFF-Api-Version;"
        "Root cause documented in commit message (middleware order vs ingress intercept vs 404-before-CORS);"
        "Co-verified with OPS-BFF-LUPIN-DEV-REDEPLOY-20260524 after redeploy",
        f"{SPEC};{AUDIT};{BE_BFF}",
    ),
    (
        "BFF-INFRA-ENVELOPE-001",
        "Error envelope shape — strip detail wrapper, add meta.correlationId per Pack D",
        "Codex", "Claude", PHASE_INFRA,
        "",
        # NOTE: parse_csv_env splits on ',' — avoid commas inside acceptance text.
        "401 404 422 500 responses use shape error.code plus error.message plus meta.correlationId per Pack D (no outer detail wrapper);"
        "Outer detail wrapper removed across HTTPException RequestValidationError ValueError and generic 500 handlers;"
        "meta.correlationId echoes X-Correlation-Id request header or generates UUIDv4 when absent;"
        "Response X-Correlation-Id header set to same value;"
        "Success envelope data plus meta unchanged;"
        "Contract test services/control-plane/bff/test_bff_error_envelope_shape.py covers 4 status cases",
        f"{SPEC};{AUDIT};{BE_BFF};services/control-plane/bff/test_bff_error_envelope_shape.py",
    ),

    # ---------- Class B §8 PM-Live ----------
    (
        "BFF-MGMT-DELTA-001",
        "GET /bff/management/persona-league/movers",
        "Codex", "Claude", PHASE_MGMT,
        "",
        # NOTE: parse_csv_env splits on ',' — avoid commas inside acceptance text.
        "Path registered in services/control-plane/bff/main.py;"
        "Top N persona ranked by ranking delta with promote/demote/idle buckets;"
        "Anonymous 401 authenticated 200;"
        "Response envelope uses canonical Pack D shape (data plus meta.correlationId);"
        "CORS preflight 204;"
        "pytest test_bff_management_delta_routes.py case persona_league_movers",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-MGMT-DELTA-002",
        "GET /bff/management/persona-league/heatmap",
        "Codex", "Claude", PHASE_MGMT,
        "",
        "Path registered; persona x time-bucket 2D grid composite score cells;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case persona_league_heatmap",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-MGMT-DELTA-003",
        "GET /bff/management/strategy-allocation",
        "Codex", "Claude", PHASE_MGMT,
        "",
        "Path registered; active strategy allocation slice across capital pools with drift;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case strategy_allocation",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-MGMT-DELTA-004",
        "GET /bff/management/capital-flow",
        "Codex", "Claude", PHASE_MGMT,
        "",
        "Path registered; deployment in/out flow events stream 24h and 7d windows;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case capital_flow",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-MGMT-DELTA-005",
        "GET /bff/management/risk-radar",
        "Codex", "Claude", PHASE_MGMT,
        "",
        "Path registered; cross persona and strategy risk indicators (drawdown exposure var);"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case risk_radar",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-MGMT-DELTA-006",
        "GET /bff/management/incident-timeline",
        "Codex", "Claude", PHASE_MGMT,
        "",
        "Path registered; incidents chronologically sorted plus severity bucket high/medium/low;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case incident_timeline",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-MGMT-DELTA-007",
        "GET /bff/management/governance-ledger",
        "Codex", "Claude", PHASE_MGMT,
        "",
        "Path registered; unified audit ledger of approvals interventions overrides;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case governance_ledger",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-MGMT-DELTA-008",
        "GET /bff/management/cost-attribution",
        "Codex", "Claude", PHASE_MGMT,
        "",
        "Path registered; cost breakdown LLM data runtime by persona and strategy;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case cost_attribution",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-MGMT-DELTA-009",
        "GET /bff/management/sentinel-pulse",
        "Codex", "Claude", PHASE_MGMT,
        "",
        "Path registered; sentinel digest live heartbeat with findings count and severity histogram;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case sentinel_pulse",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-MGMT-DELTA-010",
        "GET /bff/management/loop-throughput",
        "Codex", "Claude", PHASE_MGMT,
        "",
        "Path registered; v5 loop runs per minute queue depth and lag;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case loop_throughput",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-MGMT-DELTA-011",
        "GET /bff/management/hiq-backlog",
        "Codex", "Claude", PHASE_MGMT,
        "",
        "Path registered; HiQ queue state pending count and SLA breach summary;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case hiq_backlog",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-MGMT-DELTA-012",
        "GET /bff/management/intervention-stream",
        "Codex", "Claude", PHASE_MGMT,
        "",
        "Path registered; intervention events stream per persona last 24h;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case intervention_stream",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),

    # ---------- Class B §9 PM-12 sub-paths ----------
    (
        "BFF-PM12-DELTA-001",
        "GET /bff/management/quarterly-ranking/drilldown",
        "Codex2", "Claude2", PHASE_PM12,
        "",
        "Path registered; single persona contribution breakdown under parent quarterly ranking;"
        "Accepts ?personaId= and ?quarter=YYYY-Qn;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case quarterly_ranking_drilldown",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-PM12-DELTA-002",
        "GET /bff/management/performance-attribution/by-persona",
        "Codex2", "Claude2", PHASE_PM12,
        "",
        "Path registered; attribution grouped by persona dimension;"
        "Accepts ?period= query;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case attribution_by_persona",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-PM12-DELTA-003",
        "GET /bff/management/performance-attribution/by-strategy",
        "Codex2", "Claude2", PHASE_PM12,
        "",
        "Path registered; attribution grouped by strategy dimension;"
        "Accepts ?period= query;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case attribution_by_strategy",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-PM12-DELTA-004",
        "GET /bff/management/performance-attribution/by-pool",
        "Codex2", "Claude2", PHASE_PM12,
        "",
        "Path registered; attribution grouped by capital pool dimension;"
        "Accepts ?period= query;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case attribution_by_pool",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-PM12-DELTA-005",
        "GET /bff/management/portfolio-book/positions",
        "Codex2", "Claude2", PHASE_PM12,
        "",
        "Path registered; portfolio book positions list (symbol qty mark pnl);"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case portfolio_book_positions",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-PM12-DELTA-006",
        "GET /bff/management/portfolio-book/exposure",
        "Codex2", "Claude2", PHASE_PM12,
        "",
        "Path registered; exposure breakdown across asset class sector region;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case portfolio_book_exposure",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
    (
        "BFF-PM12-DELTA-007",
        "GET /bff/management/board-pack",
        "Codex2", "Claude2", PHASE_PM12,
        "",
        "Path registered; composite payload combining cockpit + quarterly-ranking + sentinel-pulse for board review export;"
        "Anonymous 401 authenticated 200; envelope canonical; CORS preflight 204;"
        "pytest case board_pack",
        f"{SPEC};{AUDIT};{BE_BFF};{FE_MGMT}",
    ),
]


def update_sprint_metadata() -> None:
    """Update ai-status.json sprint id + objective before dispatching."""
    state = json.loads(STATE_PATH.read_text())
    state["sprint"] = SPRINT_ID
    state["sprint_started_at"] = "2026-05-24T00:00:00Z"
    state["objective"] = SPRINT_OBJECTIVE
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    print(f"Sprint metadata updated: {SPRINT_ID}")


def dispatch_one(task_id: str, title: str, owner: str, reviewer: str,
                 phase: str, depends_on: str, acceptance: str, artifacts: str) -> None:
    env = os.environ.copy()
    env["AI_NAME"] = "Operator"
    env["TASK_PHASE"] = phase
    env["TASK_DEPENDS_ON"] = depends_on
    env["TASK_ACCEPTANCE"] = acceptance
    env["TASK_ARTIFACTS"] = artifacts
    cmd = [
        sys.executable,
        "scripts/ai_status.py",
        "assign",
        task_id,
        owner,
        reviewer,
        title,
    ]
    result = subprocess.run(cmd, env=env, cwd=str(REPO_ROOT),
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAIL {task_id}: {result.stderr.strip() or result.stdout.strip()}",
              file=sys.stderr)
        sys.exit(result.returncode)
    print(f"OK   {task_id}  owner={owner}  reviewer={reviewer}  phase={phase}")


def main() -> int:
    expected_ids = [t[0] for t in TASKS]
    if len(expected_ids) != len(set(expected_ids)):
        print("Duplicate task IDs in dispatch list", file=sys.stderr)
        return 1
    update_sprint_metadata()
    print(f"Dispatching {len(TASKS)} tasks ...")
    for task in TASKS:
        dispatch_one(*task)
    print(
        f"\nDispatched {len(TASKS)} tasks. "
        "Run `python3 scripts/ai_status.py sync` to refresh derived files."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

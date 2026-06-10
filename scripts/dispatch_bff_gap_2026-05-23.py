#!/usr/bin/env python3
"""One-shot dispatcher for the 2026-05-23 BFF API GAP final integration tasks.

Reads no input; emits 39 `python3 scripts/ai_status.py assign` calls with
TASK_PHASE / TASK_DEPENDS_ON / TASK_ACCEPTANCE / TASK_ARTIFACTS env per task.

Owner / reviewer split:
  P0 (B1-*)       -> Claude   / Codex
  P1 Core (B2-*)  -> Claude2  / Codex2
  P1 Mgmt (B3-*)  -> Codex    / Claude
  PM-12 (PM12-*)  -> Codex2   / Claude2
  P2 (B5/B6-*)    -> Claude   / Codex

Sprint: 2026-05-16-pantheon-bff-p0-foundation (unchanged).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = "docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md"
FE_PATHS = "execute-plans/src/lib/bff-v1/paths.ts"
FE_MGMT = "execute-plans/src/lib/bff-v1/management.ts"
BE_BFF = "services/control-plane/bff/main.py"
BE_OP_CMD = "/api/v1/operator/commands"

PHASE_P0   = "Sprint BFF-1 / EPIC-BFF-GAP-P0"
PHASE_CORE = "Sprint BFF-2 / EPIC-BFF-GAP-CORE"
PHASE_MGMT = "Sprint BFF-3 / EPIC-BFF-GAP-MGMT"
PHASE_PM12 = "Sprint BFF-4 / EPIC-BFF-GAP-PM12"
PHASE_HG   = "Sprint BFF-5 / EPIC-BFF-GAP-HUMANGATE"
PHASE_NL   = "Sprint BFF-6 / EPIC-BFF-GAP-NL"

# title; owner; reviewer; phase; depends_on (csv); acceptance (csv); artifacts (csv)
TASKS = [
    # ---------- P0 / B1 ----------
    ("BFF-B1-001",
     "CORS fix for Lovable preview and published origins",
     "Claude", "Codex", PHASE_P0,
     "",
     "Allowed origins include 4 Lovable hosts per spec section 1.5;"
     "Allowed headers include Authorization X-Correlation-Id X-Request-Id X-Idempotency-Key X-Confirm-Token X-MFA-Token;"
     "Exposed headers include X-Correlation-Id X-Request-Id X-BFF-Api-Version;"
     "OPTIONS preflight returns 204 with full ACAO ACAH ACAM ACEH headers",
     f"{SPEC}#15-cors;{BE_BFF};{FE_PATHS}"),

    ("BFF-B1-002",
     "Fix /openapi.json 500; make Swagger/OpenAPI readable",
     "Claude", "Codex", PHASE_P0,
     "",
     "GET /openapi.json returns 200 with valid OpenAPI 3 JSON;"
     "Anonymous request succeeds; CORS preflight passes;"
     "Schema includes /bff/* surface so FE generator works",
     f"{SPEC}#11-session-bootstrap;{BE_BFF}"),

    ("BFF-B1-003",
     "GET /bff/me session bootstrap",
     "Claude", "Codex", PHASE_P0,
     "BFF-B1-001",
     "Returns payload per spec section 1.1 B1-001 schema including operatorId roles tenantId allowedTenants locale sessionKind capabilities featureFlags;"
     "Anonymous returns 401 with typed error; authenticated returns 200;"
     "meta.correlationId echoed; X-Correlation-Id response header set",
     f"{SPEC}#11-session-bootstrap;{BE_BFF};{FE_PATHS}"),

    ("BFF-B1-004",
     "PATCH /bff/me/locale operator locale preference",
     "Claude", "Codex", PHASE_P0,
     "BFF-B1-003",
     "Persists operator locale; /bff/me reflects new locale on next call;"
     "Validates locale code; returns typed error on invalid input;"
     "Idempotent for same locale value",
     f"{SPEC}#11-session-bootstrap;{BE_BFF}"),

    ("BFF-B1-005",
     "POST /bff/auth/refresh cookie or bearer refresh",
     "Claude", "Codex", PHASE_P0,
     "BFF-B1-003",
     "Refreshes cookie or bearer token when refresh credential present;"
     "Returns typed auth error code when no refresh path available;"
     "Never returns raw 500",
     f"{SPEC}#11-session-bootstrap;{BE_BFF}"),

    ("BFF-B1-006",
     "POST /bff/logout clear session",
     "Claude", "Codex", PHASE_P0,
     "BFF-B1-003",
     "Clears cookie or invalidates session; idempotent on repeat call;"
     "/bff/me afterwards returns 401",
     f"{SPEC}#11-session-bootstrap;{BE_BFF}"),

    ("BFF-B1-007",
     "POST /bff/v1/commands canonical command admission facade",
     "Claude", "Codex", PHASE_P0,
     "BFF-B1-001,BFF-B1-003",
     "Accepts spec section 1.3 input schema (command target action params audit_context confirmToken approvalDecisionId twoManSignatureId);"
     "Required headers Authorization X-Correlation-Id X-Request-Id X-Idempotency-Key honored;"
     "Internally maps to /api/v1/operator/commands;"
     "Returns CommandResponse with status accepted receipt_id command_id trackingUrl meta.idempotency;"
     "Duplicate idempotency key with same payload replays; different payload returns 409;"
     "live broker scope fail-closed when disabled",
     f"{SPEC}#13-command--action-compatibility;{BE_BFF};{BE_OP_CMD}"),

    ("BFF-B1-008",
     "POST /bff/actions/{entityType}/{entityId}/{actionId} action facade",
     "Claude", "Codex", PHASE_P0,
     "BFF-B1-007",
     "Routes into same command admission as /bff/v1/commands;"
     "Returns same CommandResponse envelope;"
     "Idempotency rules identical to BFF-B1-007",
     f"{SPEC}#13-command--action-compatibility;{BE_BFF}"),

    ("BFF-B1-009",
     "Confirm-token lifecycle 5 endpoints",
     "Claude", "Codex", PHASE_P0,
     "BFF-B1-001",
     "POST /bff/confirm-tokens issues token;"
     "GET /bff/confirm-tokens/{tokenId} returns state;"
     "POST /bff/confirm-tokens/{tokenId}/redeem marks redeemed;"
     "POST /bff/command-confirmations and GET /bff/command-confirmations/{token} mirror lifecycle;"
     "Expired tokens return typed 410 not raw 500",
     f"{SPEC}#14-confirm-token-lifecycle;{BE_BFF}"),

    ("BFF-B1-010",
     "POST /bff/approvals/{id}/decide and batch-decide",
     "Claude", "Codex", PHASE_P0,
     "BFF-B1-007",
     "Single decide accepts approve reject request_changes escalate;"
     "Batch-decide accepts list and returns per-id status;"
     "Routes through governance services or /api/v1/operator/commands;"
     "Anonymous 401; authenticated 200",
     f"{SPEC}#12-decision-endpoints;{BE_BFF}"),

    ("BFF-B1-011",
     "POST /bff/v5/interventions/{id}/decide",
     "Claude", "Codex", PHASE_P0,
     "BFF-B1-007",
     "v5 human intervention decide endpoint accepts decision payload;"
     "Returns command receipt envelope;"
     "Pairs with B4-005 GET /bff/v5/interventions/{id}",
     f"{SPEC}#b4--p1-v5-closed-loop-os-apis;{BE_BFF}"),

    ("BFF-B1-012",
     "POST /bff/alerts/{id}/acknowledge",
     "Claude", "Codex", PHASE_P0,
     "BFF-B1-007",
     "Acknowledges alert; idempotent on repeat;"
     "Alert state transitions to acknowledged in subsequent GET /bff/alerts",
     f"{SPEC}#12-decision-endpoints;{BE_BFF}"),

    # ---------- P1 Core / B2 ----------
    ("BFF-B2-001",
     "Strategy / Persona / Capital / Deployment list-detail facade (B2.1 14 endpoints)",
     "Claude2", "Codex2", PHASE_CORE,
     "BFF-B1-003",
     "Implements all 14 endpoints in spec section B2.1 (strategies x3 personas x5 capital-pools x2 rebalances x2 deployments x2);"
     "List endpoints return {items pageInfo meta} envelope;"
     "Detail endpoints return {data meta};"
     "meta.source one of live local_snapshot degraded;"
     "Anonymous 401 authenticated 200",
     f"{SPEC}#b21-strategy--persona--capital--deployment-core;{BE_BFF};{FE_PATHS}"),

    ("BFF-B2-002",
     "Evolution + Operations facade (B2.2 13 endpoints)",
     "Claude2", "Codex2", PHASE_CORE,
     "BFF-B2-001",
     "Implements all 13 endpoints in spec section B2.2 (evolution-programs x4 jobs x2 alerts incidents x2 audit artifacts x2 runtimes);"
     "Adapts /api/v1/evolution-decisions and /api/v1/operator/alerts and /api/v1/operator/runtime-state;"
     "Follows BFF envelope rules from spec section 3",
     f"{SPEC}#b22-evolution--jobs--ops;{BE_BFF}"),

    ("BFF-B2-003",
     "Capabilities facade: mcp-servers mcp-tools skills channels tools ranking-formulas",
     "Claude2", "Codex2", PHASE_CORE,
     "BFF-B2-001",
     "Implements /bff/mcp-servers /bff/mcp-tools /bff/skills /bff/channels /bff/tools /bff/ranking-formulas;"
     "List envelope per spec section 2.4;"
     "FE-compatible DTO matching execute-plans capability registry",
     f"{SPEC}#b23-capabilities--research--search;{BE_BFF}"),

    ("BFF-B2-004",
     "Research and search facade: /bff/research-experiments and /bff/search",
     "Claude2", "Codex2", PHASE_CORE,
     "BFF-B2-001",
     "GET /bff/research-experiments returns research experiment list with envelope;"
     "GET /bff/search?q=... returns search hits with cursor pagination;"
     "Anonymous 401 authenticated 200",
     f"{SPEC}#b23-capabilities--research--search;{BE_BFF}"),

    ("BFF-B2-005",
     "Agora canonical aliases (B7 6 endpoints)",
     "Claude2", "Codex2", PHASE_CORE,
     "BFF-B1-003",
     "Adds /bff/agora/ask/sessions and /bff/agora/ask/sessions/{id} aliases on top of existing /bff/agora/sessions;"
     "/bff/agora/signals /bff/agora/journal /bff/agora/postmortems verified and returns envelope;"
     "/bff/agora/inbox composes insights signals tasks per spec section B7",
     f"{SPEC}#b7--agora-compatibility-apis;{BE_BFF}"),

    ("BFF-B2-006",
     "v5 closed-loop read routes (B4 4 read endpoints)",
     "Claude2", "Codex2", PHASE_CORE,
     "BFF-B2-001",
     "GET /bff/v5/loop-runs list and detail;"
     "GET /bff/v5/sentinel/findings;"
     "GET /bff/v5/execution/persona-health;"
     "GET /bff/v5/interventions/{id} detail;"
     "Anonymous 401 authenticated 200; envelope per section 3",
     f"{SPEC}#b4--p1-v5-closed-loop-os-apis;{BE_BFF}"),

    # ---------- P1 Mgmt / B3 ----------
    ("BFF-B3-001",
     "GET /bff/management/cockpit aggregate",
     "Codex", "Claude", PHASE_MGMT,
     "BFF-B2-001",
     "Composes operator home + runtime health + alerts + human inbox + trading pulse + anomalies per spec section B3.3;"
     "FE-compatible shape per execute-plans/src/lib/v5/management/cockpit.ts;"
     "Pathreon Management Cockpit renders live with no seed fallback",
     f"{SPEC}#b3--p1-management-aggregate-apis;{FE_MGMT};{BE_BFF}"),

    ("BFF-B3-002",
     "GET /bff/management/persona-fleet aggregate",
     "Codex", "Claude", PHASE_MGMT,
     "BFF-B2-001",
     "Composes personas + bindings + telemetry persona-health + training evolution info per spec section B3.3;"
     "FE Persona Fleet page renders live",
     f"{SPEC}#b3--p1-management-aggregate-apis;{FE_MGMT};{BE_BFF}"),

    ("BFF-B3-003",
     "GET /bff/management/human-inbox aggregate and detail",
     "Codex", "Claude", PHASE_MGMT,
     "BFF-B2-001",
     "List composes approvals + interventions + sentinel + readiness blockers + policy violations;"
     "Detail /bff/management/human-inbox/{id} returns HumanGateDetail shape;"
     "Detail links resolve to command path",
     f"{SPEC}#b3--p1-management-aggregate-apis;{FE_MGMT};{BE_BFF}"),

    ("BFF-B3-004",
     "GET /bff/management/trading-pulse and rankings",
     "Codex", "Claude", PHASE_MGMT,
     "BFF-B2-001",
     "trading-pulse composes telemetry performance + runtime status + rankings + baseline comparison;"
     "trading-pulse/rankings returns ranking blocks;"
     "FE Trading Pulse cards render live",
     f"{SPEC}#b3--p1-management-aggregate-apis;{FE_MGMT};{BE_BFF}"),

    ("BFF-B3-005",
     "GET /bff/management/evolution-journal aggregate",
     "Codex", "Claude", PHASE_MGMT,
     "BFF-B2-002",
     "Composes evolution decisions + postmortems + mutation review + rollback freeze records;"
     "FE Evolution Journal page renders live",
     f"{SPEC}#b3--p1-management-aggregate-apis;{FE_MGMT};{BE_BFF}"),

    ("BFF-B3-006",
     "GET /bff/management/evidence Evidence Explorer aggregate",
     "Codex", "Claude", PHASE_MGMT,
     "BFF-B2-001",
     "Adapts /api/v1/knowledge/evidence into Management Evidence Explorer shape;"
     "Evidence rows include working links;"
     "FE Evidence Explorer renders live",
     f"{SPEC}#b3--p1-management-aggregate-apis;{FE_MGMT};{BE_BFF}"),

    ("BFF-B3-007",
     "GET /bff/management/persona-intent redacted aggregate",
     "Codex", "Claude", PHASE_MGMT,
     "BFF-B2-001",
     "Returns redacted persona trace + trainer + Agora intent summaries;"
     "PII / strategy alpha details redacted per governance policy;"
     "FE Persona Intent Traces renders live",
     f"{SPEC}#b3--p1-management-aggregate-apis;{FE_MGMT};{BE_BFF}"),

    ("BFF-B3-008",
     "Readiness 5 endpoints: ep5 broker-live capital-binding-live bff-ha strict-publish",
     "Codex", "Claude", PHASE_MGMT,
     "BFF-B2-001",
     "Implements all 5 readiness endpoints in spec section B3.1 rows B3-010..014;"
     "Composes M7 packets + evidence refs + broker BFF strict-publish status + human gates;"
     "FE readiness pages show real evidence and blockers",
     f"{SPEC}#b3--p1-management-aggregate-apis;{FE_MGMT};{BE_BFF}"),

    # ---------- PM-12 ----------
    ("BFF-PM12-001",
     "GET /bff/management/portfolio-book summary",
     "Codex2", "Claude2", PHASE_PM12,
     "BFF-B2-001",
     "Composes capital pools + runtime bindings + telemetry + holdings snapshot;"
     "FE Portfolio summary card renders live with total capital exposure PnL",
     f"{SPEC}#b32-pm-12-performance--portfolio-10-endpoints;{FE_MGMT};{BE_BFF}"),

    ("BFF-PM12-002",
     "GET /bff/management/portfolio-book/holdings global holdings",
     "Codex2", "Claude2", PHASE_PM12,
     "BFF-PM12-001",
     "Returns positions + fills + mark prices + strategy persona runtime links;"
     "FE Global holdings table renders live",
     f"{SPEC}#b34-pm-12-composition-sources;{FE_MGMT};{BE_BFF}"),

    ("BFF-PM12-003",
     "GET /bff/management/portfolio-book/pools pool summaries",
     "Codex2", "Claude2", PHASE_PM12,
     "BFF-PM12-001",
     "Returns capital pool list + exposure + risk budget + PnL;"
     "FE Capital pool summary table renders live",
     f"{SPEC}#b34-pm-12-composition-sources;{FE_MGMT};{BE_BFF}"),

    ("BFF-PM12-004",
     "GET /bff/management/persona-league table",
     "Codex2", "Claude2", PHASE_PM12,
     "BFF-B2-001",
     "Composes personas + strategy bindings + PnL + risk + execution metrics;"
     "FE Persona League table renders live",
     f"{SPEC}#b34-pm-12-composition-sources;{FE_MGMT};{BE_BFF}"),

    ("BFF-PM12-005",
     "Persona league rankings and tiers",
     "Codex2", "Claude2", PHASE_PM12,
     "BFF-PM12-004",
     "GET /bff/management/persona-league/rankings returns computed ranking blocks;"
     "GET /bff/management/persona-league/tiers returns tier config / current season tiers;"
     "FE league pages render live",
     f"{SPEC}#b34-pm-12-composition-sources;{FE_MGMT};{BE_BFF}"),

    ("BFF-PM12-006",
     "GET /bff/management/quarterly-ranking",
     "Codex2", "Claude2", PHASE_PM12,
     "BFF-PM12-004",
     "Accepts ?quarter=YYYY-Qn query;"
     "Composes persona league + formula + quarter window + evidence;"
     "FE quarterly ranking page renders live",
     f"{SPEC}#b34-pm-12-composition-sources;{FE_MGMT};{BE_BFF}"),

    ("BFF-PM12-007",
     "GET /bff/management/quarterly-ranking/formula",
     "Codex2", "Claude2", PHASE_PM12,
     "BFF-PM12-006",
     "Returns formula weights and version;"
     "Version increments traceable to governance evidence",
     f"{SPEC}#b34-pm-12-composition-sources;{FE_MGMT};{BE_BFF}"),

    ("BFF-PM12-008",
     "GET /bff/management/quarterly-ranking/recommendations",
     "Codex2", "Claude2", PHASE_PM12,
     "BFF-PM12-006",
     "Accepts ?quarter=YYYY-Qn query;"
     "Returns governance recommendations only (promote_to_canary_candidate increase_research_budget grant_tool_access reduce_capital_access require_retraining freeze_persona suspend_persona retire_persona);"
     "No direct live capital change; all writes enter Human Inbox per spec section B3.5",
     f"{SPEC}#b35-important-policy-rule;{FE_MGMT};{BE_BFF}"),

    ("BFF-PM12-009",
     "GET /bff/management/performance-attribution",
     "Codex2", "Claude2", PHASE_PM12,
     "BFF-PM12-001",
     "Accepts ?dimension= and ?period= query;"
     "Returns attribution rows by persona strategy pool asset broker runtime regime;"
     "FE Performance Attribution table renders live",
     f"{SPEC}#b34-pm-12-composition-sources;{FE_MGMT};{BE_BFF}"),

    # ---------- P2 ----------
    ("BFF-B5-001",
     "HumanGate command operations via /bff/v1/commands",
     "Claude", "Codex", PHASE_HG,
     "BFF-B1-007,BFF-B3-003",
     "Implements command names HumanGateApprove HumanGateReject HumanGateRequestMoreEvidence HumanGateRevoke HumanGateExtendTtl QuarterlyRankingRecommendationSubmit;"
     "Each command path returns standard CommandResponse;"
     "Human Inbox decision flow can approve reject request-evidence through command path",
     f"{SPEC}#b5--p15--p2-humangate-write-apis;{BE_BFF}"),

    ("BFF-B6-001",
     "POST /bff/management/nl/ask Management NL endpoint",
     "Claude", "Codex", PHASE_NL,
     "BFF-B3-001",
     "Accepts spec section B6 input schema prompt pageContext intent;"
     "Returns data.summary bullets followups evidenceRefs refused provider;"
     "Frontend never calls model provider directly;"
     "BFF performs auth scope redaction context retrieval model provider call grounding audit",
     f"{SPEC}#b6--p2-management-natural-language-api;{BE_BFF}"),

    ("BFF-B6-002",
     "NL audit and evidence grounding",
     "Claude", "Codex", PHASE_NL,
     "BFF-B6-001",
     "Every NL call writes audit record auditRef;"
     "Responses include evidenceRefs pointing to /api/v1/knowledge/evidence;"
     "meta.redactedEvidenceCount accurate",
     f"{SPEC}#b6--p2-management-natural-language-api;{BE_BFF}"),

    ("BFF-B6-003",
     "NL high-risk refusal policy",
     "Claude", "Codex", PHASE_NL,
     "BFF-B6-001",
     "Command-like high-risk prompts return refused=true with Human Inbox link in followups;"
     "Refusal does not call upstream model provider;"
     "Audit log records refusal reason",
     f"{SPEC}#b6--p2-management-natural-language-api;{BE_BFF}"),
]


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
    print(f"Dispatching {len(TASKS)} tasks ...")
    for task in TASKS:
        dispatch_one(*task)
    print(f"\nDispatched {len(TASKS)} tasks. Run `python3 scripts/ai_status.py sync` to refresh derived files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

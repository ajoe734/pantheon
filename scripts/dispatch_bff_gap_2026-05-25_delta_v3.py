#!/usr/bin/env python3
"""One-shot dispatcher for the 2026-05-25 BFF API GAP delta-v3 audit tasks.

Source audit: execute-plans/.lovable/audits/bff-backend-gap-2026-05-25-delta-v3.md
Spec doc:    docs/04/pantheon_bff_api_gap_2026-05-25_delta_v3/BFF_API_GAP_delta_v3_spec.md

v2 (2026-05-24) dispatched 22 tasks; 21 archived, but 1 critical deploy task
(OPS-BFF-LUPIN-DEV-REDEPLOY-20260524) blocked on Gemini2 GCP IAM and was
cleaned up without ever shipping to live. Result: 22 routes + CORS + envelope
all sit on origin/dev but live BFF is stale. Lovable v3 audit therefore shows
the same gap surface.

v3 dispatches 3 tasks:
  OPS-BFF-LUPIN-DEV-REDEPLOY-20260525   Codex / Claude   (retry; user explicit reassign)
  BFF-INFRA-ERRORCODE-PACKD-001          Codex / Claude   (real code gap: Pack D §D21)
  OPS-DOC-BFF-NAMING-CANONICAL-001       Claude / Codex   (decision doc only)

Owner / reviewer per user instruction (Q1: Codex for redeploy).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "ai-status.json"

SPEC = "docs/04/pantheon_bff_api_gap_2026-05-25_delta_v3/BFF_API_GAP_delta_v3_spec.md"
AUDIT = "execute-plans/.lovable/audits/bff-backend-gap-2026-05-25-delta-v3.md"
PRIOR_SPEC = "docs/04/pantheon_bff_api_gap_2026-05-24_delta/BFF_API_GAP_delta_audit_spec.md"
BE_BFF = "services/control-plane/bff/main.py"
NAMING_DOC = "docs/04/pantheon_bff_api_gap_2026-05-25_delta_v3/CANONICAL_PATH_NAMING.md"

PHASE_INFRA = "Sprint BFF-DELTA-V3 / EPIC-BFF-DELTA-V3-INFRA"

SPRINT_ID = "2026-05-25-pantheon-bff-p0-delta-v3"
SPRINT_OBJECTIVE = (
    "Pantheon BFF P0 Delta-v3 — close the v2 deploy-lag bottleneck plus 1 real "
    "Pack D ErrorCode alignment plus 1 canonical path naming decision. v2 (2026-05-24) "
    "shipped 22 of 23 task to dev (routes + CORS + envelope) but the single deploy "
    "task OPS-BFF-LUPIN-DEV-REDEPLOY-20260524 blocked on Gemini2 GCP IAM "
    "(compute.instances.get missing on lupin project) and was cleaned up without "
    "ever rolling out a new image. Lovable v3 audit on 2026-05-25 therefore shows "
    "essentially the same surface as v2 - 24/24 management routes still 404, CORS "
    "still 400, envelope still detail-wrapped - all because lupin dev BFF is running "
    "stale image. v3 reassigns redeploy to Codex (user explicit), adds Pack D "
    "ErrorCode enum alignment (audit caught OBJECT_NOT_FOUND not in canonical 26), "
    "and one decision doc for 5 FE/BE naming alignments. Babysit protocol: do not "
    "mark sprint done until live BFF curls verify 8 audit paths return 200."
)

# (task_id, title, owner, reviewer, phase, depends_on, acceptance, artifacts)
TASKS = [
    (
        "OPS-BFF-LUPIN-DEV-REDEPLOY-20260525",
        "Re-deploy lupin dev BFF (retry from v2 blocker) - verify 8 audit paths live",
        "Codex", "Claude", PHASE_INFRA,
        "",
        # NOTE: parse_csv_env splits on ',' - avoid commas inside acceptance text.
        "Investigate v2 IAM failure root cause and document either self-grant path or operator handoff with exact gcloud command;"
        "Rebuild lupin dev BFF image from pantheon@origin/dev HEAD;"
        "Push image and roll out service; new pod ready;"
        "CORS preflight from 4 Lovable origins to /bff/me returns 204 with full ACAO ACAH ACAM ACEH headers;"
        "8 path live curl with Authorization Bearer pantheon-dev-browser:reviewer all return 200 (POST /bff/approvals/batch-decide; GET /bff/command-confirmations/{token}; GET /bff/management/cockpit; GET /bff/management/persona-league/rankings; GET /bff/management/persona-league/movers; GET /bff/management/quarterly-ranking?quarter=2026-Q2; GET /bff/management/performance-attribution; GET /bff/management/portfolio-book);"
        "Evidence at support/evidence/bff-delta-v3-20260525/redeploy-curl-results.md committed before status transition to done;"
        "Do not mark done if any curl returns 404 or 500",
        f"{SPEC};{AUDIT};{PRIOR_SPEC};{BE_BFF};support/evidence/bff-delta-v3-20260525/redeploy-curl-results.md",
    ),
    (
        "BFF-INFRA-ERRORCODE-PACKD-001",
        "Align ErrorCode enum to Pack D §D21 26 canonical codes",
        "Codex", "Claude", PHASE_INFRA,
        "",
        # NOTE: parse_csv_env splits on ',' - avoid commas inside acceptance text.
        "Audit services/control-plane/bff/main.py ErrorCode enum against Pack D §D21 canonical 26-code allowlist;"
        "Rename OBJECT_NOT_FOUND to RESOURCE_NOT_FOUND and align all other non-canonical names;"
        "Status-code mapping table updated; 404 maps to RESOURCE_NOT_FOUND;"
        "Contract test test_bff_error_envelope_shape.py extended to assert exactly Pack D §D21 26-code allowlist;"
        "Any code outside allowlist fails the test;"
        "Live verification (after OPS-BFF-LUPIN-DEV-REDEPLOY-20260525 done): GET /bff/strategies/__nonexistent__ returns error.code RESOURCE_NOT_FOUND",
        f"{SPEC};{AUDIT};{BE_BFF};services/control-plane/bff/test_bff_error_envelope_shape.py",
    ),
    (
        "OPS-DOC-BFF-NAMING-CANONICAL-001",
        "Decision doc for 5 FE/BE naming alignments plus 12 snake_case duplicates",
        "Claude", "Codex", PHASE_INFRA,
        "",
        # NOTE: parse_csv_env splits on ',' - avoid commas inside acceptance text.
        "Decision doc at docs/04/pantheon_bff_api_gap_2026-05-25_delta_v3/CANONICAL_PATH_NAMING.md;"
        "For each of 5 FE/BE naming pairs from v3 audit decide canonical name and document migration path (persona-fleet vs persona-league; human-inbox; trading-pulse; evolution-journal; evidence; portfolio-book/holdings vs /positions; portfolio-book/pools vs /exposure);"
        "For 12 snake_case duplicate families (P3 advisory) recommend keep/deprecate/both;"
        "Cross-reference each decision to Pack D §D2 path naming convention;"
        "FE migration ticket noted as separate follow-up not in scope here",
        f"{SPEC};{AUDIT};{NAMING_DOC}",
    ),
]


def update_sprint_metadata() -> None:
    """Update ai-status.json sprint id + objective before dispatching."""
    state = json.loads(STATE_PATH.read_text())
    state["sprint"] = SPRINT_ID
    state["sprint_started_at"] = "2026-05-25T00:00:00Z"
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
        "Run `python3 scripts/ai_status.py sync` to refresh derived files. "
        "BABYSIT: check OPS-BFF-LUPIN-DEV-REDEPLOY-20260525 within 4 hours."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Commands without an executing owner must fail before admission."""
from fastapi import HTTPException


RETIRED_COMMANDS = {
    "ApproveRollback": "/bff/approvals/{decision_id}/decide",
    "RejectRollback": "/bff/approvals/{decision_id}/decide",
    "RankingAction": "GET /bff/rankings",
    "RankingFormulaAction": "GET /bff/ranking-formulas",
    "QuarterlyRankingRecommendationSubmit": "GET /bff/rankings",
    "PromotionReviewDecision": "/bff/approvals/{decision_id}/decide",
    "AuditExport": "GET /bff/audit",
    "ToolAction": "GET /bff/tools",
    "McpServerAction": "GET /bff/mcp-servers",
    "SkillAction": "GET /bff/skills",
    "HumanGateApprove": "/bff/approvals/{decision_id}/decide",
    "HumanGateReject": "/bff/approvals/{decision_id}/decide",
    "HumanGateRequestMoreEvidence": "GET /bff/approvals/{decision_id}",
    "HumanGateExtendTtl": "GET /bff/approvals/{decision_id}",
    "RequestReview": "GET /bff/approvals",
    "CreateDeployment": "approval then POST /api/deployment/plans/validate and POST /api/deployment/plans",
}


def reject_retired_command(command: str) -> None:
    replacement = RETIRED_COMMANDS.get(command)
    if replacement:
        raise HTTPException(410, detail={"error": {
            "code": "ACTION_RETIRED",
            "message": f"{command} has no executing owner; use {replacement}",
            "details": {"replacement": replacement},
        }})


def reject_unowned_action(cmd) -> None:
    action = str(cmd.action or cmd.params.get("action_id") or cmd.params.get("actionId") or "").lower()
    command = cmd.command.value
    unsupported = (
        command == "ExperimentAction" and action not in {"cancel", "retry", "archive", "archived", "invalidate", "invalidated"}
        or command == "JobAction" and (not cmd.target.id.startswith("job-orchestrator-") or action not in {"cancel", "retry"})
    )
    if unsupported:
        raise HTTPException(410, detail={"error": {
            "code": "ACTION_RETIRED",
            "message": f"{command}/{action} has no executing owner; use /bff/v1/commands with a supported owner action",
            "details": {"replacement": "/bff/v1/commands"},
        }})

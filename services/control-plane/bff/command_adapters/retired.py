"""Commands without an executing owner must fail before admission."""
from fastapi import HTTPException


RETIRED_COMMANDS = {
    "ApproveRollback": "/bff/approvals/{decision_id}/decide",
    "RejectRollback": "/bff/approvals/{decision_id}/decide",
    "RankingAction": "GET /bff/rankings",
    "RankingFormulaAction": "GET /bff/ranking-formulas",
    "QuarterlyRankingRecommendationSubmit": "GET /bff/rankings",
    "AuditExport": "GET /bff/audit",
    "ToolAction": "GET /bff/tools",
    "McpServerAction": "GET /bff/mcp-servers",
    "SkillAction": "GET /bff/skills",
    "HumanGateApprove": "/bff/approvals/{decision_id}/decide",
    "HumanGateReject": "/bff/approvals/{decision_id}/decide",
    "HumanGateRequestMoreEvidence": "GET /bff/approvals/{decision_id}",
    "HumanGateExtendTtl": "GET /bff/approvals/{decision_id}",
}


def reject_retired_command(command: str) -> None:
    replacement = RETIRED_COMMANDS.get(command)
    if replacement:
        raise HTTPException(410, detail={"error": {
            "code": "ACTION_RETIRED",
            "message": f"{command} has no executing owner; use {replacement}",
            "details": {"replacement": replacement},
        }})

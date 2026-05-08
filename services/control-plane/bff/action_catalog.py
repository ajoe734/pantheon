"""
Canonical BFF action catalog (BFF-FINAL-004).

This module is the single source of truth for all operator-facing actions.
The frontend maps these descriptors into ActionDescriptor[] and never invents
action truth on its own.

Each entry expresses:
  - action_id       : matches CommandType value
  - entity_type     : the ObjectType the action targets
  - endpoint        : canonical BFF endpoint for command submission
  - risk_level      : low | medium | high | critical
  - requires_*      : approval / confirm_token / two_man gates
  - cooldown_seconds: minimum re-trigger interval
  - idempotency_required: whether Idempotency-Key is mandatory
  - required_roles  : minimum RBAC role set
  - description     : human-readable action summary
"""
from __future__ import annotations

from models import BffActionCatalogEntry, BffActionCatalogResponse, RiskLevel

_FINAL_COMMAND_ENDPOINT = "/bff/v1/commands"

_CATALOG_ENTRIES: list[BffActionCatalogEntry] = [
    # ------------------------------------------------------------------ #
    # Approval / decision governance
    # ------------------------------------------------------------------ #
    BffActionCatalogEntry(
        action_id="ApproveDeployment",
        entity_type="DeploymentPlan",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.MEDIUM,
        requires_approval=True,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["approver"],
        description="Approve a pending deployment plan for execution.",
    ),
    BffActionCatalogEntry(
        action_id="ApproveDecision",
        entity_type="ApprovalDecision",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.MEDIUM,
        requires_approval=True,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["approver"],
        description="Approve a pending governance decision.",
    ),
    BffActionCatalogEntry(
        action_id="RejectDecision",
        entity_type="ApprovalDecision",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.MEDIUM,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["approver"],
        description="Reject a pending governance decision.",
    ),
    BffActionCatalogEntry(
        action_id="RequestApprovalRevision",
        entity_type="ApprovalDecision",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.LOW,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["approver", "operator"],
        description="Request a revision to a pending approval before it can be accepted.",
    ),
    # ------------------------------------------------------------------ #
    # Runtime control
    # ------------------------------------------------------------------ #
    BffActionCatalogEntry(
        action_id="PauseRuntime",
        entity_type="Runtime",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.HIGH,
        requires_approval=False,
        requires_confirm_token=True,
        requires_two_man=False,
        cooldown_seconds=30,
        idempotency_required=True,
        required_roles=["operator", "approver"],
        description="Pause a live runtime binding; no new orders will be placed.",
    ),
    BffActionCatalogEntry(
        action_id="PauseExecution",
        entity_type="Runtime",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.MEDIUM,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=10,
        idempotency_required=True,
        required_roles=["operator"],
        description="Pause execution within a runtime without full binding suspension.",
    ),
    BffActionCatalogEntry(
        action_id="IssueRiskOff",
        entity_type="Runtime",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.HIGH,
        requires_approval=False,
        requires_confirm_token=True,
        requires_two_man=False,
        cooldown_seconds=60,
        idempotency_required=True,
        required_roles=["operator", "approver"],
        description="Issue a risk-off signal to halt new risk-taking positions.",
    ),
    BffActionCatalogEntry(
        action_id="IssueSafeMode",
        entity_type="Runtime",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.HIGH,
        requires_approval=False,
        requires_confirm_token=True,
        requires_two_man=False,
        cooldown_seconds=60,
        idempotency_required=True,
        required_roles=["operator", "approver"],
        description="Switch a runtime into safe mode; reduces exposure and disables new strategies.",
    ),
    # ------------------------------------------------------------------ #
    # Rollback lifecycle
    # ------------------------------------------------------------------ #
    BffActionCatalogEntry(
        action_id="ExecuteRollback",
        entity_type="Rollback",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
        requires_confirm_token=True,
        requires_two_man=False,
        cooldown_seconds=120,
        idempotency_required=True,
        required_roles=["operator", "approver"],
        description="Execute a previously prepared rollback plan.",
    ),
    BffActionCatalogEntry(
        action_id="ApproveRollback",
        entity_type="Rollback",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.MEDIUM,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["approver"],
        description="Approve a pending rollback plan so it can be executed.",
    ),
    BffActionCatalogEntry(
        action_id="RejectRollback",
        entity_type="Rollback",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.MEDIUM,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["approver"],
        description="Reject a pending rollback plan and cancel the rollback flow.",
    ),
    BffActionCatalogEntry(
        action_id="HardRollback",
        entity_type="Rollback",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.CRITICAL,
        requires_approval=True,
        requires_confirm_token=True,
        requires_two_man=True,
        cooldown_seconds=300,
        idempotency_required=True,
        required_roles=["approver"],
        description="Force an immediate hard rollback with full capital unwind; requires two-man authorization.",
    ),
    # ------------------------------------------------------------------ #
    # Kill switch
    # ------------------------------------------------------------------ #
    BffActionCatalogEntry(
        action_id="ActivateKillSwitch",
        entity_type="KillSwitchOrder",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.CRITICAL,
        requires_approval=True,
        requires_confirm_token=True,
        requires_two_man=True,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["approver"],
        description="Activate the system-wide kill switch; halts all trading and unwinds positions.",
    ),
    # ------------------------------------------------------------------ #
    # Capital pool operations
    # ------------------------------------------------------------------ #
    BffActionCatalogEntry(
        action_id="LiquidateAll",
        entity_type="CapitalPool",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.CRITICAL,
        requires_approval=True,
        requires_confirm_token=True,
        requires_two_man=True,
        cooldown_seconds=300,
        idempotency_required=True,
        required_roles=["approver"],
        description="Liquidate all positions in a capital pool; requires two-man authorization.",
    ),
    # ------------------------------------------------------------------ #
    # Evolution / diff governance
    # ------------------------------------------------------------------ #
    BffActionCatalogEntry(
        action_id="EscalateDiff",
        entity_type="EvolutionDecision",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.LOW,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["operator"],
        description="Escalate a strategy diff for human governance review.",
    ),
    BffActionCatalogEntry(
        action_id="ApproveEvolutionDecision",
        entity_type="EvolutionDecision",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.MEDIUM,
        requires_approval=True,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["approver"],
        description="Approve an evolution decision to allow strategy parameter changes.",
    ),
    BffActionCatalogEntry(
        action_id="ExecuteEvolutionAction",
        entity_type="EvolutionDecision",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.MEDIUM,
        requires_approval=True,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=60,
        idempotency_required=True,
        required_roles=["operator", "approver"],
        description="Execute a previously approved evolution action.",
    ),
    # ------------------------------------------------------------------ #
    # Mutation review
    # ------------------------------------------------------------------ #
    BffActionCatalogEntry(
        action_id="ApproveMutation",
        entity_type="ApprovalDecision",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.MEDIUM,
        requires_approval=True,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["approver"],
        description="Approve a pending mutation decision.",
    ),
    BffActionCatalogEntry(
        action_id="RejectMutation",
        entity_type="ApprovalDecision",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.MEDIUM,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["approver"],
        description="Reject a pending mutation decision.",
    ),
    # ------------------------------------------------------------------ #
    # Sponsor / committee governance
    # ------------------------------------------------------------------ #
    BffActionCatalogEntry(
        action_id="RecordSponsorDecision",
        entity_type="CommitteeBoard",
        endpoint=_FINAL_COMMAND_ENDPOINT,
        risk_level=RiskLevel.MEDIUM,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["approver"],
        description="Record a committee sponsor decision (approved / rejected / conditional).",
    ),
    # ------------------------------------------------------------------ #
    # HIQ Sentinel interventions (v5)
    # ------------------------------------------------------------------ #
    BffActionCatalogEntry(
        action_id="RemediateSentinelIntervention",
        entity_type="SentinelIntervention",
        endpoint="/bff/v5/interventions/{intervention_id}/remediate",
        risk_level=RiskLevel.CRITICAL,
        requires_approval=True,
        requires_confirm_token=True,
        requires_two_man=True,
        cooldown_seconds=60,
        idempotency_required=True,
        required_roles=["approver"],
        description="Execute HIQ Sentinel remediation for a pending intervention; requires two-man authorization.",
    ),
    # ------------------------------------------------------------------ #
    # Strategy / persona resource actions (BFF-LUV-GAP-002)
    # ------------------------------------------------------------------ #
    BffActionCatalogEntry(
        action_id="StrategyAction",
        entity_type="Strategy",
        endpoint="/bff/strategies/{strategy_id}/actions/{action_id}",
        risk_level=RiskLevel.MEDIUM,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["operator", "approver"],
        description="Generic strategy lifecycle action (edit, submit, retire, dry-run wiring); precondition gates flow through the action catalog and command queue.",
    ),
    BffActionCatalogEntry(
        action_id="PersonaAction",
        entity_type="Persona",
        endpoint="/bff/personas/{persona_id}/actions/{action_id}",
        risk_level=RiskLevel.MEDIUM,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["operator", "approver"],
        description="Generic persona lifecycle action (edit, retire, route-policy update, test-prompt wiring); precondition gates flow through the action catalog and command queue.",
    ),
    # ------------------------------------------------------------------ #
    # Capital / ranking / rebalance resource actions (BFF-LUV-GAP-003)
    # ------------------------------------------------------------------ #
    BffActionCatalogEntry(
        action_id="CapitalPoolAction",
        entity_type="CapitalPool",
        endpoint="/bff/capital-pools/{pool_id}/actions/{action_id}",
        risk_level=RiskLevel.MEDIUM,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["operator", "approver"],
        description="Generic capital pool lifecycle action (activate, freeze, retire, allocation update); precondition gates flow through the action catalog and command queue.",
    ),
    BffActionCatalogEntry(
        action_id="RankingFormulaAction",
        entity_type="RankingFormula",
        endpoint="/bff/ranking/formulas/{formula_id}/actions/{action_id}",
        risk_level=RiskLevel.LOW,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["operator", "approver"],
        description="Generic ranking formula lifecycle action (activate, deactivate, recompute); precondition gates flow through the action catalog and command queue.",
    ),
    BffActionCatalogEntry(
        action_id="RebalanceAction",
        entity_type="Rebalance",
        endpoint="/bff/rebalances/{rebalance_id}/actions/{action_id}",
        risk_level=RiskLevel.HIGH,
        requires_approval=False,
        requires_confirm_token=True,
        requires_two_man=False,
        cooldown_seconds=30,
        idempotency_required=True,
        required_roles=["operator", "approver"],
        description="Generic rebalance lifecycle action (approve, cancel, reconcile); risky and routes through the command/precondition machinery.",
    ),
    BffActionCatalogEntry(
        action_id="RankingAction",
        entity_type="Ranking",
        endpoint="/bff/rankings/{ranking_id}/actions/{action_id}",
        risk_level=RiskLevel.LOW,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["operator", "approver"],
        description="Generic ranking lifecycle action (refresh, archive, tag); precondition gates flow through the action catalog and command queue.",
    ),
    # ------------------------------------------------------------------ #
    # Tools / MCP servers / skills resource actions (BFF-LUV-GAP-008)
    # ------------------------------------------------------------------ #
    BffActionCatalogEntry(
        action_id="ToolAction",
        entity_type="Tool",
        endpoint="/bff/tools/{tool_id}/actions/{action_id}",
        risk_level=RiskLevel.MEDIUM,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["operator", "approver"],
        description="Generic tool lifecycle action (enable, disable, configure); precondition gates flow through the action catalog and command queue.",
    ),
    BffActionCatalogEntry(
        action_id="McpServerAction",
        entity_type="McpServer",
        endpoint="/bff/mcp/servers/{server_id}/actions/{action_id}",
        risk_level=RiskLevel.MEDIUM,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["operator", "approver"],
        description="Generic MCP server lifecycle action (connect, disconnect, refresh); precondition gates flow through the action catalog and command queue.",
    ),
    BffActionCatalogEntry(
        action_id="SkillAction",
        entity_type="Skill",
        endpoint="/bff/skills/{skill_id}/actions/{action_id}",
        risk_level=RiskLevel.MEDIUM,
        requires_approval=False,
        requires_confirm_token=False,
        requires_two_man=False,
        cooldown_seconds=0,
        idempotency_required=True,
        required_roles=["operator", "approver"],
        description="Generic skill lifecycle action (enable, disable, configure, promote); precondition gates flow through the action catalog and command queue.",
    ),
]

# Build an O(1) lookup by action_id
_CATALOG_INDEX: dict[str, BffActionCatalogEntry] = {
    e.action_id: e for e in _CATALOG_ENTRIES
}


def get_action_catalog() -> BffActionCatalogResponse:
    """Return the full canonical action catalog as a response object."""
    return BffActionCatalogResponse(catalog=_CATALOG_ENTRIES)


def get_catalog_entry(action_id: str) -> BffActionCatalogEntry | None:
    """Return a single catalog entry by action_id, or None if unknown."""
    return _CATALOG_INDEX.get(action_id)


def catalog_action_ids() -> frozenset[str]:
    """Return the set of all catalogued action_id values."""
    return frozenset(_CATALOG_INDEX.keys())

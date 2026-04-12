from __future__ import annotations

from models import WorkflowDefinition


INGEST_WORKFLOW = WorkflowDefinition(
    workflow_id="pantheon.ingest",
    schedule="0 */6 * * *",
    description="Ingest approved research sources and emit governed research intake handoffs.",
    upstream_entrypoint="research.ingest",
    handoff_type="research_package",
    from_stage="research_ingest",
    to_stage="strategy_normalization",
    execution_context="research",
    policy_id="oc002.cron.ingest",
    allowed_tool_classes=("research", "status"),
    artifact_type="strategy_spec",
    initial_lifecycle_state="draft",
    approval_required=False,
    required_payload_keys=(
        "strategy_id",
        "title",
        "hypothesis",
        "objective",
        "symbols",
        "frequency",
        "source_refs",
    ),
)


REVIEW_WORKFLOW = WorkflowDefinition(
    workflow_id="pantheon.review",
    schedule="15 7 * * 1-5",
    description="Package candidate review evidence into approval requests without auto-approving.",
    upstream_entrypoint="governance.review",
    handoff_type="approval_request",
    from_stage="registry_candidate",
    to_stage="paper_review",
    execution_context="paper",
    policy_id="oc002.cron.review",
    allowed_tool_classes=("status", "monitoring"),
    artifact_type="strategy_spec",
    initial_lifecycle_state="candidate",
    approval_required=True,
    required_payload_keys=("strategy_id", "spec_ref", "lineage_ref"),
    produces_strategy_ref=True,
)


RETRAIN_WORKFLOW = WorkflowDefinition(
    workflow_id="pantheon.retrain",
    schedule="0 2 * * 1-5",
    description="Trigger batch retraining from governed feedback or trajectory datasets.",
    upstream_entrypoint="learning.retrain",
    handoff_type="registry_submission",
    from_stage="approved_feedback",
    to_stage="registry_candidate",
    execution_context="research",
    policy_id="oc002.cron.retrain",
    allowed_tool_classes=("research", "status", "monitoring"),
    artifact_type="model_artifact",
    initial_lifecycle_state="draft",
    approval_required=True,
    required_payload_keys=("strategy_id", "spec_ref", "feedback_dataset_refs"),
    produces_strategy_ref=True,
)


DEPLOY_WORKFLOW = WorkflowDefinition(
    workflow_id="pantheon.deploy",
    schedule="*/15 * * * *",
    description="Create governed DeploymentPlans for approved registry entries and emit execution projections.",
    upstream_entrypoint="deployment.plan",
    handoff_type=None,
    from_stage="registry_approved",
    to_stage="execution_projection",
    execution_context="paper",
    policy_id="oc002.cron.deploy",
    allowed_tool_classes=("deployment", "status"),
    artifact_type="execution_bundle",
    initial_lifecycle_state="approved",
    approval_required=True,
    required_payload_keys=("registry_entry", "target_stage", "capital_pool_id"),
    uses_promotion_gate=True,
)


WORKFLOW_CATALOG = {
    workflow.workflow_id: workflow
    for workflow in (
        INGEST_WORKFLOW,
        REVIEW_WORKFLOW,
        RETRAIN_WORKFLOW,
        DEPLOY_WORKFLOW,
    )
}


def get_workflow_definition(workflow_id: str) -> WorkflowDefinition:
    try:
        return WORKFLOW_CATALOG[workflow_id]
    except KeyError as exc:
        raise KeyError(f"Unknown workflow: {workflow_id}") from exc

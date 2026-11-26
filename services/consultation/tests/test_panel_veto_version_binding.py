
from services.consultation.tests.test_persona_panel_integration import (
    stack, _bootstrap, _provider_fake, _config, _provider,
    _propose_and_review, _decide, _gov_post, TENANT, INITIATOR, SERVICE_TOKEN,
)
from services.consultation.client import ConsultationServiceClient
from services.consultation.workflow_executor import run_tick
from services.consultation.workflow_state import WorkflowStateStore
from services.persona import consult_triggers as triggers


def test_operator_approval_of_v1_must_not_clear_v2_veto(stack):
    _bootstrap(stack)
    requirement = triggers.required_consultation(
        'trial_admission', 'candidate-same-id', initiator_persona_id=INITIATOR,
        target_version='2', candidate_digest='b' * 64,
    )
    with _provider_fake({INITIATOR: 'approve', triggers.RISK_PERSONA_ID: 'reject'}) as fake:
        cfg = _config(stack, f'http://127.0.0.1:{fake.server_address[1]}/contribute')
        ConsultationServiceClient(
            base_url=stack['api'], token=SERVICE_TOKEN, tenant_id=TENANT,
        ).open_persona_panel(requirement, persona_id=INITIATOR, trace_id='review-v2')
        result = run_tick(config=cfg, state=WorkflowStateStore(cfg.state_path), provider=_provider(cfg))
        assert result['completed'] == 1, result
    stored_decision = stack['handoffs'].list_all()[0]['gated_decision']
    _propose_and_review(stack, 'old-v1', 'candidate-same-id')
    old_status, old_body = _decide(stack, 'old-v1', actor='operator-v1', roles=['governance_reviewer', 'operator'])
    assert old_status == 200, old_body
    hold_status = stack['handoffs'].list_all()[0]['operator_decision']['status']
    status, body = _gov_post(stack, '', {
        'decision_id': 'new-v2', 'expected_version': 0, 'target_type': 'registry_entry',
        'target_id': 'candidate-same-id', 'target_version': '2', 'tenant_id': TENANT,
        'owner_user_id': 'proposer-1', 'risk_level': 'low', 'candidate_digest': 'b' * 64,
        'expires_at': '2099-01-01T00:00:00Z',
    }, 'proposer-1', ['approval_proposer'])
    assert status == 201, body
    status, body = _gov_post(stack, '/new-v2/review', {
        'expected_version': 1, 'actor_role': 'governance_reviewer', 'actor_id': 'rev-1',
    }, 'rev-1', ['governance_reviewer'])
    assert status == 200, body
    status, body = _decide(stack, 'new-v2', actor='auto-gate', roles=['automated_gate'], actor_role='automated_gate')

    assert status == 409, 'v2 risk veto was cleared by a v1 operator approval'

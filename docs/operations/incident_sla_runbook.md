# Incident Escalation SLA Runbook

Task: `TEL-HARD-003-V2`

## Scope

High and critical `IncidentCase` records must not close until the escalation
chain is complete:

1. The incident has a published `Postmortem` within the configured
   incident-to-postmortem SLA.
2. The postmortem has an `EvolutionDecisionProposal` within the configured
   postmortem-to-proposal SLA.
3. The proposal action matches the severity policy.

The gate is implemented in `services/incident/escalation_sla.py` as a pure
evaluation helper. It does not write to the incident store or governance store.

## Default Policy

| Severity | Postmortem SLA | Proposal SLA | Allowed proposal action |
|---|---:|---:|---|
| `high` | 24 hours | 24 hours | `rollback` |
| `critical` | 24 hours | 24 hours | `freeze`, `rollback` |

`low` and `medium` incidents are not escalation-SLA gated by default.

## Closure Gate

Use `evaluate_escalation_sla()` before closing a high or critical incident.
The returned `EscalationSlaEvaluation.closure_allowed` must be true.

Use `assert_incident_closure_allowed()` when the caller should fail closed with
an exception. A missing postmortem, unpublished postmortem, missing proposal,
late artifact, wrong source link, missing timestamp, or disallowed action
blocks closure.

## Configuring SLA Values

Instantiate `EscalationSlaConfig` with task-specific thresholds:

```python
config = EscalationSlaConfig(
    postmortem_sla_hours=12,
    proposal_sla_hours=6,
)
```

The action policy is configurable through `allowed_proposal_actions` when an
operator-approved runbook needs a narrower or wider incident response path.

## Operational Notes

- `Postmortem.status` must be `published` and `published_at` must be present.
- Proposal timestamps use `created_at` or `proposed_at`.
- Proposal linkage must match both `source_postmortem_id` and
  `source_incident_id`.
- Critical incidents may satisfy the gate with a freeze proposal or a rollback
  proposal. Follow-through execution remains owned by governance/evolution
  controllers.
- SLA breaches are evidence, not auto-remediation. Record the breach and keep
  the incident open until a reviewer decides the corrective path.

## Verification

Focused test:

```bash
python3 -m pytest tests/incident/test_escalation_sla.py -q
```

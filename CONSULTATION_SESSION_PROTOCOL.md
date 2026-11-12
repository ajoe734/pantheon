# Consultation Session Protocol

Status: source contract for consult-policy persona panels
Task: CONSULT-POLICY-PERSONA-PANEL-20261112 · Design: SD_PERSONA_CYCLE_RECONNECT §6.4 / D6 / D7 / D8
Code: `services/persona/consult_triggers.py`, `services/consultation/workflow_executor.py`
(`_execute_panel_claim`), `services/consultation/main.py` (`_authorize_panel_open`),
`services/consultation/client.py` (`open_persona_panel`)

This document records the session shape that the code enforces. Consultation is
**advisory**: a panel never grants deployment, broker, capital or approval authority.
The best gate outcome is `adopt_eligible`, which still needs the owning gate's own
approval.

## 1. Trigger (consult policy v1, `consult-policy-v1`)

`required_consultation(decision_type, decision_id, initiator_persona_id, touches)`:

| Gated decision | Required participants |
|---|---|
| `trial_admission`, `envelope_publication` | initiator + risk persona |
| `harness_patch` (every patch) | initiator + risk persona |
| `harness_patch` touching `guardrails` or `consult_policy` | initiator + risk persona + red team persona |

Any other decision type is ungated (`None`). The result is stored on the request as
`metadata.panel` (`gated_decision`, `initiator_persona_id`, `participants`, `touches`).

## 2. Opening a session

A persona opens a panel **as a service caller carrying its own persona id**:
service token + `X-Pantheon-Service-Actor: persona:<persona_id>`, `requested_by =
{persona, <persona_id>}`, `from_persona_id = <persona_id>`, `panel.initiator_persona_id =
<persona_id>`. Any mismatch is `403`. The service re-derives the policy requirement and
refuses (`422`) a panel missing a required participant. The executor repeats both checks,
so a panel cannot be under-staffed after creation.

## 3. Participants and rounds

* **Participants** are real personas: initiator (`primary_reviewer`), risk persona
  (`risk_reviewer`, may veto), red team persona (`red_team`, when required). The synthesis
  is authored by a `committee` participant `consult-panel:<request_id>` (role `observer`).
* **Rounds:** one independent round. Each participant is asked separately, through the
  provider boundary, as its own agent. The ask carries the request and that participant's
  own id/role only — **never another participant's answer**. The provider idempotency key is
  scoped per participant. The returned contribution must be `persona` type and
  `participant_ref` must equal the asked persona, with a unique `contribution_id`; otherwise
  the claim blocks. Each answer is persisted durably before the next is asked, so a retry
  resumes instead of re-asking.
* Each participant's answer is recorded as its own transcript event and evidence
  attachments (attached by that persona).

## 4. Synthesis (no vote)

The executor writes exactly one `committee_summary` memo listing each persona's answer,
**Agreements** and **Disagreements**, and the gate outcome. There is no vote and no
majority rule; disagreement is reported, not resolved. A `panel_synthesis` transcript event
records `voting: false` and the gate result. The memo is published and linked to the gated
decision through the request target (`target_type`/`target_id` = decision type/id) and the
governance handoff (`consultation.panel.<decision_type>.reviewed`, evidence refs include
`consultation://memos/<memo_id>`).

## 5. Veto

`evaluate_gate` applies the **risk persona's** answer only:

* `reject` / `freeze` / `rollback` → **oppose**: `operator_decision`, `vetoed_by=<risk id>`,
  memo recommendation `escalate`, finding `risk_veto`.
* Any other non-support answer (`escalate`, `request_more_research`) or a missing required
  answer → `operator_decision` (fail closed).
* `approve` / `approve_with_conditions` → `adopt_eligible`.

The gate handoff (the hold) is persisted and dispatched before the memory writeback, and the
hold is bound to the gated decision's mapped approval target type, id and (when bound) version.

The veto holds in paper too: an oppose is never auto-adopted anywhere; it is routed to an
operator. Red team opposition is reported as a disagreement but does not veto.

## 6. Memory writeback

After the memo is published (and before handoff dispatch) the executor posts one
idempotent Learn-feedback writeback (`source_event_type=consultation_closed`,
`write_authority=consultation-svc`, `source_event_id=consultation:<request_id>`) to the
memory service. It writes a `consultation_outcome` to the **initiator's** persona memory and
an institutional `cross_persona_observation`. Without a configured memory address the claim
blocks; it never skips silently.

## 7. Sponsor certification

A published panel memo is evidence, not a sponsor decision. Sponsor certification of a
consultation (committee sponsor decision) remains the existing
`POST /api/consult/committees/{committee_id}/sponsor-decision` flow
(`sponsor_decision_bridge.py`). An `operator_decision` gate result is resolved only by an
operator through that path; the panel, its memo and its memory entries never stand in for it.

## 8. Deployed addresses

`consultation-svc` is configured with governance (`CONSULTATION_HANDOFF_SINK_URL`), memory
(`PANTHEON_MEMORY_API_URL`, `PANTHEON_MEMORY_AUTH_TOKEN[_FILE]`) and evolution
(`PANTHEON_EVOLUTION_API_URL`, probed for `harness_patch` panels). Evolution is not a
`depends_on` of consultation-svc because it transitively depends on it.

## 9. Panel personas

`bootstrap_consult_personas` idempotently creates `persona-risk-officer` and
`persona-red-team` through `POST /api/personas` in `draft` with `metadata.trading=false`,
`consultable=true`. Advancing their lifecycle to `consultable` is a governed step and is not
automated here.

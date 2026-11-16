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
  provider boundary, as a tenant-and-persona-bound consultation-only agent (deny-all tools,
  verified live before dispatch; never `main` or the persona's trading agent) that sees only
  committee-visible memory (`persona_and_committee` and institutional; never `persona_private`). The ask carries the request and that participant's
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

The gate handoff (the hold) is persisted and dispatched and acknowledged before the memory
writeback (memory follows the acknowledged handoff), and the
hold is bound to the gated decision's mapped approval target type, id and (when bound) version.

The veto holds in paper too: an oppose is never auto-adopted anywhere; it is routed to an
operator. Red team opposition is reported as a disagreement but does not veto.

## 6. Memory writeback

After the memo is published and the gate handoff is acknowledged the executor posts one
idempotent Learn-feedback writeback (`source_event_type=consultation_closed`,
`write_authority=consultation-svc`, `source_event_id=consultation:<request_id>`) to the
memory service. It writes a `consultation_outcome` to the **initiator's** persona memory and
an institutional `cross_persona_observation`. Without a configured memory address the claim
blocks; it never skips silently.

## 7. Sponsor certification

A published panel memo is evidence, not a sponsor decision. A risk-persona veto
(`operator_decision` gate result) holds the gated approval at governance and is resolved only
through the governance approvals `decide` command by a human operator whose approval target
type, id, `target_version` and `candidate_digest` match the bound decision; the panel, its
memo and its memory entries never stand in for it. Committee sponsor decisions
(`POST /api/consult/committees/{committee_id}/sponsor-decision`) are a separate flow and do
not resolve this veto.

## 8. Deployed addresses

`consultation-svc` is configured with governance (`CONSULTATION_HANDOFF_SINK_URL`), memory
(`PANTHEON_MEMORY_API_URL`, `PANTHEON_MEMORY_AUTH_TOKEN[_FILE]`) and evolution
(`PANTHEON_EVOLUTION_API_URL`, probed for `harness_patch` panels). Evolution is not a
`depends_on` of consultation-svc because it transitively depends on it.

## 9. Panel personas

The existing Persona service lifespan runs `bootstrap_consult_personas` through
its local persistent owner's normal create/CAS path before serving HTTP. It
creates `persona-risk-officer` and `persona-red-team` for
`PERSONA_DEFAULT_TENANT_ID` (fallback `PANTHEON_TENANT_ID`), with policy
`consult-panel.non-trading`. Compose binds this tenant to the same deployment
tenant as the consultation executor. Restart and concurrent replicas reuse the
same identities; a conflicting tenant or unsafe existing persona blocks bootstrap.
A partial record's consultation policy/metadata can be repaired through the owner
patch path; trading policies and tenant/lifecycle changes are never repaired automatically.

These personas remain **draft**, with `metadata.persona_class=consult_panel`,
`trading=false` and `consultable=true`. Consultation eligibility comes from the
panel policy, without lifecycle promotion. The owner refuses trading policies,
class removal, trading metadata and lifecycle advancement for panel identities.
Drafts are excluded from daily-cycle registration and paper admission; the fleet
classifier explicitly excludes `consult_panel` from quarterly trading ranking,
so no ranking-derived paper allocation or canary nomination is produced.

Before asking anyone, the consultation executor ensures **every** required
participant, including the initiating trading persona, through authenticated
`POST /api/openclaw-adapter/consultation/agents/ensure`. The body contains
`tenant_id` and `persona_id`; the existing consultation provider bearer token,
allowed service actor and matching `X-Pantheon-Tenant-Id` are required. The adapter
reads the real tenant-bound Persona owner record before any Gateway mutation.
It requires `PANTHEON_PERSONA_API_URL` and the existing Persona service token
(`PANTHEON_PERSONA_SERVICE_TOKEN` or its `_FILE` setting).

Each consultation agent has the exact admission ID
`consult-<sha256(tenant + NUL + persona)[:12]>-<persona>` and its own
`<gateway-state>/workspaces/<agent-id>` workspace. It has deny-all tools,
exec security deny/ask always, empty skills, disabled memory search and no context
injection. Gateway `config.patch` changes only this ID, guarded by `baseHash` and
the shared registry lock. Explicit array replacement paths allow removal of old
tools/skills during repair. No trading/default agent or private memory workspace
is written. The unchanged live admission check runs again before each contribution;
an agent with tools remains inadmissible. The Gateway is pinned at 2026.7.1;
its [config patch handler](https://github.com/openclaw/openclaw/blob/v2026.7.1/src/gateway/server-methods/config.ts)
and [ID-keyed merge contract](https://github.com/openclaw/openclaw/blob/v2026.7.1/src/config/merge-patch.ts)
define revision fencing and targeted agent patches.

Read startup status at `GET /healthz`, `details.panel_personas`: `ready` includes
per-persona `created`, `exists` or `repaired`; `blocked` includes a reason.
The owner retries a blocked bootstrap every 30 seconds, without changing lifecycle.
The ensure route returns `ready`, exact `agent_id`, `workspace_ref` and `changed`;
it revalidates live admission and workspace even on an unchanged attempt.
Failures return a typed retryable `blocked` envelope. The executor records
`panel_agent_provisioning_blocked` with a readable reason on the consultation
transcript and preserves a retryable workflow hold, renewing its lease during
provisioning. It repeats ensure on the next attempt rather than caching failure
or automatically adopting the gated decision. Contribution/adoption gates and
operator authority are unchanged. Hosted deployment proof belongs to
`PERSONA-CYCLE-RECONNECT-HOSTED-ACCEPTANCE-20261112` AC3.

## 10. Deliberative persona meetings (`persona_meeting`)

Task: `MEETING-PROTOCOL-ENGINE-R2-20261116` · Design: `SD_PERSONA_MEETINGS_2026-11-15.md` (§5–§12, amended by `SD-PERSONA-MEETINGS-AMEND-20261115`)
Code: `services/consultation/meetings.py`, `services/consultation/meeting_catalogue.json`,
`services/consultation/main.py` (`/api/consult/meetings*`), `services/consultation/client.py` (`open_persona_meeting`),
`services/consultation/workflow_executor.py` (`_execute_meeting_claim`),
`services/openclaw-gateway-adapter/consultation_provider.py` (`_validate_meeting_turn`, `_meeting_persona_prompt`)

### 10.1 Two consultation classes (M1)

1. **Gate panels (`persona_panel`)**: `trial_admission`, `envelope_publication`, `harness_patch`, and `paper_canary_candidacy` keep Sections 1–9 unchanged: one independent round, risk veto.
2. **Deliberative meetings (`persona_meeting`)**: never gate anything and execute nothing directly. Proposals are routed through existing governed entry points under a trading-persona sponsor's identity (or Agora's candidate decision path when unsponsored).

### 10.2 Catalogue (`services/consultation/meeting_catalogue.json`, M2 & §12.6)

The consultation service accepts `persona_meeting` only for the six amended catalogue types and refuses all others (including the retired `post_mortem` type):

| Code | Slug | Purpose | Cadence | Cross-persona |
|---|---|---|---|---|
| `MT-1` | `pool_evolution_review` | Pool evolution direction: mutate, retrain, replace, or research gap | weekly | false |
| `MT-2` | `optimisation_review` | Harness patches, pending lessons, and expression counterfactuals | event_driven | false |
| `MT-3` | `strategy_ideation` | Turn research findings and hypothesis cards into sponsored demands | weekly | true (≥2 trading personas) |
| `MT-4` | `performance_attribution_review` | Outlier and disagreement performance attribution review (§12.6) | weekly_and_event | false |
| `MT-5` | `regime_interpretation` | Exchange interpretations of governed `RegimeState` transitions | event_driven | true (≥2 trading personas) |
| `MT-6` | `quarterly_review` | Quarterly review before paper allocation and canary nomination | quarterly | true (≥2 trading personas) |

### 10.3 Rosters, Agora eligibility, and private servants (M7, §11.2, §12.8)

* **Institutional personas**: enrolled trading personas (`origin=institutional`, `can_sponsor=true`) plus `persona-risk-officer` (`role=risk_reviewer`) and `persona-red-team` (`role=red_team`, `can_sponsor=false`).
* **Agora advisors**: (`origin=agora_advisor`, `can_sponsor=false`) must pass the existing operational Agora eligibility (`lifecycle_state` in `OPERATIONAL_LIFECYCLE_STATES`, not `suspended`, advisory ceiling) and must not be `user_private`.
* **Private servants**: (`origin=private_servant`, `can_sponsor=false`, bound to `agora_user_id`) attend **only**:
  1. a meeting convened by their owning `agora_user`, or
  2. an `MT-4` (`performance_attribution_review`) meeting whose subject is their own trader, regardless of who convened it.
  Attendance in any other meeting is refused (`private_servant_not_allowed`); a missing required servant fails closed (`servant_absent`).
* **Sponsors**: advisors, private servants, risk, and red-team personas cannot sponsor proposals.

### 10.4 Pre-read, rounds, anonymisation, and citation guard (M3)

* **Pre-read**: shared case file of facts plus each participant's own committee-visible memory entries in full (`persona_and_committee` and `institutional`, up to 12 items; `persona_private` is strictly forbidden) until the H10 interpretation turn (`PANEL-FULL-INTERPRETATIONS-R2-20261114`) is active.
* **Round 1 (independent)**: each participant answers from its own deny-all consultation agent (`consult-<hash>-<persona>`) with `stance`, `confidence` (0–1), and `citations`. No participant sees another participant's answer in Round 1.
* **Round 2 (anonymised cross-visibility)**: each participant sees every valid Round 1 position labelled by `role` and `style` only (`persona_id` is stripped and forbidden at the adapter boundary), and states what it `accepted`, what it `disputed`, and whether its position `changed`.
* **Round 3 (evidence-gated)**: runs only when the chair records `new_evidence` during the meeting.
* **Citation guard**: any claim or position citing an item outside the shared case file or that participant's own pre-read is rejected (`meeting_position_rejected`).

### 10.5 Chair synthesis, MT-4 causal hypothesis rule, and dissent memo (M4 & §12.6)

* **No vote**: the chair synthesizes `proposals`, a non-binding confidence-weighted `tally` (`voting: false`), and `outcome`. A `dissent` memo (`MemoType.DISSENT`) is published and linked via `synthesis.dissent_memo_id`.
* **Unreadable or empty synthesis**: recorded as `outcome = "REVIEW"` with `proposals = []`.
* **MT-4 (`performance_attribution_review`)**: carries `attribution_record` in the case file; `persona-red-team` is assigned `luck_or_small_sample_or_confounder`; the synthesis accepts **only** testable causal hypotheses naming both the explained `component` and empirical `test` (routed as `research_demand`, never direct lessons or rules), and otherwise records `outcome = "no_testable_hypothesis"`.

### 10.6 Budgets, memory writebacks, C1 routes, and C2 output routing (M5, M6, M9)

* **Budgets**: configured in `meeting_catalogue.json` (`max_participants=5`, `max_rounds=2`, `max_rounds_with_evidence=3`, `max_meetings_per_persona_per_week=3`, `max_calls_per_meeting=16`). Violations and cross-persona meetings with `<2` trading personas (`insufficient_participants`) are refused and recorded in `catalogue.refusals`.
* **Memory writeback**: posts one `consultation_closed` writeback to `/api/memory/writebacks/learn-feedback` (`write_authority=consultation-svc`), writing shared minutes once as an institutional fact and each participant's outcome to its own persona memory (`writebacks: [{persona_id, entry_id}]`).
* **C1 routes**: `POST /api/consult/meetings` (202, idempotent by `Idempotency-Key`), `GET /api/consult/meetings`, `GET /api/consult/meetings/{meeting_id}` (404 if unknown), `GET /api/consult/meetings/proposals`, and `GET /api/consult/meetings/catalogue`.
* **C2 output routing**: after storing synthesis, calls `dispatch_proposals(meeting)` and reads live status via `read_downstream_status(proposal_id)` in `services/consultation/meeting_outputs.py`.


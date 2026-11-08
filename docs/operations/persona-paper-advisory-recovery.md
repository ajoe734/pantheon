# Persona Paper advisory admission recovery

Existing Management Paper Personas provisioned before this change have no
capability snapshot and no explicit `environment_ceiling`, so Agora Talk
rejects them with `capability_snapshot_unavailable, environment_ceiling_exceeded`.

## Operation

`POST /bff/personas/{persona_id}/provisioning/advisory-admission`
(operator role; tenant derived from the caller identity; no body).

It writes through the Persona owner only:
1. an immutable snapshot `cap-advisory-{persona_id}` with exactly `["persona_opinion"]`;
2. metadata `capability_snapshot_id` and `environment_ceiling="paper"`.

Idempotent: repeats return the same snapshot and perform no extra write.
No order, capital, broker or runtime authority is granted.

## Refusals
- 404: Persona absent or in another tenant.
- 409 `persona_not_active`: retired / suspended / archived / frozen / draft.
- 409 `persona_not_paper_running`: not a reconciled Paper-running Persona.
- 409 `environment_ceiling_exceeds_paper_advisory`: an existing higher ceiling is never rewritten.
- 409 `capability_owner_rejected: ...`: owner conflict/unavailable.

## Hosted use (existing publisher, after release)
Call once per Persona (青禾 R2 persona-be19a1fac471247f2b2a, 澄川
persona-d9363cffc0efbbbcbeb9, 衡岳 persona-9d4fb3f44cbc5e7cb84f) as
operator_a / tenant-dev, then read back via the Agora eligibility endpoint and
confirm `eligible: true` with `participant_snapshot`. Source delivery only;
hosted completion and provider-response proof are separate.

## Bounded Operating Evidence for Selected-Persona Talk / Challenge / Reflect

When a selected Persona is invoked via `/bff/agora/interactions` (Modes `ask`, `challenge`, `compare`, `propose_action`, `reflect`), the BFF hydrates and freezes an immutable `agora.persona-operating-context.v1` payload (`operating_context`) before OpenClaw invocation:
1. **Strict Scope & Immutable Readback:** Operating evidence is hydrated strictly for `(tenant_id, persona_id)` at interaction submission (`status="queued"`) and frozen into `_frozen_personas[i]["operating_context"]`, `_operating_context[persona_id]`, the `interaction_queued` timeline outbox event, and `context_snapshot` (`position_risk_snapshot_refs`, `journal_ref`, `context_refs`).
2. **Authoritative Surfaces & Coverage Semantics:**
   - `lifecycle_and_execution`: separates governance lifecycle maturity (`lifecycle_maturity`, e.g. `research_only`) from current execution mode (`execution_mode`, e.g. `paper`) and operational state (`operational_state`, e.g. `paper_running`).
   - `runtime_binding`: bound runtime binding ID, plan ID, capital pool ID, and health status.
   - `strategy_parameters`: populated (`status="bound_artifact_verified"`) ONLY when read from the Persona's exact bound approved strategy artifact (`read_registry_strategy_artifact_detail` / `get_strategy_artifact` / loader attestation). Otherwise reported as `status="not_provided"` with explicit guidance never to infer or invent MA/indicator parameters from `strategy_family`.
   - `telemetry_and_positions`: bounded positions (`<= 10`), `mark_price`, `mark_as_of`, `last_fill`, `total_trades`, `pnl`, `realized_pnl`, `unrealized_pnl`, `return_percent`, `drawdown`, `fill_rate`, and `not_provided_fields`.
   - `trade_journal`: bounded recent trade episodes (`<= 5`, ID lists `<= 20`) from `/api/telemetry/trade-episodes?persona_id={persona_id}` via `read_persona_operating_episodes`.
   - Missing or unavailable fields/surfaces are explicitly marked `not_provided` or `unavailable` (`missing_semantics="not_provided_or_unavailable_never_proves_zero_records"`).
3. **No Execution Authority:** Every frozen operating context and provider prompt carries `authority_boundary()` (`execution_authority="none"`).

## Management Readiness & Lifecycle Presentation (`GET /api/v1/operator/persona-management/{persona_id}`)

- `deploymentPlans` and `approvals` resolve bound `DeploymentPlan` and terminal/pending `ApprovalDecision` records across all authoritative links (`persona_id`/`sponsor_persona_id`, `plan_id` from active `RuntimeBinding`, `persona_capital_binding_id`, `runtime_binding_id`, `capital_pool_id`, `approval_decision_id`, and `strategy_artifact_id`/`registry_id`) with direct `get_deployment_plan` and `get_approval_decision` fallback reads and strict tenant filtering.
- `health` separates governance lifecycle maturity (`lifecycle_maturity="research_only"`) from current execution mode (`execution_mode="paper"`, `operational_state="paper_running"`). A `research_only` Persona with an active, healthy paper `RuntimeBinding` is not falsely degraded with `persona_lifecycle_not_active`, while `persona.lifecycle_state` remains `research_only` until formal governance promotion. Unreconciled or unhealthy Personas remain `degraded`, and `unknown` remains `unknown`.


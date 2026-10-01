# Persona Evaluator Agent

`services/persona-evaluator-agent` is the **only** persona recommendation
evaluator. It replaces the BFF's score-threshold recommendation rules
(`_pm12_recommendation_action_ids`, static rationale text), which were deleted in
the same delivery. The deterministic performance/risk/eligibility scores and
formulas are unchanged and are read-only evidence.

## Run (every `PERSONA_EVALUATOR_INTERVAL_SECONDS`, default 900)

1. Read the current quarter's ranking from the BFF
   (`GET /bff/management/quarterly-ranking`, bearer `PERSONA_EVALUATOR_BFF_TOKEN`):
   scores, tier, state, stage, eligibility, exclusion codes and evidence refs.
   Unavailable or empty evidence is a degraded run.
2. If a result is already saved for `(quarter, ranking_snapshot_id)`, reuse it:
   the provider is not asked again, so refresh/retry/restart read the same
   recommendation. Otherwise ask one agent through the openclaw gateway
   `/assistant/providers/openclaw/structured` route (data-only tool, every native
   tool denied) which of the existing actions each persona warrants. Output that
   names an unknown persona or action, has no rationale, or cites no evidence ref
   from that persona's own list is dropped, never repaired. There is no heuristic
   fallback when the provider is unavailable.
3. Persist the result (`PERSONA_EVALUATOR_STATE_PATH`, one JSON file on a volume)
   keyed by quarter + ranking snapshot, with persona id, snapshot id, run id,
   rationale and evidence ref ids. The last 20 snapshots per quarter are kept so a
   submit that replays an admitted snapshot still resolves.
4. For a *supported lifecycle* recommendation only, propose a governance
   `persona_lifecycle_transition` approval
   (`POST /api/governance/approvals`, `subject = {persona_id, from_state,
   to_state}`; `proposal_id` is the recommendation id and the content digest binds
   rationale + evidence). Supported: `freeze_persona` -> `frozen`,
   `retire_persona` -> `retired`, when the persona's current state allows that
   transition. Every other action (`promote_to_canary_candidate`,
   `increase_research_budget`, `grant_tool_access`, `reduce_capital_access`,
   `require_retraining`, `suspend_persona`) stays a persisted, non-executable
   advisory entry; nothing is reinterpreted as a lifecycle, budget, tool or capital
   write.

## Limits, dedupe, degraded runs

- At most 5 POST attempts per run (unknown outcomes count) and 20 per rolling
  hour (persisted; a slot and the exact request identity are reserved before the
  POST and kept when the outcome is unknown; later runs replay that identity
  even if the ranking snapshot changed).
- Deduplicated per persona and target state for 7 days, plus a deterministic
  decision id / `Idempotency-Key`, so a replay or a concurrent run merges (HTTP 409)
  instead of creating a second request.
- A degraded run (evidence, an unavailable ranking surface or source, missing
  refs, agent or output unavailable) is recorded in
  `last_run` and creates nothing.

## Authority

The agent holds a governance token (`PERSONA_EVALUATOR_GOVERNANCE_TOKEN`,
`approval_proposer` role, subject `PERSONA_EVALUATOR_ACTOR_ID`) and has no tool or
code path to decide, review, revoke or apply a request, or to change persona,
capital or runtime state. A human approves; lifecycle application stays with the
persona lifecycle owner tasks.

## Read surface

`GET /api/persona-evaluator/recommendations?quarter=YYYY-Qn[&snapshot_id=]`
(header `X-Pantheon-Service-Token: $PERSONA_EVALUATOR_READ_TOKEN`) returns the
saved result. The BFF (`pm12/evaluator_results.py`) projects it into
`/bff/management/quarterly-ranking/recommendations`, promotion reviews and the
Human Inbox; it never recomputes advice on GET and returns no recommendations when
nothing is saved.

## Dev acceptance (operator-run, not claimed by unit tests)

Provision the BFF read token and the governance proposer token, run one evaluator
pass against real persona evidence and the configured openclaw provider, refresh
the existing UI to see the saved recommendation, approve through the human gate
(a decision, not by the evaluator), and let the lifecycle owner tasks verify
application.

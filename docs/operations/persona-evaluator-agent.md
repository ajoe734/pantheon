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
   Read every page and verify the complete content-addressed snapshot identity;
   unavailable, empty, redacted/incomplete or changing evidence is a degraded run.
   Before asking the provider or reusing saved advice, admit that immutable snapshot
   through `services.rankings.store` into the existing Rankings table. Same-content
   replay preserves the first creation time; differing contents or evidence assertion
   digests conflict. `RANKING_STORE_DSN` (fallback `DATABASE_URL`) and
   `RANKING_STORE_TABLE` must identify the same table used by the BFF reader.
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
   rationale and evidence ref ids. The last 20 recommendation results per quarter
   are retained; immutable Rankings snapshots remain in the owner table.
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
- Deduplicated per persona and target state for 7 days when content matches.
  Proposal identity (`Idempotency-Key` and `decision_id`) deterministically binds
  persona, transition states, source snapshot, and content digest (rationale +
  evidence refs), so changed content never silently attaches to an old request.
- On verified same-content replay (HTTP 200/201) or HTTP 409 with matching readback
  (`GET /api/governance/approvals/{decision_id}` confirming identical tenant, target,
  target_type, target_version, and content digest), the request is verified (created if
  HTTP 201, deduped if HTTP 200 or 409 replay).
- Incomplete HTTP 201 with unverified or failed readback retains its hourly reservation
  and pending retry identity as an uncertain/possibly-created outcome.
- Content, version, and CAS conflicts without matching readback remain unresolved:
  non-creation is established, releasing the hourly reservation slot while preserving
  pending retry identity; no `governance_request` is published as accepted, and the run
  is recorded as visibly degraded (`status: degraded`).
- Evidence or snapshot admission failure is recorded in `last_run`; it never
  asks the provider or creates advice/proposals. A provider failure may leave its
  already-admitted evidence snapshot. Governance conflicts retain the existing
  pending proposal identity and are reported as degraded.

## Authority

The agent holds a governance token (`PERSONA_EVALUATOR_GOVERNANCE_TOKEN`,
`approval_proposer` role, subject `PERSONA_EVALUATOR_ACTOR_ID`) and has no tool or
code path to decide, review, revoke or apply a request, or to change persona,
capital or runtime state. A human approves; lifecycle application stays with the
persona lifecycle owner tasks.

On dev both tokens are short-lived principals minted by `dev-paper-principal-issuer`
(`persona-evaluator-agent` consumer: BFF `viewer` read, governance `approval_proposer`
with subject `persona-evaluator-agent`), refreshed hourly and revoked with the dev
paper grant. They are mounted read-only at `/run/pantheon-principals/` and read from
`PERSONA_EVALUATOR_{BFF,GOVERNANCE}_TOKEN_FILE` at the start of every run; the plain
env vars are only the fallback when no file path is set. A missing or empty file is a
degraded run that creates nothing.

## Read surface

`GET /api/persona-evaluator/recommendations?quarter=YYYY-Qn[&snapshot_id=]`
(header `X-Pantheon-Service-Token: $PERSONA_EVALUATOR_READ_TOKEN`) returns the
saved result. The BFF (`pm12/evaluator_results.py`) projects it into
`/bff/management/quarterly-ranking/recommendations`, promotion reviews and the
Human Inbox; it never recomputes advice on GET and returns no recommendations when
nothing is saved. A referenced snapshot that is missing or unreadable returns
HTTP 503, never a computed replacement. Ranking GETs only attach content IDs;
rolling diagnostics do not require durable admission. Quarterly evaluator inputs
are admitted by the scheduled owner before any recommendation is saved. The BFF
composition constructs `RankingReadStore` (no bootstrap or write methods). The
legacy injection keyword `ranking_write_owner` now carries only that reader.

## Dev acceptance (operator-run, not claimed by unit tests)

Provision the BFF read token and the governance proposer token, run one evaluator
pass against real persona evidence and the configured openclaw provider, refresh
the existing UI to see the saved recommendation, approve through the human gate
(a decision, not by the evaluator), and let the lifecycle owner tasks verify
application.

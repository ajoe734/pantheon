# Pantheon Management Console Operations Workflow Plan - 2026-07-07

Status: archived plan and fleet execution source of truth

Owner: Codex

Scope:

- `/management/persona-fleet`
- `/management/performance-attribution`
- `/management/capital`
- `/management/persona-league`
- `/management/quarterly-ranking`
- Human review and governed action surfaces used by those pages

This plan answers the operator question behind the 2026-07-07 screenshots:
how should the management console be used to monitor personas, diagnose
performance, decide what to do next, and execute changes without bypassing
human governance?

## Executive Summary

The current pages contain useful pieces, but they do not yet behave like one
operator workflow. Persona Fleet can show a running persona and a headline
performance summary. Performance Attribution can open from that persona, but
when canonical attribution and holdings data are missing it silently builds a
fallback row from the fleet summary and shows `nan` source rows. Portfolio and
ranking pages expose additional fragments, often with degraded or fixture-like
coverage. The result is that an operator can see numbers, but cannot reliably
answer:

1. Is this persona healthy and bound to the right runtime, ledger, pool, and
   strategy?
2. Did the reported PnL come from formal attribution, a portfolio book, a
   runtime summary, or a degraded fallback?
3. Which action is allowed now: observe, request review, pause, demote,
   promote, rebalance, or contain risk?
4. What evidence and human approval receipt prove that the action was safe?

The desired design is an operations loop:

```text
Portfolio Book -> Persona Fleet -> Performance Attribution -> Human Review
      |                |                    |                    |
      v                v                    v                    v
capital/risk     runtime health       causal evidence      governed action
```

Persona League and Quarterly Ranking should feed this loop as ranking and
governance inputs, not as separate competing sources of truth.

## Current State Audit

Audit timestamp: 2026-07-07 UTC.

Observed hosted frontend:

- `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/management/persona-fleet`
- `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/management/performance-attribution?dimension=persona&persona=persona-20260528-04688755...`

Observed focus persona:

- `persona-20260528-04688755`
- Display label: `Crypto-Alt-Hunter` / `Persona Fleet summary`
- Runtime: `runtime-crypto-paper`
- State: `paper_running`
- PnL contribution: `$48,000`
- Performance delta: `18.20%`
- Drawdown: `6.40%`
- Needs human: yes

### Persona Fleet

Persona Fleet is currently the most useful operator entry point. It shows
running paper personas, status, last mutation date, performance delta, and a
human-review hint.

What is correct:

- It can identify the focus persona and its runtime state.
- It exposes enough summary fields to start triage.
- It provides a review/action affordance.

What is incomplete:

- It does not clearly show data confidence: formal attribution, runtime
  fallback, fixture, degraded, or stale.
- It does not make runtime, ledger, capital pool, strategy, and artifact binding
  identity visible enough for operations.
- The performance link opens a page that looks more authoritative than the data
  actually supports.

Required role:

- Persona Fleet is the runtime/persona command center.
- It should answer who is running, who needs attention, what changed recently,
  which bindings are attached, and what the next safe action is.

### Performance Attribution

The screenshot from `績效歸因` is not the desired final behavior. It is useful as
an emergency fallback, but it should not present itself as normal formal
attribution.

Observed behavior:

- The page says it is focused on `persona-20260528-04688755`.
- It shows one top-level summary row with `$48,000`, `18.20%`, and `6.40%`.
- The source detail includes `persona-fleet.performanceSummary`.
- `portfolio-book.holdings` appears with `nan` metrics and no matching holding.
- The formal attribution endpoint does not provide a matching row for the focus
  persona.

Interpretation:

- The page is synthesizing a persona-level fallback from Persona Fleet because
  the canonical attribution and holdings sources do not match the selected
  persona.
- This is acceptable as a degraded diagnostic, but it is not acceptable as a
  normal attribution claim.

Required role:

- Performance Attribution is the causal drilldown page.
- It should distinguish formal attribution from fallback summary, missing
  source evidence, and stale/degraded data.
- It should show why the page can or cannot explain the PnL.

Minimum operator-safe behavior:

- Show a confidence banner: `formal`, `partial`, `fallback`, `degraded`, or
  `unavailable`.
- If attribution is synthesized from fleet summary, label it as fallback.
- Do not count fallback-only summary as a matched attribution row.
- Replace `nan` cells with explicit empty states such as `missing holdings
  source`, `not linked to selected persona`, or `source returned null`.
- Show the selected persona id, runtime id, period, source timestamps, and source
  status together.

### Portfolio Book And Capital Monitor

The portfolio/capital views should be the first page for account-level risk.
Current data is fragmented: the holdings source is degraded and only returns a
small smoke-like subset, while the focus persona has a runtime summary but no
matching holdings row.

Required role:

- Portfolio Book is the capital, exposure, and risk monitor.
- It should answer what money is allocated, where exposure sits, which runtime
  or persona owns it, and whether telemetry is current.

Required behavior:

- Normalize `snake_case` and `camelCase` BFF responses consistently.
- Join rows by stable identifiers: persona id, runtime id, pool id, sleeve id,
  strategy id, and artifact id.
- Show coverage: row count, runtime count, telemetry runtime count, stale rows,
  and degraded sources.
- Separate paper ledgers from canary sleeves and live capital pools.
- Show mismatches as first-class incidents instead of quietly dropping them.

### Persona League

Persona League should not be treated as the same thing as formal quarterly
governance. It is a short-cycle operations ranking and comparison surface.

Current issue:

- The frontend route can read status or readiness rows as if they were ranking
  rows.
- The ranking endpoint and the status endpoint do not currently provide one
  unified operator view model.

Required role:

- Persona League is the short-cycle ranking and comparison page.
- It should help operators compare active personas, identify candidates for
  review, and spot degraded or missing evidence.

Required behavior:

- Separate ranking rows from status/readiness summaries.
- Show ranking criteria, eligibility, evidence coverage, and exclusion reasons.
- Link each ranked persona to Persona Fleet, Attribution, and Human Review.
- Never imply promotion or allocation change without a governed review/action
  receipt.

### Quarterly Ranking

Quarterly Ranking is the slower governance cadence. It can include formal
rank/score rows, but current telemetry coverage can be empty or degraded.

Required role:

- Quarterly Ranking is the governance-cycle page for staged decisions.
- It should turn ranking evidence into review packets, not direct live changes.

Required behavior:

- Normalize backend field names before display.
- Show formal rank, score, period, eligibility, missing telemetry, and reasons.
- Provide links to attribution detail and the generated human review packet.
- Distinguish recommendation, submitted review, approved review, applied action,
  and rejected/expired decision.

## Desired Operator Workflow

### Daily Monitoring Loop

1. Start at Portfolio Book.
2. Check total capital, paper/canary/live separation, exposure, drawdown,
   runtime telemetry coverage, and degraded source count.
3. Drill into Persona Fleet filtered by risk, performance, last mutation, stale
   telemetry, or needs-human state.
4. Open Performance Attribution for any persona with material PnL, drawdown,
   suspicious performance, stale evidence, or pending review.
5. Submit or inspect Human Review only after source evidence is visible.

Acceptance:

- An operator can go from account risk to persona cause to governed action in
  three clicks or fewer from the primary table rows.
- Every page shows whether the data is formal, partial, fallback, degraded, or
  unavailable.

### Incident Triage Loop

1. Detect an anomaly on Portfolio Book or Persona Fleet.
2. Scope the anomaly by persona, runtime, pool, strategy, asset class, broker,
   and period.
3. Open Performance Attribution and confirm whether evidence is formal or
   fallback-only.
4. If evidence is missing, create a data-quality incident rather than an
   investment decision.
5. If risk containment is required, create a Human Review packet with the
   proposed action and evidence.

Acceptance:

- Missing attribution, missing holdings, stale telemetry, and binding mismatch
  are operator-visible incident states.
- Emergency actions can reduce or pause risk, but cannot promote a persona or
  increase allocation as a side effect.

### Governance Loop

1. Use Persona League for short-cycle ranking and candidate selection.
2. Use Quarterly Ranking for formal cycle decisions.
3. Generate a review packet with ranking evidence, attribution evidence,
   bindings, capital impact, and policy constraints.
4. Human Review records the decision.
5. The apply command writes an auditable receipt.
6. Persona Fleet and Portfolio Book show the applied result and link back to the
   receipt.

Acceptance:

- Recommendation, review submission, approval, apply, and receipt are separate
  states.
- No ranking page directly mutates capital.

## Read Model Contract

The management console needs one shared operations read model. Individual pages
can query different slices, but they must share identifiers, source status, and
data-confidence semantics.

Core identity fields:

- `persona_id`
- `persona_label`
- `stage`: `research`, `paper_ready`, `paper_running`, `canary`, `live`,
  `paused`, `retired`
- `runtime_ids`
- `paper_ledger_ids`
- `capital_pool_ids`
- `sleeve_ids`
- `strategy_ids`
- `artifact_ids`
- `broker_ids`
- `period`
- `as_of`

Performance fields:

- `pnl`
- `pnl_pct`
- `drawdown_pct`
- `risk_pct`
- `sharpe`
- `rank`
- `score`
- `performance_delta`
- `source_contribution`

Source status fields:

- `source_name`
- `source_status`: `ok`, `partial`, `degraded`, `unavailable`
- `source_freshness`
- `source_row_count`
- `source_error`
- `coverage_ratio`

Data confidence:

- `formal`: canonical source has a matching row and all required joins.
- `partial`: canonical source exists, but some optional evidence is missing.
- `fallback`: page synthesized a summary from another operator source.
- `degraded`: upstream source returned degraded data or fixture-like coverage.
- `unavailable`: source did not respond or cannot produce the requested slice.

Frontend display rules:

- Never render `nan` to operators.
- Never silently coerce `null`, `NaN`, or missing joins into zero.
- Fallback rows must be visually and textually distinct from formal rows.
- Counts must describe what they count: formal rows, fallback summaries, missing
  source diagnostics, or excluded rows.
- Each row should carry enough identity to open the related page without losing
  context.

## Action Model

The console should support operations, not just dashboards. Actions are allowed
only when their preconditions and governance states are explicit.

Action categories:

- Observe: no mutation, read-only drilldown.
- Request Review: creates a human review packet.
- Pause Paper Runtime: stops or pauses paper execution.
- Resume Paper Runtime: resumes after review or repair.
- Demote/Retire Persona: removes a persona from active ranking or runtime use.
- Promote Candidate: creates a recommendation or review packet only.
- Rebalance Proposal: creates a proposal, not a capital mutation.
- Apply Approved Rebalance: mutates after approval and records a receipt.
- Emergency Containment: reduces exposure or pauses runtime, never increases
  allocation or promotes.

Required preconditions:

- Persona identity and current stage are known.
- Runtime, ledger, and capital bindings are not ambiguous.
- Source confidence is visible.
- Policy gate result is present.
- Human review requirement is explicit.
- Apply commands are idempotent and audited.

## Page-Level Target State

### Persona Fleet Target State

Primary questions:

- Which personas are running?
- Which ones need attention?
- What is their stage, runtime, ledger, pool, and strategy binding?
- What performance changed recently?
- What action is safe now?

Required changes:

- Add data-confidence and source-status columns.
- Show binding identity compactly with drilldown links.
- Make the performance link carry persona id, runtime id, period, and source
  hints.
- Replace generic `needs human` chips with review state: `none`, `recommended`,
  `submitted`, `approved`, `applied`, `blocked`, or `expired`.
- Add row actions that create review packets instead of directly mutating
  capital.

### Performance Attribution Target State

Primary questions:

- What caused PnL and drawdown?
- Is this formal attribution or fallback?
- Which sources matched and which failed?
- What evidence supports a decision?

Required changes:

- Create a normalized attribution view model from formal attribution, portfolio
  holdings, runtime summary, and persona fleet summary.
- Add confidence banner and source coverage panel.
- Split rows into formal contribution, fallback summary, and diagnostics.
- Make missing holdings for the selected persona an explicit data-quality state.
- Keep the focus persona visible while switching dimensions.

### Portfolio Book Target State

Primary questions:

- Where is capital?
- Which personas and runtimes own exposure?
- Which holdings, pools, and sleeves are stale, missing, or mismatched?
- What risk needs containment?

Required changes:

- Normalize BFF response shapes.
- Join holdings to persona/runtime/pool/sleeve identity.
- Show coverage and staleness summaries at the top.
- Add filters for stage, broker, runtime, source status, and stale telemetry.
- Link risk incidents to attribution and human review.

### Persona League Target State

Primary questions:

- Which personas rank best in short-cycle operations?
- Which candidates are eligible for review?
- Which are excluded and why?

Required changes:

- Use a ranking-specific endpoint or adapter, not status rows.
- Show criteria and eligibility.
- Carry source confidence and evidence links.
- Submit recommendations to Human Review, never direct promotion.

### Quarterly Ranking Target State

Primary questions:

- Which personas are candidates for formal governance cycle decisions?
- Which evidence is complete enough for approval?
- What changed after the decision was applied?

Required changes:

- Normalize score/rank/performance fields.
- Show telemetry coverage and missing evidence.
- Generate review packets with traceable evidence.
- Show decision and apply receipt states.

## Implementation Waves

### Wave 0: Source Truth And Acceptance

Lock the page inventory, route ownership, live endpoint behavior, data source
coverage, and acceptance criteria. This prevents each page from fixing symptoms
with incompatible local fallbacks.

### Wave 1: Shared Operations Read Model

Implement BFF contracts and adapters for persona, capital, performance,
portfolio, ranking, source confidence, and action states.

### Wave 2: Page Integration

Update the frontend pages to consume normalized view models, show confidence and
diagnostics, and link the monitoring workflow end to end.

### Wave 3: Governed Actions

Wire review packet creation, approval state, apply receipts, emergency
containment, and route/action guardrails.

### Wave 4: Hosted Acceptance And Closeout

Publish dev, run hosted smoke tests, capture evidence for the focus persona, and
close residual risks.

## Global Acceptance Criteria

The work is complete only when a hosted operator can perform these flows:

1. Portfolio Book identifies capital/risk status, degraded source coverage, and
   paper/canary/live separation.
2. Persona Fleet identifies `persona-20260528-04688755`, its runtime, stage,
   performance, binding identity, and review/action state.
3. Clicking performance opens Performance Attribution for the same persona and
   clearly labels whether the view is formal attribution or fallback summary.
4. Missing holdings or formal attribution rows are shown as diagnostics, not
   `nan` metrics.
5. Persona League and Quarterly Ranking show rankings with evidence coverage and
   link into Human Review.
6. Any recommendation, promotion, rebalance, pause, or containment action creates
   or consumes a human review packet and records an auditable receipt when
   applied.
7. Tests cover BFF contracts, frontend adapters, route drilldowns, fallback
   labeling, `nan` suppression, and action guardrails.

## Non-Goals

- No direct live broker mutation.
- No promotion or capital increase without human approval and apply receipt.
- No fixture-only or fallback-only evidence may be labeled as formal
  attribution.
- No unrelated visual redesign beyond what is needed to make operations clear.

## Fleet Execution Packet

Execution tasks are archived at:

- `docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/INDEX.md`

Dispatch script:

- `scripts/dispatch_management_console_ops_workflow_2026-07-07.py`

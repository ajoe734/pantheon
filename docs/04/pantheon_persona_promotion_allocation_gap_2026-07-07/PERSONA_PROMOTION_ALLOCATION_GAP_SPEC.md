# Persona Promotion And Allocation Gap Spec - 2026-07-07

Status: follow-up gap spec and execution source of truth
Owner: Codex
Supersedes: none
Extends:

- `docs/04/pantheon_persona_promotion_governance_gap_2026-07-05/PERSONA_PROMOTION_GOVERNANCE_GAP_SPEC.md`
- `docs/04/pantheon_persona_promotion_governance_gap_2026-07-05/archive/PPL-GOV-007-PRODUCTION-CLOSEOUT-2026-07-05.md`

Scope: persona creation, paper runtime bootstrap, paper-to-real promotion,
real/canary ranking, capital sleeve binding, quarterly allocation weight
updates, emergency containment, and management page consolidation.

## Why This Follow-Up Exists

`PPL-GOV-*` closed the recommendation-to-human-review loop:

```text
recommendation -> submit -> promotion review -> human decision -> auditable receipt
```

That is necessary, but not sufficient for the product workflow operators need.
The management console must also answer these operator questions without
requiring tribal memory:

1. When I create a persona, is it immediately paper-running with all required
   paper trading bindings, or is it only a passive persona shell?
2. Where do I review paper personas for real-money eligibility?
3. Where do I approve paper/canary/live promotion?
4. Where do I see which persona owns which paper ledger, canary sleeve, or live
   capital sleeve?
5. Where do I review the quarterly real allocation ranking and proposed target
   weights?
6. How do I approve a rebalance, and how do emergency losses interrupt the
   quarterly cycle?
7. Which management pages are primary workflow surfaces, which are supporting
   detail pages, and which are legacy redirects?

The current UI has a single `Promotion & Allocation` entry, but the underlying
workflow is still split across generic registry pages, onboarding repair, Human
Inbox, capital pools, rebalance detail, readiness pages, and ranking diagnostics.
This follow-up makes the workflow explicit and executable.

## Required Operating Model

### Persona Creation

Creating a trading persona must create a runnable paper bundle, not just a
persona record:

```text
create persona
  -> persona state = paper_running
  -> isolated paper_ledger_id
  -> paper runtime binding
  -> paper deployment plan
  -> data source bindings
  -> mandate / strategy direction / risk preference
  -> first evaluation schedule
```

Creation is not allowed to leave the operator with a "draft persona" that
cannot run paper simulation unless the UI explicitly labels it as failed or
incomplete. `PersonaOnboarding` becomes setup repair / completion, not the
normal creation path.

Paper personas must not appear to share an ambiguous paper capital pool. A paper
persona uses an isolated ledger. If a portfolio parent or benchmark budget is
shown, the UI must still show the per-persona `paper_ledger_id`.

### Stage Model

The governed runtime stages are:

```text
paper_running -> canary_candidate -> canary_running -> live_candidate -> live_running
              \-> frozen / suspended / retired
```

Operators may say "paper to real"; the system must implement that as a governed
real-money entry path:

- paper to canary is the first real-money eligibility review;
- canary to live is the full live capital review;
- no direct paper-to-full-live mutation is allowed.

### Shared Competition, Stage-Aware Actions

Paper, canary, and live personas compete in the same league/ranking model. The
same score may mean different actions by stage:

| Stage | High-rank recommendation means | Human review target |
|---|---|---|
| `paper_running` | request real-money entry | paper-to-canary promotion review |
| `canary_running` | request full live entry | canary-to-live promotion review |
| `live_running` | request capital increase / retention | quarterly capital allocation review |
| any stage with hard risk breach | containment only | freeze, reduce, suspend, or retire |

Recommendation submit never changes live capital directly. It creates a review
or command authorization packet that a human must approve.

## Real Allocation And Weight Rules

Quarterly real allocation is separate from promotion approval. It applies only
to personas with canary/live eligibility and human-reviewed capital authority.

### Eligibility

A persona is excluded from positive real allocation if any condition is true:

- unresolved S1/S2 incident;
- hard risk breach;
- missing required evidence;
- reconciliation anomaly unresolved;
- runtime/broker/capital binding mismatch;
- sample below policy minimum for its stage;
- existing human review is blocked, rejected, or expired.

Excluded personas may still receive reduction, freeze, suspend, or retire
recommendations.

### Target Weight Formula

For every eligible canary/live persona, compute:

```text
rank_score =
  0.25 * pnl_score
+ 0.20 * sharpe_score
+ 0.15 * drawdown_control_score
+ 0.15 * execution_quality_score
+ 0.15 * risk_compliance_score
+ 0.05 * improvement_score
- 0.05 * human_intervention_penalty
- hard_penalty

capacity_adjusted_score =
  max(rank_score, 0)
  * capacity_factor
  * risk_budget_factor
  * evidence_confidence_factor
```

Normalize `capacity_adjusted_score` within the reviewed allocation universe to
derive target weights.

### Caps And Smoothing

The allocation engine must apply these caps before emitting a proposal:

- canary capital cap: <= 5% of the relevant real pool or sleeve budget unless a
  risk-owner override lowers the cap;
- live S tier max: 25%;
- live A tier max: 15%;
- live B tier max: 8%;
- watch/suspended/retired max: 0%;
- quarterly increase cap: +25% relative to current weight unless explicitly
  approved as an override;
- quarterly decrease may exceed the smoothing cap when reducing risk;
- any increase of live capital requires human approval;
- emergency containment may reduce/freeze immediately through the emergency
  review path but cannot promote or increase capital.

### Rebalance Proposal

Quarterly ranking must produce an auditable rebalance proposal, not a direct
mutation:

```text
real ranking snapshot
  -> target allocation set
  -> rebalance proposal
  -> simulation + constraints + risk check
  -> Human Inbox / governance approval
  -> apply rebalance command
  -> audit receipt and rollback target
```

Each line must show `persona_id`, `stage`, `capital_scope`, `pool_id` or
`sleeve_id`, `current_weight`, `target_weight`, `delta`, cap reason, and
evidence refs.

## Emergency Containment

Emergency actions interrupt the quarterly cycle. Triggers include:

- drawdown above hard threshold;
- daily loss above policy threshold;
- forced kill;
- broker/runtime/capital binding mismatch;
- reconciliation mismatch above threshold;
- unresolved S1/S2 incident;
- hard risk policy violation;
- missing or stale live telemetry for a capital-affecting persona.

Allowed emergency actions:

- freeze persona;
- reduce capital access;
- suspend persona;
- risk-off / flatten;
- rollback to previous allocation;
- retire persona after human-approved terminal action.

Forbidden emergency actions:

- promote paper to canary;
- promote canary to live;
- increase live allocation;
- bypass evidence/audit receipts.

## Management Page Inventory And Target State

| Surface | Target state | Required change |
|---|---|---|
| `/management/promotion-allocation` | Primary workflow page | Expand into a workbench with `Paper candidates`, `Real ranking`, `Quarterly capital`, and `Emergency actions` tabs. |
| `/management/persona-fleet` | Primary monitoring page | Show stage, paper ledger, runtime binding, real sleeve/pool, ranking link, and next governed action. |
| `/management/personas` | Registry plus create entry | Replace generic persona create drawer with Create Paper Persona flow. |
| `/management/personas/:id/onboarding` | Repair/completion only | Rename/copy as setup repair; use only for incomplete bundles or failed creation steps. |
| `/management/human-inbox` | Human approval queue | Add promotion/allocation filters and show stage target, capital impact, evidence, and decision status. |
| `/management/human-inbox/:id` | Decision detail | Approve / approve with conditions / reject promotion and allocation reviews with receipts. |
| `/management/capital` | Capital binding visibility | Show per-persona paper ledgers, canary sleeves, live sleeves/pools, allocation weights, and binding state. |
| `/management/capital/:id` | Capital detail | Show persona bindings and linked rebalances with current/target weights. |
| `/management/rebalance/:id` | Rebalance approval detail | Keep as canonical single-proposal review and apply/rollback surface. |
| `/management/ranking` | Formula diagnostics only | Do not use as promotion/allocation workflow; link to Promotion & Allocation for action. |
| `/management/readiness/capital-binding-live` | Readiness gate only | Do not present as allocation management; link back to capital/rebalance review. |
| `/management/persona-league` | Legacy route | Redirect to `/management/promotion-allocation?tab=real-ranking`. |
| `/management/quarterly-ranking` | Legacy route | Redirect to `/management/promotion-allocation?tab=paper-candidates`. |
| `/management/rebalance` and `/management/rebalances` | Legacy list routes | Redirect to `/management/promotion-allocation?tab=quarterly-capital`; detail route remains. |

## Required Product Surfaces

BFF:

- `POST /bff/management/personas/create-paper-bundle` or equivalent command
  that atomically creates persona + paper ledger + paper runtime binding +
  mandate/data-source/risk settings.
- Persona fleet rows expose `stage`, `paper_ledger_id`, `runtime_binding_id`,
  `capital_scope`, `capital_pool_id`, `capital_sleeve_id`, and next action.
- Capital pool/sleeve reads expose persona binding summaries and target/current
  weights.
- Promotion/allocation ranking reads expose stage-aware recommendations,
  eligibility, exclusions, and cap reasons.
- Rebalance proposal create/read/apply routes carry idempotency, approval
  references, simulation, constraints, rollback target, and audit refs.
- Emergency containment routes are role-gated and cannot emit promotion or
  allocation increase actions.

Frontend:

- Persona create starts the paper bundle flow and lands on a running paper
  persona or a setup-repair detail with exact failed step.
- Promotion & Allocation becomes the only operator action workbench for
  paper-to-real review and quarterly real allocation.
- Capital list/detail makes it impossible to confuse isolated paper ledgers
  with shared real capital pools.
- Human Inbox decision detail shows capital impact and cannot imply direct live
  mutation before approval/apply.
- Legacy pages either redirect or are clearly labeled diagnostics/supporting
  detail pages.

## Production Acceptance

This gap is complete only when:

1. A hosted dev create flow produces a `paper_running` persona with isolated
   `paper_ledger_id`, paper runtime binding, and visible data-source/risk
   settings.
2. Persona Fleet and Capital pages show distinct binding identities for
   paper ledgers and real sleeves/pools.
3. Paper candidates can submit promotion recommendations and land in Human
   Inbox with stage target and evidence.
4. Real ranking can produce a quarterly target allocation and rebalance
   proposal with current/target weights and cap reasons.
5. Human approval is required before applying any real capital increase.
6. Emergency containment can reduce/freeze/suspend outside the quarterly cycle
   and is proven unable to promote or increase capital.
7. Legacy/diagnostic pages no longer duplicate the primary workflow.
8. Pantheon and Execute Plans PRs are merged, deployed to dev where applicable,
   and validated with local tests plus hosted smoke evidence.
9. Final closeout records PR numbers, merge SHAs, deployed commits, validation
   commands, and residual risks.

## Execution Packet

Fleet execution tasks live at:

- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/INDEX.md`

Dispatch command:

```sh
python3 scripts/dispatch_persona_promotion_allocation_2026-07-07.py
python3 scripts/ai_status.py sync
```

# Pool member evolution

Task: `EVOLUTION-POOL-MEMBER-ACTIONS-20261112` · Owner: Codex2 · Reviewer: Claude

The existing evolution consumer tick now reads paper personas' pool records,
their approved registry baselines and their own virtual books. Automatic retire
removes a member and publishes the new immutable envelope. The automatic path
has no persona lifecycle writer. An empty pool publishes an explicitly pinned
empty envelope whose view is FLAT; the persona and runtime remain running.

The tick reconciles publication before scheduling, including publication left
unfinished by research trial admission. Observed member trading dates drive
20-day revalidation and ISO-week mutate/retrain proposals. Deterministic IDs
survive pool-version changes. The existing proposal API still enforces active
decision and cooldown rules. Missing baselines, observations or owner authority
are recorded and fail closed.

Revalidation replays only post-admission governed bars on the member's own
share-sized gate basis. It uses the existing decay-policy Sharpe floor and
drawdown ceiling. It requires 20 fresh trading dates and enough warmup for a
defined metric; unavailable data is an evaluation rejection, never a removal.
Original candidate admission still requires the unchanged OOS gate and its
registry-owned proof. Periodic monitoring does not require another two years
inside a 20-day window.

Weekly mutation evaluates up to the requested 50 structural children; retraining
uses a 50-variant search. The shared lifetime family counter is read strictly;
500 trials means `tried` and stops further variant generation. A built child
rejected by the gate before numerical scoring still counts as a trial. Passing children
obtain ordinary low-risk registry approval and join the same pool as zero-weight
trials through `evolution_trial`. Pool history records persona admission trials.

`evolution_compete` independently verifies both approved baselines, parent
lineage, checksums, aligned virtual-book dates and at least 60 new trading-return
observations after child admission. The child must also pass the existing decay-policy floor/ceiling on its fresh
gate-basis metrics. Only a passing, stronger child becomes active with the
parent's weight; otherwise the child is removed. Removed members stay in history.
All applied changes publish through the existing BFF publisher and its existing
Registry/Governance/Deployment path.

Virtual-book snapshots now expose their observed dates and source references.
Book lookup and baseline resolution use the pool member registry reference
(`member_id`), preserving registry aliases independently of embedded artifact IDs.
The default runtime book path is scoped by tenant/persona, with locked writes
and exact immutable-member history carryover across binding/envelope changes.
Explicit lifecycle/performance paths keep their existing isolation; deployments
using those overrides must preserve their book file across replacements.

## Configuration

The existing `EVOLUTION_AUTO_CONSUMER_ENABLED` switch and existing worker process
remain the entry point. Both evolution and research need the configured owner
URLs and credentials used by `PoolOwner`:

- `AUTO_CONSUMER_PERSONA_URL/TOKEN`, `CAPITAL_URL/TOKEN`, `REGISTRY_URL/TOKEN`.
- `AUTO_CONSUMER_TELEMETRY_URL/TOKEN`, `RESEARCH_URL/TOKEN`, `BFF_URL/TOKEN`.
- `AUTO_CONSUMER_GOVERNANCE_URL`, `PROPOSER_TOKEN/ID`, `GATE_TOKEN/ID`.

Every abbreviated name above has the `AUTO_CONSUMER_` prefix. Proposer and gate
remain distinct existing authenticated principals. BFF publication retains its
existing operator authorization. No issuer, grant, credential or permission is
created here. Research continues to load datasets through its governed
evaluation-authority loader. Missing configuration fails closed.

## Evidence boundaries

`evidence.json` maps acceptance to tests and exact completed validation batches.
The orchestration integration uses unmodified recorded TWSE STOCK_DAY prices
from `pool_admission_tw_recorded.json` through the real tick, Evolution proposal
API, Governance request/decision flow, dispatch adapter, research orchestrator,
50-child mutation execution, pool store and 60-day competition.

Its HTTP owners, baseline attestations, member views and selected gate outcomes
are explicitly controlled test ports. It proves orchestration and refusal
boundaries, not profitable mutation, a genuine approval of those test children,
or a hosted natural event. Genuine recorded baseline proofs, real post-admission
replay, real retrain/OOS refusal, and the mounted publisher/coordinator/dispatch
path are covered separately. There is no hosted deployment or live-capital
evidence in this task.

## Review resumption

Human/Ops admitted all 14 requested exact paths through the governed artifact
contract on 2026-11-12 and reopened the same task for Codex2, retaining Claude
as reviewer and all seven acceptance criteria. The earlier contract revision
request is retained as history with its resolution recorded. Source head
`df618258d86979eeda3742db78682f3a30046fc1` already contains current dev base
`caec3f40727c6bf7dd73c710625deff81b5fd4eb`; resumption refreshes validation and
the review manifest without changing the implementation contract. Independent
review and the canonical integrator remain required before owner closeout.

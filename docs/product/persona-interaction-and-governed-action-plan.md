# Persona Interaction And Governed Action Plan

Date: 2026-07-12  
Status: execution-ready planning baseline  
Repos: `ajoe734/pantheon`, `ajoe734/execute-plans`

## 1. Outcome

Pantheon must give an operator one coherent path to ask one or more Personas to
analyse, challenge, debate, and propose changes to a trading decision without
confusing conversation with execution authority.

The target loop is:

```text
select context
  -> select interaction mode and participants
  -> preserve independent Persona opinions
  -> expose agreement, disagreement, evidence, and uncertainty
  -> create a governed proposal
  -> research/backtest/validate
  -> human decision
  -> paper/canary/live governance
  -> Trade Journal outcome review
  -> reviewed learning or mutation candidate
```

Agora Strategy Workshop is the canonical conversation surface. Trading Room,
Performance, Trade Journal, and Persona Detail are contextual entry points into
that same workshop model, not separate chat systems.

## 2. Current Product Truth

This plan builds on current implementation and active work rather than treating
the product as greenfield.

- `/agora/strategy-workshop/:workshopId` already has a Servant composer,
  workshop cards, SSE refresh, research, backtest, consultation, patch, and
  readiness semantics.
- Consultation result cards already expose participants, consensus,
  disagreements, and risk notes, but participant selection and interaction
  intent are not explicit operator controls.
- The top-right Servant drawer currently displays workshop context only. It is
  not the primary conversation composer.
- Trading Room already supports `approve`, `reject`, `defer`, and `modify` for
  decision events. These controls must remain decision controls, not become an
  implicit free-form Persona chat.
- Persona Detail already manages identity, workspace, capital binding, strategy
  ownership, route policy, permissions, memory/training, evaluation, versions,
  test, restriction, suspension, and retirement. It is an entry point, not a
  second conversation implementation.
- Persona Trade Journal implementation has merged and focused tests pass, while
  `PTJ-007` still owns dev deployment and hosted verification. This plan must
  not duplicate that deployment task.
- `PPL-ALLOC-007/009` own allocation visibility and promotion/allocation
  closeout. Persona proposals may link to those surfaces but must not silently
  mutate capital bindings.
- `MGMT-PERF-IA-006` owns contextual integration among Cockpit, Persona Fleet,
  entity details, Human Inbox, and Agora. New deep links must align with its
  canonical-center approach.

## 3. Product Principles

1. **Servant orchestrates; Personas opine.** Servant may gather context,
   recommend participants, invoke governed work, and synthesize. Every Persona
   opinion retains its author and provenance.
   Consultation participants are frozen `SessionPersona` projections with a
   capability snapshot; the product must not treat a mutable registry Persona
   as a direct-message runtime.
2. **Discussion never implies execution.** A conversational response can create
   a proposal, research request, journal entry, lesson candidate, or review
   request. It cannot directly place an order, change live capital, or write a
   RuntimeBinding.
3. **Disagreement is first-class.** The UI must not flatten disagreement into a
   false consensus. It must preserve positions, causes, missing evidence, and
   conditions that would change each opinion.
4. **Context is explicit.** Strategy, immutable version, decision event,
   environment, time range, capital/runtime references, evidence freshness, and
   source page are visible before submission.
5. **One durable thread.** Contextual entry points create or reopen a canonical
   Strategy Workshop. Follow-up, proposal, decision, journal, and learning
   records link back to that thread.
6. **Fail closed.** Missing permission, stale versions, unavailable evidence,
   unsupported actions, or absent governance prerequisites produce explicit
   degraded or blocked states.

## 4. Information Architecture

### 4.1 Canonical surface

`Agora -> Strategy Workshop -> workshop session` owns:

- conversation timeline;
- context bar;
- interaction mode;
- participant selection;
- Persona opinions and debate;
- consultation synthesis;
- research and backtest cards;
- governed proposal cards;
- readiness and decision handoff;
- durable references to later execution and outcome records.

### 4.2 Contextual entry points

| Source | Action | Workshop context |
| --- | --- | --- |
| Trading Room decision event | `Ask Personas` | strategy/version, event, position/risk snapshot, evidence refs |
| Performance attribution | `Review with Personas` | strategy/version, window, attribution slice, anomalous result |
| Persona Trade Journal entry | `Reflect with Personas` | original thesis, decision, outcome, linked fills/telemetry |
| Persona Detail | `Talk to`, `Ask to review`, `Compare` | preselected Persona and optional comparison Persona |
| Human Inbox | `Open consultation` | gate/review subject and required human decision |

The entry action must resolve a canonical workshop through an idempotent BFF
command and then navigate to the workshop URL with durable focus references.

## 5. Interaction Model

### 5.1 Context bar

Before the composer submits, display:

- subject type and id;
- strategy id and immutable strategy version;
- focused decision, journal, research, proposal, or mutation reference;
- selected Persona and capital/runtime links when available;
- `research`, `paper`, `canary`, or `live` environment;
- evidence time range and latest data timestamp;
- source-page return link;
- stale, missing, permission, or degraded warnings.

Context is submitted as typed references. Do not paste authoritative strategy,
position, or runtime objects into free-form message text.

### 5.2 Interaction modes

| Mode | Intent | Required output |
| --- | --- | --- |
| `ask` | Explain or analyse | conclusion, rationale, evidence, uncertainty |
| `challenge` | Attack assumptions | challenged assumptions, counter-evidence, failure modes, missing data |
| `consult` | Obtain multiple independent views | individual opinions, agreements, disagreements, synthesis |
| `propose_action` | Convert a view into a candidate measure | typed proposal, before/after, risk, validation and approval requirements |
| `reflect` | Compare thesis, decision, execution, and outcome | attribution, lesson candidates, patch/memory/mutation handoffs |

Natural language remains available, but mode selection makes operator intent
and resulting contracts explicit.

### 5.3 Participant selection

The participant picker supports:

- one named Persona;
- multiple named Personas;
- recommended panel;
- risk committee;
- red team;
- same-archetype comparison;
- cross-style comparison.

For each participant show identity, role/archetype, permitted scope, relevant
strategy/capital relationship, readiness, evidence availability, and why the
Servant recommends it. Disabled participants show the exact eligibility or
permission reason.

The Servant does not appear as a voting participant.

## 6. Durable Response Model

A response timeline must distinguish `human`, `servant`, `persona`,
`committee_synthesis`, and `tool_result` authors.

Each Persona opinion contains:

- opinion id and Persona/version identity;
- conclusion and stance;
- rationale;
- evidence references and freshness;
- confidence and uncertainty;
- risk notes and invalidation conditions;
- recommended measures;
- conditions that would change the opinion;
- provenance and trace reference.

A consultation contains:

- participant opinion references;
- agreements;
- disagreements with cause classification;
- homogeneity/correlation warning;
- unresolved questions and missing evidence;
- synthesis status: `options`, `recommendation`, `no_consensus`, or
  `more_research_required`;
- optional proposal references.

Allowed disagreement causes include data, horizon, regime assumption, risk
preference, model assumption, liquidity/execution, policy constraint, and
unknown. Synthesis must never erase the independent records.

## 7. Governed Proposal Model

A Persona measure becomes a typed proposal card, never an unstructured promise
to change the system. The card is a presentation envelope over an existing
canonical owner such as a workshop version patch, trading proposal and
`TradingIntent`, research request, allocation review, journal lesson, memory
candidate, or evolution decision. It must not create a universal proposal state
machine that competes with those owners.

Required proposal fields:

- proposal id, type, state, proposer, and consultation/workshop references;
- target kind/id and immutable current version;
- current value and proposed value as a reviewable diff;
- rationale, evidence, confidence, expected benefit, and adverse scenarios;
- environment ceiling and expiry;
- validation plan, rollback trigger, and rollback action;
- required permissions, reviewers, approvals, and human gate;
- research/backtest/telemetry dependencies;
- resulting artifact, DeploymentPlan, binding, action, or lesson references.

Initial proposal types:

- strategy parameter or rule patch;
- entry/add/reduce/exit condition change;
- position/risk limit recommendation;
- research or backtest request;
- shadow/paper candidate request;
- capital allocation review request;
- pause/freeze/restrict recommendation;
- Trade Journal lesson;
- Persona memory update candidate;
- Persona policy/mutation review candidate.

Operator actions are `request_review`, `request_research`, `modify`, `validate`,
`approve`, `reject`, `defer`, and `cancel`. `modify` creates a new proposal
revision and preserves the original.

## 8. Authority And Risk Tiers

| Tier | Examples | Flow |
| --- | --- | --- |
| Analysis | ask, challenge, consultation | execute read-only analysis; audit conversation/evidence |
| Research | research plan, backtest, comparison | plan -> approval if required -> job -> result |
| Paper/shadow | strategy patch or measure candidate | proposal -> validation -> human approval -> immutable version -> governed paper/shadow activation |
| Canary/live | capital, risk, lifecycle, deployment-affecting measure | proposal -> validation -> risk review -> human approval -> existing governance artifacts and deployment/binding flows |

No Persona or Servant may directly place an order, modify broker authority,
change capital binding, promote lifecycle, write RuntimeBinding, bypass policy,
or approve its own proposal. Existing kill switch and emergency containment
paths remain separate operator controls.

### 8.1 Human role boundary

- `operator` and `admin` are the only interactive roles allowed to create a
  contextual Workshop, submit an interaction, create or revise a governed
  proposal, request validation, or record a proposal decision.
- `viewer` is read-only. A viewer may read only the resources allowed by the
  normal tenant and capability checks, and must receive `401` or `403` for
  every mutation even when its capability manifest includes an Agora
  capability. The frontend must hide or disable the same controls and direct
  API negative tests must prove that this is not merely a UI restriction.
- Capability names describe available product functions; they do not elevate a
  human role or grant execution authority.

### 8.2 Exact proposal and approval binding

An approval is valid only for the exact proposal state that was reviewed. The
authorization record must bind proposal id, proposal revision, target immutable
version, canonical proposal-content digest, validation-result digest, approving
actor, decision time, and expiry. A revised proposal, changed content,
superseded validation, expired authorization, revoked decision, mismatched
tenant, or proposer/approver identity collision invalidates the approval and
requires a new validation and human decision.

Proposal revisions, idempotency records, audit events, and pending side effects
must survive process restart in the production persistence backend. Recovery
must replay deterministic pending work without duplicating audit or downstream
effects. None of these records is itself an order or RuntimeBinding.

## 9. Surface Behaviour

### 9.1 Strategy Workshop

- Add mode selector, participant picker, and explicit context bar around the
  existing composer.
- Render author-labelled opinion cards before synthesis.
- Render agreement/disagreement and evidence gaps as navigable sections.
- Convert supported recommendations into proposal cards.
- Preserve existing research/backtest/workshop cards and SSE update model.

### 9.2 Trading Room

- Add `Ask Personas` beside decision-event details.
- Open a contextual panel that can create/reopen a Workshop.
- Allow fast risk, red-team, and option-comparison consultation.
- Keep final `approve/reject/defer/modify` on the existing decision control.
- `modify` opens structured measure editing and records the linked consultation.

### 9.3 Trade Journal and Performance

- Add original-Persona review, alternate-Persona review, red team, thesis versus
  outcome, lesson, patch, and Persona-update actions.
- Attribute variance to market noise, thesis, signal/data, timing, sizing,
  execution, risk policy, Persona reasoning, or human override.
- Submit lessons and memory/mutation candidates to existing governance queues;
  never silently update Persona memory.

### 9.4 Persona Detail

- Add deep links to start a Workshop with this Persona, request its review, or
  compare it with another Persona.
- Show recent workshops, proposals, adoption/rejection outcomes, material
  disagreements, human corrections, journal lessons, memory candidates, and
  mutations where current contracts provide them.

## 10. BFF Contract Direction

Contract design must extend the canonical Agora versioned bundle additively.
Do not mutate frozen schemas or invent frontend-only truth.

It must also project the existing consultation service request, participant,
transcript/event, memo, escalation, and sponsor-decision lifecycle. A new
committee lifecycle or direct Persona-message store is out of scope.

The contract owner must first reconcile existing workshop, consultation,
trading event, journal, governance action, and Persona contracts. Expected
capabilities include:

- resolve/create contextual workshop idempotently;
- list eligible/recommended participants with exclusion reasons;
- submit a typed interaction intent plus context references;
- read durable Persona opinions, debate, synthesis, and provenance;
- create/revise/read/validate a governed proposal;
- link proposal to research, decision, governance, execution, journal, and
  learning records;
- stream opinion/consultation/proposal lifecycle events through the existing
  workshop event channel.

Exact routes, schemas, ETag rules, capability names, and storage ownership are
an explicit contract-design deliverable. Implementation tasks must not guess
them before that task is accepted.

## 11. Observability And Audit

Every interaction records actor, tenant/user, mode, participant Persona
versions, context refs, evidence refs/freshness, provider/tool traces, result
status, proposal revisions, human decisions, and downstream receipts.

Metrics include interaction completion, consultation latency, evidence-degraded
rate, disagreement/no-consensus rate, proposal conversion, validation failure,
human modification, adoption, rollback, outcome attribution, and reviewed
lesson acceptance. Metrics must be segmented by environment and must not expose
private prompt, secret, broker credential, or raw restricted evidence.

## 12. Accessibility, Mobile, And Failure States

- All mode, participant, evidence, card, diff, and decision controls must be
  keyboard accessible and labelled.
- Mobile uses a full-width consultation/composer sheet without hiding the
  decision context.
- Loading, empty, stale, permission denied, unsupported capability,
  no eligible Persona, no consensus, version conflict, and provider failure are
  distinct states.
- Strict-live mode must never replace missing contracts with mock opinions or
  proposals.

## 13. Release Gates

1. Contract schemas and compatibility tests pass.
2. BFF persistence, restart recovery, idempotency, ETag, tenancy, operator/viewer
   permission, and fail-closed tests pass.
3. Frontend unit/integration tests cover all interaction modes and degraded
   states.
4. Cross-repo E2E proves one-Persona ask, red-team consultation, disagreement,
   proposal revision, paper validation, decision linkage, and Journal
   reflection.
5. No flow grants direct order, broker, capital-binding, RuntimeBinding, or
   self-approval authority.
6. Pantheon BFF and `execute-plans` commits are merged and deployed to the
   Pantheon-owned dev environment. The frontend deployment manifest records the
   exact 40-character frontend commit and exact 40-character BFF commit used by
   the build; `/bff/version` must match the latter.
7. The deployed frontend uses live BFF mode, strict fallback, safe write
   defaults, and contains no embedded bearer token.
8. Authenticated hosted browser smoke verifies strict-live behaviour on desktop
   and mobile, audit/readback, proposal revision/validation, rollback/degraded
   paths, an operator positive flow, and both UI and direct-API viewer mutation
   denial.

## 14. Explicit Non-Goals

- A new standalone generic Persona chat product.
- Browser-to-OpenClaw or browser-to-provider calls.
- Automatic live trading from conversation.
- Automatic memory/policy mutation from outcome feedback.
- Replacing existing Trading Room decisions, governance actions, capital
  allocation, deployment, or emergency controls.
- Using Lovable or `front-ai-trading-system` as the delivery source.

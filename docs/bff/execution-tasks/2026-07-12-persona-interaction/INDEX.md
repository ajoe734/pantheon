# Persona Interaction Execution Tasks

Date: 2026-07-12  
Planning source: `docs/product/persona-interaction-and-governed-action-plan.md`

## Delivery waves

| Task | Owner | Reviewer | Wave | Depends on | Outcome |
| --- | --- | --- | --- | --- | --- |
| `PINT-001` | Claude | Codex | 0 | — | Freeze cross-repo interaction, opinion, consultation, proposal, and event contracts |
| `PINT-002` | Codex2 | Claude | 1 | `PINT-001` | Implement BFF context resolution, participant eligibility, and typed interaction submission |
| `PINT-003` | Gemini | Codex2 | 1 | `PINT-001` | Implement durable opinion/debate/synthesis readback and workshop streaming |
| `PINT-004` | Claude2 | Gemini | 1 | `PINT-001` | Implement governed proposal revisions, validation bridge, linkage, and audit |
| `PINT-005` | Copilot | Claude2 | 2 | `PINT-002`, `PINT-003` | Implement Workshop context/mode/participant UX and independent opinion rendering |
| `PINT-006` | Codex | Copilot | 2 | `PINT-002`, `PINT-004` | Implement proposal cards, diff/revision, validation, and decision handoff UX |
| `PINT-007` | Gemini2 | Codex | 2 | `PINT-002`, `PINT-003`, `PINT-005` | Add Trading Room Ask Personas and structured modify linkage |
| `PINT-008` | Antigravity | Gemini2 | 2 | `PINT-002`, `PINT-003`, `PTJ-007` | Add Trade Journal/Performance reflection and governed learning handoffs |
| `PINT-009` | Claude | Antigravity | 2 | `PINT-002`, `PINT-005`, `MGMT-PERF-IA-006` | Add Persona Detail and Human Inbox contextual entry links/readback |
| `PINT-010` | Codex2 | Claude | 3 | `PINT-003`–`PINT-009` | Cross-repo integration, security, dev deploy, hosted E2E, and closeout |

Owners are initial fleet routing recommendations. The supervisor may reassign
for provider readiness or occupancy, but owner and reviewer must remain
different.

## Shared guardrails

- Read the planning source and every cited canonical contract before editing.
- Reconcile active tasks before work; do not duplicate `PTJ-007`,
  `PPL-ALLOC-007/009`, or `MGMT-PERF-IA-006` scope.
- Frontend work belongs in `ajoe734/execute-plans`, never inside Pantheon and
  never in `front-ai-trading-system`.
- Contract changes are additive to the currently active Agora contract version;
  frozen bundles must not be rewritten.
- Strict-live paths must fail closed. No mock Persona response or proposal may
  appear as live truth.
- No task may add direct order, broker, capital-binding, RuntimeBinding,
  lifecycle promotion, or Persona self-approval authority.
- Each repository change requires task branch, validation, scoped commit with
  required trailers, push, PR, checks, merge, and merge-SHA evidence.

## Task specifications

### PINT-001 — Interaction contract and authority closure

Artifacts:

- versioned Agora schema/OpenAPI additions for context, interaction intent,
  participant eligibility, Persona opinion, consultation synthesis, governed
  proposal/revision, links, and workshop events;
- storage ownership and authority matrix;
- route, capability, idempotency, ETag, tenant, permission, degraded-state, and
  compatibility decisions;
- generated examples consumed by BFF and frontend tests.

Acceptance:

- existing Workshop, consultation, trading-event, Trade Journal, governance,
  Persona, allocation, and runtime contracts are traced;
- participant identity is a frozen SessionPersona/capability snapshot and the
  accepted design projects the existing consultation service lifecycle;
- the proposal presentation contract maps each type to its canonical owner and
  does not create a competing universal execution state machine;
- frozen bundle hashes remain unchanged and additive bundle validation passes;
- Servant, Persona, committee, human, and tool authorship is unambiguous;
- disagreement, evidence freshness, no-consensus, and proposal revision are
  representable without free-form-only fields;
- authority tests prove no new direct execution or self-approval path;
- owner opens and merges the Pantheon PR before downstream implementation.

### PINT-002 — BFF context, eligibility, and interaction commands

Artifacts: Pantheon BFF routes/services/tests for contextual workshop
resolution, participant eligibility/recommendation, and typed interaction
submission.

Acceptance:

- repeated contextual entry commands are idempotent;
- immutable strategy version and focused object references are verified;
- eligibility returns included and excluded Personas with reasons;
- tenant, role, Persona readiness, environment, and capability gates fail
  closed;
- read-only analysis cannot cross into governed write authority;
- contract, unit, and integration tests pass and the PR is merged.

### PINT-003 — Durable opinions, debate, synthesis, and streaming

Artifacts: persistence/adapters, BFF reads, workshop-event integration, and
tests for independent opinions, debate, agreements, disagreements, evidence
gaps, and synthesis.

Acceptance:

- Persona/version authorship and provenance survive persistence/readback;
- synthesis references but never overwrites individual opinions;
- `no_consensus` and `more_research_required` remain valid terminal outputs;
- homogeneity/correlation warning is represented and enforced where required;
- SSE reconnect/readback does not duplicate or lose durable cards;
- degraded provider/evidence paths are honest and tested; PR is merged.

### PINT-004 — Governed proposal lifecycle

Artifacts: proposal/revision store and routes, validation adapter to existing
governed action contracts, linkage/audit, authorization, and tests.

Acceptance:

- proposal types and states match the accepted contract;
- before/after diff, immutable target version, expiry, evidence, validation,
  approval, and rollback metadata are required where applicable;
- modify creates a revision and preserves history;
- ETag/idempotency conflicts fail deterministically;
- paper/live ceilings and existing governance authority are enforced;
- no proposal directly writes orders, bindings, or memory; PR is merged.

### PINT-005 — Strategy Workshop interaction UX

Repo: `ajoe734/execute-plans`.

Artifacts: context bar, mode selector, participant picker, composer integration,
opinion/debate/synthesis cards, accessibility/mobile states, strict-live
adapters, and tests.

Acceptance:

- all five modes can be submitted with typed context;
- named, recommended, committee, red-team, same-style, and cross-style
  participant choices render eligibility explanations;
- Human, Servant, Persona, synthesis, and tool authors are visually distinct;
- independent opinions precede synthesis and disagreements remain visible;
- loading/empty/stale/denied/degraded/no-consensus states are covered;
- existing research/backtest cards and SSE flow continue to work; PR is merged.

### PINT-006 — Proposal and decision UX

Repo: `ajoe734/execute-plans`.

Artifacts: proposal card, structured diff/revision editor, evidence/validation
panel, review/research/validate/approve/reject/defer/cancel controls, audit
links, and tests.

Acceptance:

- current and proposed values are reviewable without raw JSON-only UX;
- modify generates a new backend proposal revision;
- permission, stale version, ETag, validation, and governance failures are
  actionable and fail closed;
- environment ceiling and required human/risk approvals are prominent;
- UI never represents conversation as executed state; PR is merged.

### PINT-007 — Trading Room contextual consultation

Repo: `ajoe734/execute-plans` plus BFF additions only if already covered by the
accepted PINT contracts.

Artifacts: `Ask Personas` entry, contextual consultation panel/deep link,
fast risk/red-team/option comparison, structured modify linkage, and tests.

Acceptance:

- decision event, strategy version, position/risk snapshot, and evidence refs
  are carried as typed context;
- consultation never replaces the existing final decision control;
- approve/reject/defer/modify retain current authority and audit semantics;
- modify links the selected proposal revision and consultation;
- mobile and strict-live E2E pass; PRs are merged.

### PINT-008 — Journal reflection and learning handoffs

Repos: Pantheon BFF and `ajoe734/execute-plans`.

Artifacts: reflection entry actions, thesis-versus-outcome and attribution
cards, lesson/patch/memory/mutation handoffs, provenance/readback, and tests.

Acceptance:

- work starts only after `PTJ-007` establishes deployed canonical Journal
  readback or documents a compatible development fixture;
- original, alternate, and red-team Persona review are supported;
- attribution categories match the plan and preserve evidence;
- lessons enter reviewed governance queues rather than mutating memory/policy;
- linked fill/telemetry permissions and missing evidence fail closed;
- cross-repo PRs are merged.

### PINT-009 — Persona and Human Inbox contextual entry

Repo: `ajoe734/execute-plans`; BFF reads only when accepted contracts require
them.

Artifacts: Persona Detail actions, comparison flow, recent interaction/proposal
readback, Human Inbox consultation entry, focus-preserving navigation, and
tests.

Acceptance:

- `Talk to`, `Ask to review`, and `Compare` resolve/reopen a canonical Workshop;
- preselected Persona and source-page return context survive navigation;
- recent records distinguish proposals, decisions, corrections, lessons, and
  mutations without inventing missing data;
- work reconciles `MGMT-PERF-IA-006` links and avoids duplicate analysis pages;
- accessibility, mobile, strict-live, and route tests pass; PR is merged.

### PINT-010 — Integration, deployment, and hosted closeout

Artifacts: cross-repo compatibility manifest updates, security/authority test
report, authenticated E2E suite, dev deployment evidence, hosted browser
evidence, rollback/degraded proof, and closeout record.

Acceptance:

- all upstream tasks and active-task dependencies are merged;
- E2E proves one-Persona ask, red-team consult, visible disagreement, proposal
  revision, paper validation, Trading Room linkage, and Journal reflection;
- negative tests prove no direct order/broker/capital/binding/memory/self-approve
  authority;
- Pantheon BFF and `execute-plans` are deployed from GitHub-visible commits to
  Pantheon-owned dev hosting with strict-live configuration;
- authenticated desktop/mobile hosted smoke and audit readback pass;
- final record lists PRs, merge commits, deployment run, and any explicitly
  deferred non-blocking scope.

## Dispatch policy

Wave 0 starts immediately. Wave 1 tasks may be placed on the board with
`PINT-001` as a hard dependency and must not implement guessed contracts.
Before touching Workshop, Performance, Trading Room, or Trade Journal files,
each owner must reconcile the active `AG-DYNUI-LIVE-*` lane and current
`PTJ-007` deployment truth to avoid overlapping edits.
Wave 2 may start only after its listed contracts and BFF capabilities merge.
`PINT-010` owns final integration and hosted completion; individual feature
tasks do not independently claim whole-program delivery.

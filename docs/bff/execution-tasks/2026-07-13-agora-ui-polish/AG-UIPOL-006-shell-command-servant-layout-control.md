# AG-UIPOL-006: Shell command, Servant, and layout-control parity

Status: draft follow-up from AG-UIPOL-005. Not yet dispatched.

Priority: 1 — shared operator control and a contextual-route crash.

## Matrix coverage

`parity-matrix.md` rows G-01–G-03, TR-10, WB-18, SRV-01–SRV-02.

## Design authority

- V6 §§2.1–2.3, 10.1–10.3, and 17
- V4 §§3–5
- V11 §§3.2, 5.1, 6.1, 8, and 15.1
- `Agora.dc.html` shell, command response, and layout-proposal states
- screenshots `01-ai.png`, `01-aifix.png`, `01-adjust2.png`,
  `02-adjust2.png`, and `01-applied.png`

## Scope

Establish the shared desk foundation once, then expose a governed command and
Servant proposal workflow across the three tabs. Repair the workshop-context
drawer crash. This task owns whole-layout proposals; widget-specific revision
already ships and must not be replaced.

Primary repo: `ajoe734/execute-plans@dev`. Additive Pantheon BFF work is in
scope only when a missing governed proposal/task contract cannot be satisfied
by an existing route. Frontend code must not be placed in the Pantheon repo.

## Work

1. Implement the recovered desk shell hierarchy and shared design tokens:
   trader/persona context, tab-aware command area, compact quantitative type,
   panel/border rhythm, and semantic amber/green/red states. Compose with
   AG-UIPOL-001 locale keys and AG-UIPOL-002's single-scroll owner.
2. Add the contextual global command input and shortcut tasks. Responses must
   separate understood intent, plan, evidence, risk, and proposed governed
   actions; do not turn it into a generic chat transcript.
3. Make the global Servant drawer a real task surface: current context,
   composer, task/proposal status, evidence/risk, apply/reject boundary, and
   recent task state. Fix workshop context normalization so
   `/agora/strategy-workshop/:workshopId` never reads an absent `subject.title`.
4. Implement “Adjust Layout” as a whole-workspace proposal with intent/chips,
   understood changes, before/after preview, validation, apply, reject, and
   version creation. It must never silently select the first view or mutate the
   dashboard before acceptance.
5. Either make Jobs/Shadow/Journal bottom-strip entrances expose their designed
   content or render them explicitly unavailable; selected styling alone must
   not imply a working panel.

## Non-goals

- Five Strategy Lens dashboards/candidate drawer (AG-UIPOL-007).
- Winner Branch view data/content (AG-UIPOL-008).
- Locale migration and scroll ownership already assigned to AG-UIPOL-001/002.
- Order routing, capital binding, broker execution, arbitrary shell access, or
  an ungoverned assistant write surface.

## Acceptance

- Hosted desktop and 390px evidence shows one coherent desk shell and a useful
  Servant task drawer on Trading Room, Strategy Workshop, and Performance.
- The contextual drawer loads on a real workshop detail route with no error
  boundary and shows normalized live context.
- A live layout request produces a reviewed before/after proposal, does not
  change the dashboard before acceptance, and creates a readable new version
  only after acceptance.
- The command/Servant request ledger proves no order, broker, capital-binding,
  or runtime-binding write route was called.
- Component tests cover absent/partial workshop subject data, proposal
  reject/apply, and drawer context changes. Hosted Playwright evidence is
  pinned to an `execute-plans` deploy SHA.

## Dependencies and composition

- Consume AG-UIPOL-001 i18n keys; do not add a second copy catalogue.
- Preserve AG-UIPOL-002's overflow contract.
- Reuse the shipped Widget Revision Proposal semantics and version APIs.
- AG-UIPOL-011 owns cross-surface narrow behavior after this desktop shell is
  stable; this task must still avoid introducing a desktop-only blocker.

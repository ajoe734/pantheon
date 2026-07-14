# AG-UIPOL-007: Multi-lens monitoring and candidate parity

Status: delivered and reviewer-approved; owner closeout evidence in progress
on 2026-07-14.

Priority: 4 — continuous monitoring around the generated strategy workspace.

## Matrix coverage

`parity-matrix.md` rows TR-01–TR-09.

## Design authority

- V6 §§3–9 and §16A/C
- V5 §§4.1–4.8
- `Agora.dc.html` lines 123–617 and 754–820
- screenshots `v5-signals.png`, `dashB2.png`, and `drawer.png`

## Scope

Restore Strategy Lens navigation, five distinct monitoring dashboards, the
dense candidate/held board, and Candidate Review Drawer without weakening the
V11 Winner Branch workspace already routed from ready strategies.

Primary repo: `ajoe734/execute-plans@dev`. Pantheon BFF additions are allowed
only as additive, live-backed contracts for designed lens/candidate fields.

## Work

1. Replace raw strategy-instance tabs as the only switcher with recognizable
   Strategy Lens cards/status: lens thesis, candidate/held counts, risk state,
   freshness, and selected strategy/workspace context.
2. Implement the five designed dashboard recipes, each with its own hierarchy
   and widgets: chip/large-holder positioning, industry laggard, technical
   breakout, event trading, and large-flow/liquidity execution. Do not reuse
   one generic card grid under five labels.
3. Add the lens thesis/threshold sidebar and dense candidate/monitoring board
   with lens-specific columns, filters, state, held comparison, and decision
   entrances.
4. Add the Candidate Review Drawer with code/fit/status, reasons, concerns,
   next event, evidence, discussion/history, and governed review/watch/decision
   actions. Distinguish candidate review from the global Servant and layout
   proposal drawers.
5. Preserve the handoff from a candidate/ready strategy into its V11 workspace
   and make the selected lens/strategy relationship explicit.

## Non-goals

- Whole-layout Servant proposals (AG-UIPOL-006).
- Filling the seven V11 workspace views (AG-UIPOL-008).
- Any direct order/broker/capital mutation.

## Delivered implementation

The frontend implementation landed in `ajoe734/execute-plans@dev` through
PRs [#319](https://github.com/ajoe734/execute-plans/pull/319),
[#320](https://github.com/ajoe734/execute-plans/pull/320),
[#322](https://github.com/ajoe734/execute-plans/pull/322), and
[#341](https://github.com/ajoe734/execute-plans/pull/341). PR #322's merge
commit, `3ce439d8713dcb437673bf7b81df78cb917d8082`, is the composed UI delivery;
PR #341's merge commit, `36a2f9292eadccd32b1fd79db2e7820ce750a984`,
adds the canonical availability response normalization required by the live
Pantheon contract:

- five explicit Strategy Lens cards and five separately structured dashboard
  recipes;
- a lens thesis/rules rail, lifecycle filters, lens-specific candidate
  columns, and dense candidate board;
- an accessible Candidate Review Drawer with reason, concerns, event,
  evidence, lifecycle review controls, and a dynamic Winner Branch handoff;
- Candidate Pool read loading/error/empty handling with an unmistakable sample
  fallback; and
- genuine `en-US` and `zh-TW` entries for task-owned operator copy, including
  the eleven keys called out during review.

The compose commit deliberately preserves AG-UIPOL-008's current Winner Branch
workspace and the existing governed Trading Room contracts.

## Data and action truth boundary

- The five dashboard recipe bodies are design-parity samples and always carry
  a sample-only badge. They are not presented as live telemetry.
- Candidate Pool member reads are attempted for the active lens. An empty or
  unavailable pool falls back to local candidates with a visible sample-data
  warning.
- Drawer lifecycle buttons change the visible candidate review state for this
  surface; they do not issue or claim a persisted Candidate Pool review write.
- The task adds no order, broker, capital-binding, or runtime-binding route.

These boundaries satisfy the task's "live or explicitly unavailable" rule
without widening authority. A future task may connect a canonical lens-to-pool
identity and governed persistence; that work must not erase the unavailable or
sample disclosure.

## Acceptance

- Hosted desktop evidence shows all five visually and informationally distinct
  lens dashboards with live or explicitly unavailable data.
- A real candidate can be opened from its board into the designed drawer and
  returned to the same filtered context.
- Candidate and held/exit states are distinguishable; action copy never claims
  execution when only a decision was recorded.
- Component/contract tests cover every recipe, lens-specific columns, empty and
  delayed data, drawer keyboard/focus behavior, and candidate state changes.
- Hosted narrow evidence is supplied for the switcher, board, and drawer;
  AG-UIPOL-011 remains the final cross-surface responsive gate.

## Closeout

- Reviewer: Claude; round-4 verdict **Approved** in
  `support/reviews/AG-UIPOL-007-review-claude.md`.
- Hosted manifest, browser assertions, screenshots, validation commands, and
  residuals:
  [`evidence/AG-UIPOL-007-hosted-evidence.md`](./evidence/AG-UIPOL-007-hosted-evidence.md).
- `parity-matrix.md` remains the AG-UIPOL-005 pre-delivery audit baseline. Its
  TR-01–TR-09 verdict cells are not silently rewritten as a post-delivery
  design audit by this implementation closeout.

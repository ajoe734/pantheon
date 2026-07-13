# AG-UIPOL-007: Multi-lens monitoring and candidate parity

Status: draft follow-up from AG-UIPOL-005. Not yet dispatched.

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

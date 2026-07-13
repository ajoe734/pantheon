# AG-UIPOL-009: V10 expert Strategy Workshop parity

Status: draft follow-up from AG-UIPOL-005. Not yet dispatched.

Priority: 3 — strategy formation and research judgment are upstream of the
Trading Room.

## Matrix coverage

`parity-matrix.md` rows SW-01–SW-07.

## Design authority

- V10 §§1–13 (latest Workshop authority)
- V11 §§2.1–2.3 for the handoff boundary
- `Agora.dc.html` lines 825–1049
- screenshots `01-v10-mid.png` and `02-v10-mid.png`

## Scope

Turn the shipped event-card Workshop into the recovered expert strategy
dialogue: professional description, reconstruction, prioritized uncertainty,
research/backtest evidence, versioned discussion, and the 12-block readiness
map. Preserve the live ready handoff that already works.

Repos: `ajoe734/execute-plans@dev` plus additive Pantheon Workshop card/contract
work where necessary. Private workshop content must remain private and
tenant-scoped.

## Work

1. Add a visible new-workshop entry with a large professional strategy
   description field, examples, and an explicit start-discussion action.
2. Render the first Servant response as a Strategy Reconstruction Card:
   understood strategy core, research subproblems, recognized components, and
   claims that cannot yet be asserted.
3. Replace questionnaire-like Unknown/Confirmed output with prioritized
   missing/conflicting assumptions and one high-information next question.
4. Add structured, provenance-bearing research cards for relationship/branch
   mapping, Winner Branch score and versions, branch migration, event lead,
   probability/EV, position sizing/capacity, literature/similar alpha, and
   backtest/robustness results.
5. Support critique, follow-up, scenario comparison, and versioned strategy
   dialogue without exposing internal agent/tool traces.
6. Implement the V10 12-block completeness/readiness map and compose with
   AG-UIPOL-004 so rail, cards, and gates read one snapshot. Preserve the
   explicit, version-scoped Add to Trading Room handoff.

## Non-goals

- A generic chatbot, raw agent trace, or developer/debug surface.
- Fabricated research/backtest claims when no evidence result exists.
- The generated workspace itself (AG-UIPOL-008).

## Acceptance

- A hosted live workflow starts from a blank professional description, shows
  reconstruction and one prioritized question, receives at least one real
  research/backtest result with provenance/caveat, and reaches an honest
  readiness state.
- The 12-block rail, dialogue cards, and readiness gates agree for the same
  version; unresolved assumptions remain visible.
- No raw UUID, provider trace, hidden prompt, or private content leakage is
  visible in operator copy or cross-tenant requests.
- Add to Trading Room remains unavailable until the designed readiness
  conditions are met, then hands off the exact workshop/strategy version.
- Component, contract, privacy, and hosted Playwright tests cover blank,
  conflict, research, failure, revision, and ready states.

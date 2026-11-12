# Hypothesis screen and entry compilation

Task: `HYPOTHESIS-CARD-SCREEN-ENTRY-20261112`. Owner: Codex2. Reviewer: Claude.

`hypothesis_card.v1` accepts optional `screen_idea` and `entry_idea` objects.
Each contains a statement, explicit dataset/field inputs, and exact source
spans. All nested objects are closed. Both ideas' citations are verified
against stored text; an unverifiable idea rejects the card. Legacy cards
without these fields retain their existing validation contract.

The compiler passes both ideas through the existing restricted model client,
then validates the proposal with the dependency's `candidate_screen.v1` and
factor-rule validators. Screen inputs must actually be consumed by the screen;
entry inputs must be consumed by signal/entry/exit/strength. A declared idea
cannot disappear into a default. Unknown datasets or fields in an idea produce
`needs_data`; every missing dataset is counted once per card, including when
several are absent. Prediction demand remains independently counted.

Code applies these declared pairings and records their names under
`default_pairing` in the compiled artifact:

| Missing part | Pairing | Behavior |
| --- | --- | --- |
| Screen | `whole_governed_tw_boundary.v1` | All eligible Taiwan equities; daily identity filter over governed, dated daily closes |
| Entry on a screen-only long card | `long_hold_while_in_pool.v1` | Constant long target and unit strength, no timed or signal exit; existing pool evaluator closes positions removed from the pool |
| Both separate idea fields on an old card | `legacy_rule_sketch` entry plus the boundary screen | Preserve the proposed legacy signal/entry/exit/strength |

The boundary default explicitly declares a minimum pool size of 3, 60 history
bars, a 20-session median traded-value window, and a TWD 10,000,000 liquidity
floor, in addition to the dependency's suspension and dated-data checks.
The identity filter uses `not(lt(close, close))` because the existing DSL has
no equality operator or empty screen. Missing daily closes still fail closed.
Default constants have reserved names and fixed ranges in the mutation surface;
the proposer cannot replace them. A non-Taiwan boundary default or a screen-only
card without an explicit long direction is refused rather than choosing an
instrument or exposure. The existing evaluator emits BUY for a maintained long
target; the test verifies that holding duration is preserved and no timed exit
is introduced.

`screen_fingerprint` hashes screen structure plus its consumed input definitions.
`entry_fingerprint` hashes signal, entry, exit and strength plus their consumed
input definitions. As with the existing compiler fingerprint, numeric parameter
values are excluded. The draft/support key hashes the pair, so changing either
structure produces a new draft; repeating a card remains idempotent. The
compiler cache key is versioned and includes both ideas, excluding predictions,
testability stamps and attached compiled artifacts. Existing stored compiler
outcomes are retained; the new version recompiles old cards instead of reusing
an outcome lacking the declared screen and fingerprints.

The production path remains `run_controller_tick -> DistillationWorker ->
HypothesisExtractor -> HypothesisCompiler -> seed metadata`. Integration tests
call that environment-wired entry point and replay the output through the shared
candidate-pool evaluator. No production data, feature catalogue entries or pool
members are synthesized. The sector-inflow/quality example remains `needs_data`
until governed industry classification and sector-flow data exist.

The new transport fixture is an explicitly hand-authored model-shaped replay,
not evidence of a live provider call. Its Taiwan price references and evaluator
inputs come from the dependency's official TWSE recording, whose raw responses,
hashes and provenance remain in
`services/research/tests/fixtures/candidate_pool_tw_recorded.json`. That recording
contains two symbols and 39 sessions and is survivorship-limited; it proves
deterministic behavior, not market-wide coverage or performance. No hosted
deployment or trading acceptance is claimed by this source task.

See `evidence.json` and the committed validation logs for exact commands,
results and acceptance mapping. Independent review is pending at handoff.

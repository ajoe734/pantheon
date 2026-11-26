# Hypothesis card predictions

Task: HYPOTHESIS-CARD-PREDICTIONS-20261112. Owner: Codex. Reviewer: Claude.

The additive `hypothesis_card.v1.predictions` field follows design §3.2 in
[PR #7206](https://github.com/ajoe734/pantheon/pull/7206), inspected at
`6e5df1989a83d38fc9c7e391f754e8429ce8e6c9`. That design PR was open when
implementation began; this task has no declared dependencies.

Each prediction carries a statement, dataset/field observable, one of the five
fixed test kinds, expected sign (`+`, `-`, `0`), a `{before, after}` window in
trading days, and exact source citations. The extraction prompt requests two
to four predictions implied by the mechanism. Existing cards can omit the field.
When present in model output the array must contain two to four items; the
stored contract permits zero to four because citation filtering can remove any
number of them. Invalid schema/extra keys fail the extraction closed.

The extractor checks every prediction citation against the full stored record's
ID, offsets, and exact text. A bad prediction is removed independently; its
index and `citation_unverifiable` detail are written as `prediction_dropped`
events in the existing extraction ledger. The card's own invalid citations
continue to reject the card.

Code assigns prediction `testability` using the same governed feature catalogue
as the rule compiler. Unknown datasets and unknown fields both receive
`needs_data:<dataset>`; known observables receive `testable`. The model cannot
supply this annotation. Catalogue membership does not assert that market data
has been fetched or that the prediction has been statistically supported.

The compiler adds missing prediction datasets to its existing needs-data
report, deduplicated per dataset/card alongside rule inputs. It checks before
cached returns so an existing rule outcome does not hide new prediction demand.
Prediction data demand does not change the card's rule testability. Predictions
are excluded from the proposer input and compile prompt, card identity, rule
artifact, and rule fingerprint. Replaying a card keeps support constant;
a distinct source supporting the same rule increments support once.

Tests replay the committed JSON fixture through the real extraction client,
`DistillationWorker`, and the environment-constructed controller tick. The
fixture is hand-authored in the structured-extraction response format; it is
not a capture from a live model. Transport and registry I/O are test doubles;
all parsing, schemas, citation checks, catalogue lookup, SQLite accounting,
compiler prompt generation, and seed persistence use production code.

Validation results and commands are in `evidence.json`. This is source-level
functional evidence; no hosted deployment or live-model result is claimed.
The statistical mechanism gate is a separate task.

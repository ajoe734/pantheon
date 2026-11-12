# Taiwan industry classification and sector features

Task: `TW-INDUSTRY-CLASSIFICATION-DATASET-20261112` · owner Codex2 · reviewer Claude.

The existing official provider refresh now normalizes `tw_industry_classification`
from the following **recorded real OpenAPI** responses. The complete selected rows
are retained in the source-ingestion fixtures; their metadata gives the original
full-response SHA-256, response row count, URL and acquisition timestamp.

| Venue | Official endpoint | Company field | Industry field | First local acquisition (UTC) |
| --- | --- | --- | --- | --- |
| TWSE | `https://openapi.twse.com.tw/v1/opendata/t187ap03_L` | `公司代號` | `產業別` | 2026-11-12T14:28:00.096140Z |
| TPEx | `https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O` | `SecuritiesCompanyCode` | `SecuritiesIndustryCode` | 2026-11-12T14:27:47.898769Z |

Both fields contain two-digit industry categories. `industry_code` is the numeric
category used by the existing numeric feature evaluator (e.g. `24`); the original
string remains in `industry_code_raw`. These codes are categories, not scores.
Missing/invalid categories are omitted, never assigned an invented unknown sector.

Each acquisition carries `observed_at`, `available_time` and `ingest_time`.
The event date is the acquisition date. Neither the report date nor the listing
date becomes historical classification evidence. The normal storage writer
appends runs and dates, so successive acquisitions accumulate forward history.
Readers select the latest classification visible at their decision cutoff, with
no classification before the first acquisition. Subsecond acquisition precision
is captured by the production normalizer and survives both the catalogue reader
and training source-storage wrapper reader. The recorded provider/storage tests
check that nothing is visible one microsecond before acquisition and the
classification is visible at acquisition itself.
The timestamps above establish the first observations **in this local recording**;
this PR does not assert a first hosted ingestion or deploy a dataset.

`tw_industry_classification.industry_code` is catalogued with zero additional day
lag and acquired-time availability. The connector definition, official-source
catalogue/template and active-universe daily refresh rule all route it through
the existing official adapter. Existing record budgets still apply; a truncated
feed cannot prove completeness for a larger market boundary.

`tw_sector_flows.net_shares` is a runtime-derived catalogue feature, not an
exchange endpoint or a separately refreshed source. It sums foreign, trust and
dealer net shares within each classified sector, then exposes the sector total
to each member. Units are **shares**. It requires an explicit complete governed
market boundary, all three components for every member on one common session,
and a classification known by the end of that Taipei session. Its additional
lag is zero; availability is the maximum of all input availability/lag floors.
It never uses a later profile to reconstruct an earlier session. Missing,
nonfinite, delayed or mismatched constituents emit no aggregate. The generic
catalogue input builder requires `sector_entities`; the screen evaluator supplies
the complete `candidate_pool_market.v1` symbol boundary automatically. Each
read derives the latest complete visible session; it does not invent a historical
sector series or a TWD money-flow measure.

Screen `group_by.industry_input` resolves a declared catalogue classification
input. `industry_neutralize` is supported as the outermost screen expression:

```json
{
  "kind": "filter",
  "refresh": "daily",
  "group_by": {"industry_input": "industry"},
  "expression": {"op": "industry_neutralize", "args": [{"ref": "price"}]}
}
```

The operator subtracts the mean of the current step's eligible members in each
industry from each member's latest time-series score. The declared `industry`
input must use dataset `tw_industry_classification`, field `industry_code`.
A missing classification or constituent produces snapshot `data_status=needs_data`
and HOLD views with `status=needs_data`. An incomplete demeaning cross-section
keeps no members. Per-symbol rules, other cross-sectional operators and temporal
operators wrapping demeaning remain rejected because they lack the required
cross-section/time alignment. Paper and research share the production screen
implementation; tests also call `evaluate_strategy_view`, `CurrentArtifactStrategy`
and the existing real vectorbt replay path.

`recorded_inputs.json` contains real TPEx 2026-11-12 prices and institutional flows
for 3105, 6488 and 8069. Tests use the original values; fault cases explicitly
remove fields or change timestamps/values. The test boundary is those three
recorded companies, not a claim to have captured the entire Taiwan market.
Current company profiles do not establish delisted-company history, so existing
survivorship-limited admission remains in force.

No egress permission or allowlist changes are included. Production profile
requests use the existing guarded official OpenAPI transport. Exchange website
endpoints are never added as an alternative.

Validation commands and acceptance-test mappings are recorded in `evidence.json`.
Independent review and the supervisor's exact-head merge are required before
owner closeout; this manifest does not claim either has happened.

The prior artifact-contract admission hold is resolved by the operator's scoped
correction and redispatch. `review-admission-hold.md` retains the original
rejection alongside the canonical resolution. This remains the same task and
PR #7245; its refreshed head must pass a fresh independent review.

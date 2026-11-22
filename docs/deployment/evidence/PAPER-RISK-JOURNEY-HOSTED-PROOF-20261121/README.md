# Governed paper journey proof — hosted evidence outstanding

Task: `PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121`; owner Codex; reviewer Claude.
Register owners: RC2 WP2.1 paper persona/risk chain, RC6 journey causes/history.

The hosted probe **failed**, exit 1. This task is not accepted. Source tests and
historical recovery do not replace the required fresh governed paper events.
No trade, repair, replay, approval or deployment was performed by this task.

At 2026-11-22 17:01Z the served pair was BFF
`da78ddacd5225c7c70d8d27a52b8db15ef7a0fec`, FE
`bf4e79d774bb387a51e9030e5821b4f61bbbb806`; the manifest deployment cutoff is
`2026-11-22T15:14:29Z`, release controller run
[45029250554](https://github.com/ajoe734/pantheon/actions/runs/45029250554).
The manifest's accepted release state describes that earlier release, not this
feature chain. Autoresolve #7740 (`eedf523a5…`) is merged and is not in this
served backend. Deployment runs
[45046460903](https://github.com/ajoe734/pantheon/actions/runs/45046460903) and
[45046752287](https://github.com/ajoe734/pantheon/actions/runs/45046752287) failed;
the latter stopped at payload validation before any switch. No rollback or
admission bypass is requested by this evidence.

`hosted-stack.json` records container/image observations separately from BFF
identity. Runtime manager, paper signal producer and BFF are healthy on images
labelled `da78…`. The inventory includes stopped one-shot services and prebuilt
images; it is not a new full-stack deployment acceptance. Runtime desired-state
and the served paper process's policy resolver are read in
`hosted-runtime-owner.json`; only the three trading bindings are retained.

The probe checked all three expected trading personas and 697 approval rows.
Each active pool still has its inline `rp-pool-persona-paper-…` policy version
`1.0`, gross/net/leverage `2.0`, requested profile `low`, and **no proven owner
mapping**. The deployed runtime resolver independently returns
`unresolved_profile_mapping` with its actual pool, policy, version and content
digest in a policy-owner decision request. These are observed owner records,
not values for engineering to approve. No single-name or cash-reserve percentage
is inferred. The three scoped list reads return eight rows each, zero eligible
post-cutoff journeys inspected. That is a bounded sample, not proof that no
other event exists globally. Fresh decision, rejection and reconciled fill
requirements all remain unmet.

`hosted-browser.json` records a normal `/management/trade-journeys` entry, then
both historical detail pages, using the existing viewer identity. The pinned
FE/BFF pair agrees before and after, all intended GETs return 200, and the
recovered reasons render visibly. There are zero write attempts, page errors
or transport errors. This is a real hosted browser observation, not a replay.

| Historical URL suffix | Before (recorded prior-task evidence) | Current hosted read |
| --- | --- | --- |
| `tj_79726064-4cc2-5510-8e11-7f5e59ff89bd` | refusal presented with expression reason `long_cash` | `insufficient_cash`; not submitted; required 100034.3461, available/limit 100000 TWD; original event `91d55953-b575-4fe8-8d39-6e0cade58d59` |
| `tj_57418ba7-ef37-5015-9b5e-b6423872da89` | health-only failed reconciliation presented as variance | `reconciliation_evidence_incomplete`, `unverified_evidence`, accounting_variance=false, comparison_completed=false; original event `48c946c8-39d1-52bd-b1a1-ce72dc7f6e14` |

Both URLs start at `https://app.dev.mvl-cap.tw/management/trade-journeys/`.
The earlier baseline is in
`docs/deployment/evidence/PAPER-RISK-POLICY-PROVENANCE-20261121/` and
`docs/deployment/evidence/TRADE-JOURNEY-HISTORICAL-CAUSE-REPAIR-20261121/`.
History remains history; neither row counts as a fresh rejection or fill.

Local verification uses synthetic fresh positive controls, recorded historical
DTO shapes and negative controls. Existing runtime tests prove that close-leg
exits still resize or become no-trades when holdings change and can reduce
exposure already above tightened limits. Existing reconciliation tests cover
broker/position/cash differences and missing evidence. These tests do not prove
a new hosted fill or risk-reducing exit occurred. Exact commands and terminal
results are in `evidence.json`.

Residual findings for the coordinator (existing classes, no replacement task):

- RC2 paper-risk owner: genuine policy-owner disposition remains with
  `PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122`; all five declared source
  dependencies are satisfied. This is an external owner/evidence boundary.
- RC2 release-chain owner: deploy an exact pair containing #7740 through the
  existing lane and verify all expected steady services at their expected
  versions, then collect natural paper lifecycle events. Current source merge
  and failed deploy attempts cannot prove served autoresolve.
- RC2/RC6 acceptance owner (this task): rerun the probe after deployment and
  normal daily processing; record fresh IDs, sizing, pre-broker reason/amounts,
  and passing broker/position/cash comparisons. Zero samples cannot pass.
- RC2 acceptance runner: internal Capital/telemetry owner read configuration is
  required when fresh reference-backed samples exist. The current release
  viewer alone cannot supply those source bodies. Missing configuration fails;
  this task does not introduce a new credential, endpoint or blocking gate.

Runtime budget: one bounded probe invocation per release, 72 requests / 240 s,
8 MiB response cap, three persona pages of at most 50 rows and four details
each. No persistent product writer, additional emitted event or daily storage
growth. Task evidence is a fixed git snapshot. The existing acceptance runner
owns its capped verdict and cleanup. See `acceptance/README.md` for execution.

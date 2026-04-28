# 2026-04-25 Executable Work Materialization Audit

## Purpose

This document is the repo-local audit for a recurring coordination failure mode:
we were repeatedly discussing whether a slice "could become an execution task"
without keeping one place that distinguishes:

1. work that was already materialized into named execution tasks,
2. work that is still materializable now,
3. work that is only partially taskable because external truth is missing, and
4. work that should not be treated as an ordinary auto-worker execution task.

The goal is to avoid reopening the same categorization discussion in later turns.

## Bottom Line

For the current Phase 5 wave, the broad set of repo-executable datasource,
runtime-proof, OpenClaw/EP5-prep, and Qlib/TRL activation-readiness work has
already been materialized into named execution tasks and archived as completed.

There is not a large hidden backlog of ordinary executable work still waiting to
be taskified. The remaining gaps are now concentrated in:

- human-gated EP5 closeout,
- externally provisioned credentials / entitlements / procurement,
- and explicitly deferred RL / experiment-backend lanes.

## Source of Truth Used For This Audit

- `DATA_SOURCE_SCOPE_MATRIX.md`
- `OSS_INTEGRATION_CHECKLIST.md`
- `RESEARCH_BACKEND_MATURITY_MATRIX.md`
- `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
- `ai-task-archive/tasks/APP-003-*.json`
- implementation files under:
  - `services/execution/`
  - `services/data-plane/`
  - `services/research/adapters/`

## A. Already Materialized As Execution Tasks

These items are not hypothetical. They were already converted into named
execution tasks, reviewed, and archived with terminal outcome `completed`.

| Task ID | Scope | Repo Truth | Terminal Outcome |
|---|---|---|---|
| `APP-003-DATASOURCE-US-001` | IBKR execution + Massive/Polygon US datasource boundary | US execution/data-source slice was materialized and finalized; archived commit `b9dd029` | completed |
| `APP-003-DATASOURCE-US-002` | Promote Massive/Polygon to primary US market-data path and tighten IBKR fallback boundary | US primary/fallback policy was materialized and finalized; archived commit `6a18a97` | completed |
| `APP-003-DATASOURCE-TW-001` | Shioaji + TWSE + TPEx + MOPS + TEJ Taiwan datasource integration | Taiwan governed datasource boundary was materialized and finalized; archived commit `244825c` | completed |
| `APP-003-DATASOURCE-TW-002` | Taiwan normalization pipeline | Symbol mapping / market segment / disclosure-fundamentals join was materialized and finalized; archived commit `6e8119b` | completed |
| `APP-003-DATASOURCE-CRYPTO-001` | Kraken + CoinGecko crypto integration | Kraken execution/reference boundary and CoinGecko join path were materialized and finalized; archived commit `46ed8ab` | completed |
| `APP-003-DATASOURCE-CRYPTO-002` | Kraken WebSocket realtime + execution sync | WebSocket-backed execution-sync path was materialized and finalized; archived commit `d3e3748` | completed |
| `APP-003-DATASOURCE-OPS-001` | Governed datasource secrets / env / smoke automation | Provider onboarding/env/smoke layer was materialized and finalized; archived commit `95ba6c1` | completed |
| `APP-003-RUNTIME-PROOF-001` | Runtime proof batch 1 | Consultation + knowledge runtime proof moved from `32/46` to `43/46`; archived commit `f3a7f90` | completed |
| `APP-003-RUNTIME-PROOF-002` | Runtime proof batch 2 | Remaining operator/trainer/residual proof moved from `43/46` to `46/46`; archived commit `5cc7f96` | completed |
| `APP-003-OPENCLAW-CLOSEOUT-001` | OpenClaw runtime-adoption + EP5 packet prep | Repo-authoritative operator packet / human-gate input bundle was materialized and finalized; archived commit `79430a9` | completed |
| `APP-003-QLIB-ACTIVATION-001` | Qlib production activation gate | Qlib activation gate-clearing work was materialized; truth remains smoke-tested and blocked on data gates; archived commit `9ee259f` | completed |
| `APP-003-TRL-ACTIVATION-001` | TRL production activation gate | TRL activation gate-clearing work was materialized; truth remains smoke-tested and runtime-data gated | completed |

## B. What Landed In Code For Those Tasks

The archived tasks above are backed by repo-local implementation, not only by
task metadata.

### Execution adapters

- `services/execution/ibkr_adapter.py`
- `services/execution/shioaji_adapter.py`
- `services/execution/kraken_adapter.py`

### Data-plane helpers

- `services/data-plane/us_equity_reference.py`
- `services/data-plane/taiwan_reference.py`
- `services/data-plane/crypto_reference.py`

### Research/reference adapters

- `services/research/adapters/coingecko_client.py`
- `services/research/adapters/taiwan_market_client.py`

### Canonical scope / defaults

- `DATA_SOURCE_SCOPE_MATRIX.md`
- `services/control-plane/bff/settings_store.py`

### Frontend provider inventory

The front-end settings surface is already aligned to the governed provider set
and is no longer using the old placeholder/demo provider list:

- `../front-ai-trading-system/src/pages/settings/sections/DataSourceSettings.tsx`

## C. Materialization Status By Category

This table is the main answer to "what can be materialized into execution tasks
and what cannot."

| Work Item | Can materialize as ordinary execution task? | Current status | Why |
|---|---|---|---|
| IBKR US execution boundary | yes | already materialized (`APP-003-DATASOURCE-US-001`) | Repo-local code/tests/contracts were executable |
| Massive/Polygon US primary market-data path | yes | already materialized (`APP-003-DATASOURCE-US-001`, `US-002`) | Client/boundary/policy work was repo-executable |
| Shioaji TW execution + quote boundary | yes | already materialized (`APP-003-DATASOURCE-TW-001`) | Repo-local adapter / boundary work was executable |
| TWSE OpenAPI client/boundary | yes | already materialized as part of `APP-003-DATASOURCE-TW-001` | Public official-reference path and schema work were repo-executable |
| TPEx E-Data client/boundary | yes | already materialized as part of `APP-003-DATASOURCE-TW-001` | Public official-reference path and schema work were repo-executable |
| MOPS disclosure client/boundary | yes | already materialized as part of `APP-003-DATASOURCE-TW-001` | Public disclosure path and schema work were repo-executable |
| TEJ API client/boundary | yes, with truthful entitlement caveat | already materialized as part of `APP-003-DATASOURCE-TW-001` | Client/governance/join code was repo-executable even though real TEJ credentials remain external |
| Taiwan multi-source normalization pipeline | yes | already materialized (`APP-003-DATASOURCE-TW-002`) | Join/symbol/segment/disclosure pipeline was repo-executable |
| Kraken REST execution + market data | yes | already materialized (`APP-003-DATASOURCE-CRYPTO-001`) | Repo-local adapter/join/test work was executable |
| Kraken WebSocket execution sync | yes | already materialized (`APP-003-DATASOURCE-CRYPTO-002`) | WebSocket/sync/replay logic was repo-executable |
| CoinGecko enrichment | yes | already materialized (`APP-003-DATASOURCE-CRYPTO-001`) | Reference-enrichment logic was repo-executable |
| Datasource env/secrets/smoke automation | yes | already materialized (`APP-003-DATASOURCE-OPS-001`) | Env template / smoke / runbook work was repo-executable |
| Runtime-proof expansion | yes | already materialized (`APP-003-RUNTIME-PROOF-001`, `002`) | Proof packet and evidence reconciliation were repo-executable |
| OpenClaw/EP5 repo-authoritative packet closeout | yes, for prep only | already materialized (`APP-003-OPENCLAW-CLOSEOUT-001`) | Repo-side packetization/prep was executable before human gate |
| Qlib activation gate-clearing | yes | already materialized (`APP-003-QLIB-ACTIVATION-001`) | Gate-clearing evidence/packet work was repo-executable |
| TRL activation gate-clearing | yes | already materialized (`APP-003-TRL-ACTIVATION-001`) | Gate-clearing evidence/packet work was repo-executable |

## D. Conditionally Taskable But Not Truthfully Auto-Closable

These can still have code/support work, but they cannot be truthfully closed by
ordinary auto workers without external operator truth.

| Work Item | Why it is not fully auto-closable |
|---|---|
| IBKR real live entitlement / account provisioning | Requires real account, permissions, and operator-owned broker credentials |
| Shioaji production auth / CA / IP allowlist | Requires real broker account, certificate chain, and production enrollment |
| TEJ paid entitlement activation | Requires paid TEJ key / contract entitlement |
| Massive/Polygon paid quota / contract bring-up | Requires commercial plan / quota ownership |
| Kraken live-trading credential bring-up | Requires operator-owned real trading credentials and venue policy decisions |
| Licensed Taiwan realtime feed procurement | Requires commercial / legal procurement outside repo-local code execution |

These items are suitable for operator runbooks, secret materialization helpers,
or readiness packets, but not for a "code-only auto worker closes everything"
claim.

## E. Should Not Be Materialized As Ordinary Execution Tasks

These are either explicitly deferred by canonical docs or inherently human-gated.

| Item | Status | Why not an ordinary execution task |
|---|---|---|
| `EP5-002` final canary/live closeout | human-gated | Requires real credentials, runtime tuple choice, canary approval, rollback signoff, and operator evidence |
| `FinRL` | deferred (`criteria-defined`) | Current wave explicitly keeps RL closed until Qlib approval + 3 months stable evidence |
| `RLlib` | deferred (`version-pinned`) | Remains behind the reopened RL gate and FinRL first-lane proof |
| `Ray Tune` | deferred (`version-pinned`) | Coupled to RLlib follow-on lane; not active in the current wave |
| `W&B` | deferred (`criteria-defined`) | Re-entry gate unmet; MLflow-history/operator/adapter/network conditions are still open |
| Options/futures specialty analytics vendors | not in current governed wave | New scope, not current executable priority |
| Taiwan derivatives greeks / IV specialty vendors | not in current governed wave | New scope, not current executable priority |
| Crypto OI / liquidation specialty vendors | not in current governed wave | New scope, not current executable priority |

## F. What This Means Operationally

The practical conclusion for the current wave is:

1. The first-wave and second-wave datasource work was already fully
   materialized into execution tasks.
2. Runtime-proof expansion was already materialized and completed.
3. Qlib / TRL activation-readiness work was already materialized and completed.
4. OpenClaw / EP5 prep was already materialized and completed up to the repo
   boundary.
5. The remaining work is no longer "missing task materialization"; it is either:
   - an external credential / entitlement / procurement dependency, or
   - a human gate, or
   - an explicitly deferred lane.

## G. Recommended Rule Going Forward

To avoid repeating the same conversation later:

1. Before proposing new execution tasks, first check whether a scope already has
   an archived `APP-003-*` execution task.
2. If the remaining gap is only credentials, procurement, or human approval,
   do not pretend it is a missing ordinary execution task.
3. If the remaining gap is repo-local code/tests/packetization and no archived
   execution task exists, then materialize it immediately.

## H. Final Judgment

For the datasource and activation-readiness wave discussed in this thread,
there is no additional large bucket of ordinary executable work that still needs
to be materialized into execution tasks.

The materialization gap is effectively closed.

What remains is:

- human-gated `EP5-002` closeout,
- external provider credential / entitlement / procurement work,
- and the explicitly deferred RL / W&B lanes.

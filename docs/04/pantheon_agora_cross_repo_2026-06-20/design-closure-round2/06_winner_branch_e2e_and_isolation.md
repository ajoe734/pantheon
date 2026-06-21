# F — Winner-Branch E2E and Cross-Repo / Isolation Acceptance

## F1. Canonical winner-branch E2E, steps 1–11

### Step 1 — Identity and private servant

- Ensure the authenticated Agora user has one user-private servant.
- Assert persona class/scope and no execution authority.
- Assert User B cannot resolve User A's servant.

### Step 2 — Create workshop with expert hypothesis

Submit the full winner-branch hypothesis:

```text
related-party holdings
candidate branch mapping
historical branch entry/exit profitability
branch migration to alternate sell branches
unmatched large selling branches
holding-change correlation
event lead analysis over 3–6 months
winner-branch scoring
probability/EV
position/add/leverage discussion
similar Alpha research
```

Assertions:

- raw initial message is encrypted/private;
- event row stores private ref + redacted summary;
- Management cannot read raw text.

### Step 3 — Servant reconstruction and gap analysis

The servant emits:

- causal-chain reconstruction;
- explicit vs inferred definitions;
- uncertainty/contradiction set;
- completeness snapshot;
- one Next-Best-Question.

Assert typed cards and ordered SSE events.

### Step 4 — First StrategySpec draft

After sufficient user confirmation:

- create one Registry draft;
- create one workshop-version link;
- do not copy StrategySpec truth into workshop storage;
- assert strategy/Registry/workshop IDs and lineage.

### Step 5 — Research plan

The servant proposes a plan including:

- data validation;
- winner-branch historical scoring;
- related-party/branch probability mapping;
- branch migration analysis;
- event lead/placebo analysis;
- probability/EV calibration;
- cost/liquidity/capacity;
- alternative Alpha/source research;
- robustness/OOS.

Trader approves the plan.

### Step 6 — Research execution

Dispatch typed stages to governed backends.

Assertions:

- real vs fixture/stub is labelled;
- no silent fallback;
- progress events are ordered/replayable;
- every output has evidence/artifact refs;
- no order route exists.

### Step 7 — Results and patch proposal

The result-synthesis skill produces:

- findings;
- limitations;
- recommended parameter/model changes;
- a `VersionPatchProposal`.

Validate and accept the proposal to create a new immutable Registry draft.

### Step 8 — Compare and readiness

Compare base and candidate versions.

Assertions:

- predicted and observed results are separate;
- OOS/cost/capacity/regime checks are visible;
- readiness transitions are evidence-based;
- fixture/stub cannot satisfy full validation.

### Step 9 — Select execution candidate(s)

The trader may select:

- one primary strategy version;
- multiple strategy variants for shadow comparison;
- no version.

Selection does not promote to live.

### Step 10 — Candidate pool and Trading Room workspace

- Generate CandidatePool with the approved scoring recipe.
- Review/add/remove/park/research/shadow candidates.
- Generate a complete strategy-specific DashboardRecipe.
- Trader adjusts widgets/layout and accepts a version.
- Trading Room becomes available only if its gate is ready.

### Step 11 — Decision event and governed intent

- Trigger an entry/add/reduce/exit/review event.
- Show confidence, probability, EV, risk, evidence and invalidation.
- Trader approves/rejects/defers/modifies.
- Approval creates TradingIntent.
- Shadow starts directly as no-order evaluation, or paper/canary/live creates a request-only governed handoff.
- Assert Agora creates no broker order, RuntimeBinding or capital binding.

## F2. Cross-repo compatibility

| ID | Assertion |
|---|---|
| XR-01 | frontend manifest names the exact backend v1.3 bundle index hash |
| XR-02 | generated TypeScript records source contract commit and schema hashes |
| XR-03 | CI fails on missing required capability |
| XR-04 | CI fails on schema/OpenAPI hash drift |
| XR-05 | `execute-plans` pages use the BFF client facade; no direct API fetch |
| XR-06 | a backend additive bundle does not modify prior frozen bundle hashes |
| XR-07 | frontend build declares the minimum compatible backend bundle version |

## F3. Cross-user isolation

| ID | Assertion |
|---|---|
| ISO-U01 | User A cannot list/get/update User B workshop |
| ISO-U02 | User A cannot read User B private content, cards, SSE or replay |
| ISO-U03 | User A cannot read/modify User B DashboardRecipe |
| ISO-U04 | User A cannot read User B candidate pool, decision event or intent |
| ISO-U05 | guessed IDs return 404/403 without existence leakage |
| ISO-U06 | frontend query/cache keys include tenant + user + aggregate |
| ISO-U07 | SSE authorization is checked before connect and replay |
| ISO-U08 | idempotency keys are scoped by tenant/user/operation |

## F4. Agora vs Management isolation

| ID | Assertion |
|---|---|
| ISO-M01 | Agora user token is denied all Management command routes |
| ISO-M02 | Management projection receives redacted workshop content only |
| ISO-M03 | Management cannot decrypt private content through normal APIs |
| ISO-M04 | institutional persona receives a minimized/redacted ContextBundle |
| ISO-M05 | Agora cannot create RuntimeBinding, capital binding or broker order |
| ISO-M06 | canary/live actions are handoff requests only |
| ISO-M07 | break-glass access is separate, audited and unavailable to ordinary Management users |
| ISO-M08 | institutional learning requires consent/privacy gates and never extends raw-content retention |

## F5. App/build isolation

Short-term monorepo acceptance:

- route guards and BFF authorization are both required;
- hiding a menu is not security;
- Agora code must not call Management command clients.

Target-state acceptance after dual-entry migration:

- Agora and Management produce separate bundles;
- Agora bundle contains no Management page chunks;
- separate auth audiences and CSP;
- independent deployment manifests.

## F6. Privacy and storage

| ID | Assertion |
|---|---|
| ISO-P01 | raw workshop text is absent from DB rows/logs/traces/audit |
| ISO-P02 | private object refs are opaque |
| ISO-P03 | owner-only decrypt is audited |
| ISO-P04 | retention/expiry/delete behavior is tested |
| ISO-P05 | redaction failure is fail-closed |
| ISO-P06 | central personas cannot receive raw private prompt by default |

## F7. Event and concurrency acceptance

| ID | Assertion |
|---|---|
| EV-01 | per-workshop `sequence_no` is monotonic |
| EV-02 | delivery is at-least-once and client dedupes |
| EV-03 | Last-Event-ID replay works in the support window |
| EV-04 | replay gap returns `SSE_REPLAY_UNAVAILABLE` |
| EV-05 | stale If-Match returns 409 and has no side effect |
| EV-06 | repeated Idempotency-Key returns the prior command result |
| EV-07 | first persisted message acknowledgement meets p95 <2s target |

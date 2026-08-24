# Pantheon Current Functional GAP — 2026-08-24

Status: current code-first and runtime-backed baseline

Scope: product functionality, operability, dev deployment, functional proof, and
duplicate/dead-code disposition

Precedence: this document replaces the conclusions, not the historical evidence,
of the 2026-08-22 document in this directory.

## 1. Executive conclusion

Pantheon is not functionally closed yet. The remaining work is narrower than the
earlier audit suggested, but two P0 paths are still open:

1. the hosted Lifecycle read model still runs on the unbounded JSON projector even
   though the PostgreSQL schema and partial reader/writer implementation exist; and
2. Agora and Management functional closure is blocked by a release workflow that
   treats repeated manual authorization of an exact FE/BFF pair as a prerequisite,
   while the actual product journeys have not completed.

The correct completion path is not to add another governance layer. It is to:

- finish and activate the existing PostgreSQL Lifecycle direction on a clean branch
  from current `dev`;
- preserve the `loop-run-projector-scheduler` service identity while replacing its
  JSON persistence behavior;
- run one real canonical-telemetry backfill, cut BFF reads and readiness to
  PostgreSQL, restart and read back, then immediately delete the exact legacy JSON
  directory;
- automatically bind proof to the immutable candidate FE/BFF identities instead of
  asking the operator to approve each new SHA pair;
- run the complete Agora, Management, and Management AI paper-only journeys; and
- remove duplicate/dead paths only after their replacement journey has passed.

No GCP billing recovery is needed for this program. The current dev delivery path is
the fixed VM and SSH deployment path. Source Ingestion remains reconcile-only in dev;
a test may perform one explicit manual pull, never recurring provider egress. Live
capital actions remain prohibited.

## 2. Audit method and frozen baseline

This audit used four evidence layers. A claim is marked complete only when code,
deployed identity, runtime state, and user-visible readback agree.

| Layer | Evidence used | Baseline on 2026-08-24 |
|---|---|---|
| Backend source | Pantheon `origin/dev` | `4845f62152ed3a466fdf4b4fbec92fef1abe6e90` |
| Frontend source | `ajoe734/execute-plans` `origin/dev` | `0eec7659c9503ba3799ed5666cfa00f2b031e7fa` |
| Hosted frontend manifest | `/deployment.json` | FE `cc4007f7f78a31c73548ce85457af17a45a4c4b9`, BFF `40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0`, pair `0d36e1dd5beb48a21dc5c9f7467c3ab85f86f246d20568a58a2fb534208aafa4` |
| Hosted profile | manifest plus `/bff/version` | `standby`, read-only, real writes false, BFF version matches manifest |
| Hosted BFF readiness | `/readyz` | overall ready, but Lifecycle dependency reads JSON health and JSON stores |
| Lifecycle database | `trade_journey_projection` | schema present; controller, receipts, links, stages, journeys, loop runs, and quarantine all contain zero rows |
| Lifecycle filesystem | `/data/bff/lifecycle-projection` | approximately 21 GiB and actively used |
| Canonical work state | supervisor/V2 TaskStore | supervisor healthy; seven nonterminal product tasks |

The hosted manifest is internally consistent, but the hosted FE and BFF are both
older than their current source `dev` tips. Therefore it proves what is served, not
that the latest functional work is deployed.

## 3. Classification rules

- **Closed**: current source is deployed at an exact identity and the real product
  path passes readback/reload evidence.
- **Implemented, not accepted**: code exists, but the current hosted path has not
  demonstrated the behavior.
- **Open**: code, activation, migration, journey, or cleanup is still missing.
- **Invalid evidence**: an artifact claims a state contradicted by the hosted
  manifest, runtime, database, or a fresh readback.
- **Not a product gap**: supervisor capacity, provider quota, review ceremony,
  security hardening, or GCP billing unless it directly prevents the functional dev
  path.

Read-only panel rendering does not close a write journey. A fixture, seed import,
memory-only mutation, prebuilt object ID, or mocked provider response is not accepted
as a functional replacement for the actual path.

## 4. Current gap matrix

| ID | Severity | Gap | Current proof | Completion boundary |
|---|---:|---|---|---|
| G24-01 | P0 | Lifecycle authority remains JSON | BFF defaults to `json`; hosted PostgreSQL projection tables are empty | PostgreSQL controller caught up, BFF reads/readiness switched, restart/readback passes |
| G24-02 | P0 | Lifecycle JSON growth and stale retirement implementation | about 21 GiB active store; PR #5147 is stale, oversized, blocked, and its hosted evidence conflicts with runtime | clean current-dev implementation, exact legacy deletion after cutover, no regrowth |
| G24-03 | P0 | Functional proof is coupled to repeated manual exact-pair authorization | current workflow/task state repeatedly blocks on a pair that changes every promotion | candidate pair derived and verified automatically; bounded paper proof runs and restores read-only |
| G24-04 | P1 | Management/Management AI journey is incomplete | product panels responded, but OpenClaw returned `OPENCLAW_RESPONSES_UNREACHABLE`; no confirmed domain mutation | real provider answer, one paper-domain action exactly once, terminal readback and reload |
| G24-05 | P1 | Agora hosted write journey is incomplete | implementation PR merged; hosted deployment remains read-only and the full journey has not run | real Workshop-to-Consultation and Trading Room journey with persistence/reload |
| G24-06 | P1 | Backend/frontend consolidation is incomplete | caller inventories and deletion dispositions remain open | each candidate retained, replaced, or deleted with caller-backed evidence |
| G24-07 | P1 | Latest exact-pair hosted acceptance is incomplete | hosted pair predates both source tips | final candidate manifest, journeys, readiness, Source mode, and read-only restoration all pass |

## 5. G24-01 — Lifecycle still has two competing persistence designs

### 5.1 What exists

The repository already contains the correct relational foundation:

- [`services/trade_journey/projection_store.py`](../../../services/trade_journey/projection_store.py)
  defines the PostgreSQL controller, receipts, identity links, journeys, stages,
  loop runs, and quarantine model;
- the BFF has a PostgreSQL reader branch and checks controller status, live mode,
  checkpoint, source high watermark, backlog, quarantine, freshness, and deployment
  SHA; and
- Compose exposes reader and writer backend selectors.

This foundation should be reused, not replaced by another projector or another set of
tables.

### 5.2 What actually runs

The active Compose defaults still select:

- `PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND=json`;
- `LIFECYCLE_PROJECTOR_WRITER_BACKEND=disabled`; and
- JSON paths under `/data/bff/lifecycle-projection/current/`.

The hosted BFF readiness payload confirms those JSON stores. The relational schema's
seven tables contain zero rows. Therefore PostgreSQL is an implemented option, not
the current runtime authority.

### 5.3 Why immediate deletion alone is wrong

`loop-run-projector-scheduler` currently runs
`python -m services.trade_journey.lifecycle_projector run`, and the BFF points to its
JSON outputs. Deleting those files before cutover would remove the accepted read
surface and make readiness or product reads fail. The service key itself is also used
by deploy and probe scripts and must not be removed.

The minimum functional sequence is:

1. enable the relational worker against canonical `public.telemetry_events`;
2. backfill and catch up to the source high watermark;
3. switch BFF reads and Lifecycle readiness to the relational controller;
4. restart both processes and read back the same journey/loop identities; and
5. delete only the legacy Lifecycle JSON directory and verify it does not regrow.

This sequence is a data-correctness dependency. It does not require a seven-day soak,
a retirement HMAC, or another operator authorization.

## 6. G24-02 — The active JSON design is unbounded, and PR #5147 is not mergeable

The current Lifecycle directory is approximately 21 GiB. The largest observed
contributors were:

| Object | Observed size |
|---|---:|
| `controller_state.json` | 3,170,778,131 bytes |
| ten root temporary files | 8,640,269,155 bytes total |
| four retained generations | approximately 2.3 GiB each |

This is not merely abandoned historical data. The worker serializes and atomically
copies large full-state JSON bundles; temporary and generation copies multiply the
working set. Deleting files without changing the writer would only allow the growth
to recur.

Pantheon PR #5147 has a useful architectural direction: transactional PostgreSQL projection,
PostgreSQL BFF reads, and PostgreSQL readiness. It must not be merged as-is because:

- its head `b6c9d92ef402f07e5bc64d61b3d585652ec4b1a6` was measured 62 commits behind
  current `dev` and carries 12 unique commits;
- its 22-file, +5,149/-4,114 diff mixes the functional cutover with a 1,329-line
  retirement-HMAC CLI and about 1,969 lines of tests for that ceremony;
- canonical review is failing; and
- its evidence says hosted PostgreSQL-only restart/readback passed, while the current
  hosted runtime uses JSON and the projection tables are empty.

The correct disposition is to keep task identity `LIFECYCLE-PROJ-RETIRE-001`, rebuild
the minimal functional change from current `dev`, and use a replacement PR. Do not
mark the canonical task superseded and do not create a duplicate Lifecycle task.

## 7. G24-03 — Exact identity is necessary; repeated manual authorization is not

Two different concerns were previously combined:

- **Version binding** is necessary. Evidence must state the exact FE commit, BFF
  commit, pair ID, profile, and release candidate that produced it.
- **Manual authorization of every new pair** is not a functional requirement for a
  bounded dev-paper test. It makes a normal promotion invalidate the prior approval
  and causes the same task to block repeatedly.

The release controller should create an immutable candidate, derive both SHAs and the
pair ID, verify the deployed manifest/version, open the bounded write-proof profile,
run the installed test account, and restore the public profile to read-only in a
finally/watchdog path. Pair inputs must be generated from that candidate, not copied
from an old task packet or requested again from the operator.

The removal of repeated approval must not weaken the journey. The proof still must
use the real hosted network path, paper-only domain actions, persisted backend state,
terminal receipt, reload, and exact-pair evidence.

## 8. G24-04 — Management and Management AI are not fully operational

The latest observed journey reached real Formula, Activity, Paper Telemetry, and
Postmortem surfaces. That is useful partial evidence, but it is not completion:

- Management AI returned `OPENCLAW_RESPONSES_UNREACHABLE` instead of an actual
  provider answer;
- the deployed read-only profile exposed mutation affordances but no mutation was
  executed; and
- there is no proof of one confirmed paper-domain action, exactly-once receipt,
  terminal readback, and reload persistence.

Completion requires the actual provider route through
`POST /bff/management/nl/ask`, followed by one supported paper action. Typed
unavailability is acceptable for a feature that is genuinely unavailable, but it is
not a passing Management AI journey when the provider is expected to be configured.

`execute-plans` PR #601 is merged and supplies part of the product path.
`execute-plans` PR #613 remains open and may contain reusable evidence hardening, but
its exact-pair logic must follow the automatic candidate-binding decision in this
baseline.

## 9. G24-05 — Agora code is merged, but the real hosted journey is still absent

`execute-plans` Agora PR #612 is merged. The remaining gap is acceptance, not a
second Agora implementation. The full proof must:

1. create browser inputs through Workshop and Consultation;
2. create/use a real Trading Room pool or workspace;
3. produce the decision and performance result through backend APIs;
4. survive page reload and a fresh read; and
5. correlate request, object, receipt, FE/BFF identity, and network trace.

Fixture records, seed imports, prebuilt IDs, and memory-only UI state do not close the
task.

## 10. G24-06 — Consolidation must remove proven duplication, not working paths

The backend and frontend consolidation tasks remain open. Their audit phase can run
in parallel with Lifecycle and journey repair because it is read-only. Actual
deletion must wait until the replacement path passes the relevant hosted journey.

Every candidate must have one disposition:

- `retain`: it is the canonical implementation;
- `replace_then_delete`: callers are migrated and replacement evidence exists;
- `delete`: no runtime, test, workflow, deployment, or documentation caller remains;
- `defer`: ownership or behavior is still ambiguous.

Do not create parallel compatibility layers to avoid deciding. Do not delete code
solely because a filename looks legacy. Preserve existing task IDs
`PFG-BE-CONSOLIDATE-20260820` and `PFG-FE-CONSOLIDATE-20260820`.

## 11. G24-07 — Hosted acceptance is the final integration task

The current hosted pair is a valid read-only standby pair, but it is not the latest
source pair and has not passed all functional journeys. Final acceptance must occur
after Lifecycle cutover, Management/AI, Agora, and proven cleanup.

The accepted final state is:

- manifest FE SHA, BFF SHA, pair ID, and `/bff/version` agree;
- Lifecycle PostgreSQL controller is ready, live, caught up, and restart-readable;
- Agora and Management/AI full journeys pass on that candidate;
- Source Ingestion is reconcile-only before and after proof, with at most one manual
  pull for a test;
- no live-capital route is exercised; and
- the public frontend is returned to immutable read-only defaults.

## 12. Canonical remaining work and corrected disposition

Seven nonterminal tasks were observed. The plan reuses them; it does not supersede
their history or create duplicate task identities.

| Task | Observed state | Correct next action |
|---|---|---|
| `LIFECYCLE-PROJ-RETIRE-001` | blocked | remove soak/HMAC ceremony from critical path; implement minimal current-dev cutover and immediate post-readback cleanup |
| `PFG-BOUNDED-FUNCTIONAL-CLOSURE-PROOF-20260824` | blocked | derive pair automatically from candidate; run bounded paper proof; restore read-only |
| `PFG-MGMT-JOURNEY-E2E-20260820` | blocked | repair provider connectivity and execute full Management/AI journey |
| `PFG-AGORA-JOURNEY-E2E-20260820` | blocked | execute full hosted write journey on the bounded paper profile |
| `PFG-BE-CONSOLIDATE-20260820` | todo | run caller audit now; delete only after replacement proof |
| `PFG-FE-CONSOLIDATE-20260820` | todo | run caller audit now; delete only after replacement proof |
| `PFG-HOSTED-ACCEPT-20260820` | todo | accept the final exact candidate after all dependencies pass |

Supervisor health proves that development tooling can dispatch work; it does not
close any of these product tasks.

## 13. Closed directions that must not be reimplemented

- `execute-plans` is the active frontend repository; do not revive
  `front-ai-trading-system`.
- The PostgreSQL Lifecycle schema and `ProjectionStore` are the relational base; do
  not add a second schema/store.
- Keep the `loop-run-projector-scheduler` Compose service key; replace internals.
- Keep exact FE/BFF identity in evidence; remove only repetitive human authorization.
- `execute-plans` Agora PR #612 and Management PR #601 are foundations, not reasons
  to create new feature copies.
- Source Ingestion stays reconcile-only and manual-once for testing.
- Existing twelve-loop work is not reopened merely because final hosted acceptance
  is pending.
- GCP billing is not a deployment blocker for the fixed-VM SSH path.

## 14. Explicitly out of scope

This functional-closure program does not add security, RBAC, MFA, compliance, HA,
secret rotation, or new review-governance work. Existing controls may remain where
they do not block function. This scope also excludes live trading and any
capital-affecting action.

## 15. Evidence limitations

- Runtime values in this document are a dated snapshot and must be recaptured for
  final acceptance.
- An open PR artifact is design input, not deployed truth.
- A healthy supervisor is development-tooling evidence only.
- A successful build or remote merge is not hosted truth until the served manifest
  and BFF version identify that exact candidate.
- No claim in this document authorizes deleting the whole `bff-data` volume,
  canonical telemetry, or PostgreSQL projection tables.

## 16. Product completion definition

Pantheon is functionally closed for this program only when all seven nonterminal
tasks above are terminal with evidence, the relational Lifecycle path survives a
restart and the legacy JSON directory is gone without regrowth, both complete hosted
journeys pass, consolidation has caller-backed dispositions, and the final exact pair
is served read-only with Source reconcile-only.

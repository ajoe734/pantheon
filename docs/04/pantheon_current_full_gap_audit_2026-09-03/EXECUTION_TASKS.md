# Pantheon Structural Closure — Governed Execution Tasks

Status: dispatch catalog

Normative inputs: [REPORT.md](REPORT.md), [SA.md](SA.md), [SD.md](SD.md), and
[TRACEABILITY.md](TRACEABILITY.md).

## Global rules

- Every task uses a clean worktree and PR to `dev`.
- Owner and reviewer must be independent identities with current eligibility.
- Rebase on current `origin/dev` before validation and merge.
- Implement through canonical owners; do not add a generic facade, service
  locator, second store/journal/scheduler/projector/deploy lane.
- Move callers and delete the replaced body in the same delivery wave.
- A skipped, timed-out, fixture-only or local-only result does not close a GAP.
- Product code tasks do not authorize hosted mutation or real-capital work.
- Hosted tasks remain separately MFA-gated.

## Dependency graph

```text
PLAN-ADMIT-001
  -> STRUCT-OWNERSHIP-001
  -> ENV-STAGING-PROD-PLAN-001
      -> BFF-PACKAGE-001
  BFF-PACKAGE-001
      -> BFF-COMPOSITION-001
  BFF-COMPOSITION-001 -> BFF-DEADCODE-001
  BFF-DEADCODE-001 -> BFF-TEST-ARCH-001
  BFF-TEST-ARCH-001 -> JOURNAL-OWNER-001
  JOURNAL-OWNER-001 -> BFF-ROUTER-STRUCT-001
  BFF-ROUTER-STRUCT-001 -> DOMAIN-WRITERS-001
  DOMAIN-WRITERS-001 + JOURNAL-OWNER-001
      -> OVERLAY-RETIRE-001
      -> AGORA-CHAIN-001
  AGORA-CHAIN-001 + OVERLAY-RETIRE-001
      -> LOOP-TRUTH-001
      -> MGMT-READ-001
  MGMT-READ-001 -> FE-STRICTLIVE-001
  FE-STRICTLIVE-001 -> DEV-DELIVERY-001 -> DEV-RELEASE-HOSTED-001
  DEV-RELEASE-HOSTED-001 + LOOP-TRUTH-001 -> L12-HOSTED-001
  DEV-RELEASE-HOSTED-001 + MGMT-READ-001 + AGORA-CHAIN-001
      -> MGMT-AGORA-E2E-001
  L12-HOSTED-001 + MGMT-AGORA-E2E-001 -> STRUCT-RETIRE-001
```

## Functional task catalog

### PLAN-ADMIT-001 — independently review and merge the planning package

Owner: `Claude`; reviewer: `Antigravity`

Copy the exact audited planning files into the declared repository path,
verify their recorded SHA-256 digests, independently challenge ownership and
anti-layering decisions, then deliver them through PR. No product code change.

Acceptance: all five documents are merged unchanged or independent-reviewer-approved
corrections are documented; current baseline conflicts are refreshed; PR,
checks, independent review and merge SHA are recorded.

### STRUCT-OWNERSHIP-001 — materialize architecture authority inventories

Owner: `Antigravity`; reviewer: `Claude2`

Create and enforce aggregate ownership, mutation-to-owner, worker/lease and
symbol-disposition registries described in SD §3. Classify every mutation
route, all 208 duplicate-definition groups and 17 unreachable tails.

Acceptance: each mutable aggregate has one command/store owner; every mutation
route maps exactly once; worker subject collisions fail validation; unknown
dispositions or permanent compatibility entries fail closed.

### ENV-STAGING-PROD-PLAN-001 — design unavailable environments without provisioning

Owner: `Claude2`; reviewer: `Antigravity2`

Produce current-state-verified staging/prod SA/SD, threat/authority boundaries,
resource and cost model, exact-pair promotion/rollback plan, and separately
authorized execution packets. Do not create cloud resources, credentials,
ingress, production data paths or live-capital capability.

Acceptance: current unavailable status remains explicit; no retired environment
is reused; staging is ephemeral; production control/execution isolation and
future authorized-operator/MFA approval points are defined; follow-up tasks remain
undispatched until separately authorized.

### BFF-PACKAGE-001 — normalize imports and dependency wiring

Owner: `Codex2`; reviewer: `Claude`

Establish one BFF package root, remove production domain/router `sys.path`
mutation and dynamic namespace forwarding, including Persona symbol copying.

Acceptance: all entrypoints and tests resolve stable imports; production
domain/router path mutation and `globals()` forwarding are zero; no parallel
package tree or forwarding shell remains.

### BFF-COMPOSITION-001 — reduce BFF to an explicit composition root

Owner: `Antigravity2`; reviewer: `Codex2`

Inject domain-specific query, command and event dependencies into router
factories. Remove global `read_store`, production domain imports from `main`
and the Deployment reverse import.

Acceptance: required owners fail startup closed; optional queries degrade
explicitly; mounted route smoke completes; `main.py` owns composition only;
old global callers are deleted.

### BFF-DEADCODE-001 — remove confirmed unreachable code

Owner: `Claude2`; reviewer: `Codex`

Delete all 17 audited unreachable tails and rebase stale tests onto minimal
deprecation contracts or canonical owners.

Acceptance: control-flow scan finds no statement after unconditional terminal
branches; deprecated routes preserve only intended status/envelope; no caller
or test reaches removed bodies.

### BFF-TEST-ARCH-001 — decouple tests from composition globals

Owner: `Codex`; reviewer: `Antigravity2`

Classify 218 `main`-importing tests into composition, router, application,
adapter and hosted layers; introduce domain-specific typed fixtures and remove
global store/overlay monkeypatching outside a narrow composition allowlist.

Acceptance: route/application suites collect and finish within documented
budgets; direct composition imports only exist in the reviewed allowlist;
timeouts are hard failures.

### BFF-ROUTER-STRUCT-001 — split giant router closures by use case

Owner: `Claude`; reviewer: `Codex2`

Split Personas, Research, Agora Trading Room/Research and Strategies router
factories into cohesive resource/use-case subrouters. Move business branching
to application owners, not helper modules.

Acceptance: parent routers only include children; no proxy symbol copying;
changed factories meet the 300-line review guardrail or have a justified
cohesion exception; normalized route contracts are unchanged.

### DOMAIN-WRITERS-001 — bind every product mutation to its canonical owner

Owner: `Antigravity`; reviewer: `Claude`

Replace missing `ReadSurfacePorts` mutation calls with typed Persona, Runtime,
Deployment, Research, Job, Audit/Sponsor and Ranking owner ports.

Acceptance: no mutation verb exists on query ports; every route produces one
canonical command receipt and fresh readback; idempotency, conflict and tenant
boundaries pass; BFF-local mutation fallback is deleted.

### JOURNAL-OWNER-001 — converge Decision Journal to one writer

Owner: `Codex2`; reviewer: `Antigravity`

Apply the SD scorecard, select one durable Journal owner, fill only missing
capability, migrate rows, move Agora/Governance callers, and delete the other
implementation.

Acceptance: one schema/write owner and one event path remain; parity and
restart tests pass; old callers/table writes are zero; no replication bridge
or permanent dual write exists.

### OVERLAY-RETIRE-001 — remove process-local state authorities

Owner: `Antigravity2`; reviewer: `Claude2`

Migrate Persona, Strategy, Incident, Job and Ranking state to existing durable
owners using bounded backfill, shadow-read parity and direct cutover.

Acceptance: canonical owner is the only writer; multi-replica/restart fresh
readback passes; overlay definitions, fallback reads, mutations and tests are
deleted; rollback does not restore dual writes.

### AGORA-CHAIN-001 — complete the natural Agora owner chain

Owner: `Claude2`; reviewer: `Antigravity2`

Connect workshop request/outbox/worker, authentic research receipt, dataset and
candidate admission, Trading Room decision, one policy/consultation handoff,
and telemetry-triggered performance suggestion without a new scheduler.

Acceptance: same correlation and causation chain; real provenance is resolved
server-side; retry/DLQ/SSE cursor/restart behavior passes; no client trust bit,
fake completion or manual-only suggestion path remains.

### LOOP-TRUTH-001 — build receipt-derived twelve-loop truth

Owner: `Codex`; reviewer: `Claude2`

Build a durable read-only projector keyed by release/correlation/loop over
canonical owner receipts. Static registry supplies labels only.

Acceptance: terminal plus next-consumer receipt is required for completion;
incremental equals rebuild; backfill cannot override newer live truth;
freshness/provenance/degradation are explicit.

### MGMT-READ-001 — serve Management and AI from owner projections

Owner: `Antigravity`; reviewer: `Codex`

Replace generic store/overlay access with purpose-built Management queries and
twelve-loop observations. Preserve the Management AI product/development
boundary.

Acceptance: unavailable owners remain explicit rows; no seed/task status is
presented as product truth; authenticated query and confirmed paper-safe
command contracts pass; provider failure degrades independently.

### FE-STRICTLIVE-001 — remove live frontend fallback reachability

Owner: `Antigravity2`; reviewer: `Claude`

In `ajoe734/execute-plans`, prove and enforce strict-live BFF settings and
remove production bundle reachability to fixture, seed, mock transport and
local write-overlay code.

Acceptance: dev-target branch is `dev`; bundle/import audit is clean; browser
shows typed unavailable/degraded states; no embedded token or real-write
default; PR/check/review/merge identities are recorded.

### DEV-DELIVERY-001 — repair the existing exact-pair release controller

Owner: `Claude`; reviewer: `Antigravity`

Reconcile the authoritative environment identity and improve the existing
baseline-before-switch step so empty/HTML/invalid manifests fail with a typed
boundary error before mutation. Do not create another deploy lane.

Acceptance: one merged target identity; manifest HTTP/content/schema checks;
checksum-bound rollback baseline; workflow tests cover invalid current
manifest and exact-pair admission; no hosted mutation in this task.

### STRUCT-RETIRE-001 — final structural retirement

Owner: `Codex2`; reviewer: `Claude2`

After both hosted acceptance tasks, delete satisfied compatibility adapters,
remaining classified copied bodies, stale facade tests/references and obsolete
current-tense environment/delivery paths.

Acceptance: ownership and anti-layering gates are green; duplicate-body count
reaches reviewed zero/allowlist; no second authority remains; full local and
hosted exact-pair evidence is linked.

## Hosted tasks — separately privileged

### DEV-RELEASE-HOSTED-001

Deploy the exact accepted Pantheon/execute-plans pair through the repaired
existing lane, establish rollback baseline, run probes, switch atomically and
read back served identities. Requires `workClass=hosted` and one-shot MFA-backed
operator authorization.

### L12-HOSTED-001

On that exact pair, create one new correlation chain and execute all mandatory
twelve-loop cases without skips, recording stimulus, terminal, next-consumer
and fresh-reader identities. Paper/sandbox only.

### MGMT-AGORA-E2E-001

On the same pair, execute authenticated Management, Management AI and Agora
workshop-to-suggestion journeys; restart BFF/workers, reconnect SSE and confirm
idempotent durable readback. Paper-safe product writes only.

## Dispatch rule

The functional packet may be signed and materialized immediately. Hosted tasks
must be emitted in a separate signed packet only when valid one-shot operator
authorization is supplied. Functional task completion does not automatically
grant hosted authority.

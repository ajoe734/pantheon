# Pantheon Pre-shutdown Development GAP Report

Audit date: 2026-09-02 UTC

Delivery baseline: `origin/dev` at `4889e498fbe5c3b87e7a66b3ca19897e030bbcc1`

Release baseline: `release/v2026.09.01.2`; promotion merge on `master` is
`65bfde7675bc3226e34b81260687075356b9d3f0`.

Companion documents:

- [SA.md](SA.md)
- [SD.md](SD.md)

## 1. Purpose and evidence boundary

This report reconstructs the development direction immediately before the
2026-09-02 host interruption and turns the remaining work into explicit gaps.
It separates four different facts that must not be conflated:

1. code merged to `dev`;
2. a release promoted to `master`;
3. code committed on a remote branch but not merged; and
4. host-recovery work created after the interruption.

Git ancestry, commit bodies, merge commits, file diffs, and recorded focused
validation are the evidence. A green local test does not mean delivered. A
remote branch does not mean an open or approved PR. The local `ai-status.json`
is dated 2026-07-12 and is explicitly excluded as current task truth.

## 2. Executive result

The last delivered development line reached `dev` at 2026-09-01 23:55 UTC+8.
It established a modular BFF, live Agora readiness, contract-bound release
gates, stronger review and credential evidence, rollback fixtures, supervisor
lost-lease recovery, orphan-tolerant persona reconciliation, and task evidence
defaults. That baseline was promoted as `v2026.09.01.2`.

Four implementation lines remained outside `dev`:

| Gap | Direction | Evidence head | Disposition |
|---|---|---:|---|
| PSD-GAP-01 | final BFF composition-root cleanup | `1d7e47165` | integrate after full route/contract regression |
| PSD-GAP-02 | distillation SQLite WAL/SHM quarantine | `ab8edbcab` | integrate after corruption/restart validation |
| PSD-GAP-03 | unverified Yahoo/Anue egress and public tunnel retirement | `73f72deab` | rebase, preserve licensed sources, security review |
| PSD-GAP-04 | telemetry DLQ replay bootstrap authorization | `66845dd5d` | integrate with explicit operator-token semantics |

The interruption exposed a fifth, separate continuity gap. Runtime layout,
provider homes, command-root promotion, and the V2 task-state journal depended
on the lost host. The recovery commits on `ops/bootstrap-orchestrator-runtime`
are post-interruption work and are not part of the delivered product baseline.

## 3. Delivered capability inventory

### 3.1 BFF domain-router decomposition

Merged PRs #5501, #5468, and #5461 moved Management, Agora, and Research
behavior from the BFF composition root into canonical domain routers and
services. The delivered change added route inventory, semantic parity,
shadowing, and domain tests. `main.py` was reduced, but the final removal of
orphaned legacy handlers remained on PSD-GAP-01.

### 3.2 Agora and OpenClaw operational readiness

PR #5504 bound Agora readiness to live operational facts. PR #5503 increased
the primary OpenClaw readiness queue budget to avoid false failure while a
provider is legitimately starting. These changes establish a distinction
between loaded code, provider availability, and product readiness.

### 3.3 Release-gate contract and evidence hardening

PRs #5506, #5507, #5509, and #5511 changed release gating from loosely
interpreted health signals to declared and verified contracts:

- provider health no longer blocks a release unless the release contract says
  it is required;
- only declared readiness fields may drive the gate;
- unverified success signals are rejected;
- OpenClaw credentials are probed instead of inferred from presence;
- independent-review commit evidence is checked; and
- the new guards run in the actual Stage 0 CI matrix.

### 3.4 Rollback and worker recovery

PR #5505 repaired EP5-007 rollback-drill fixtures. PRs #5512 and #5515 made
lost-lease recovery preserve dirty WIP evidence while requeueing work into a
fresh workspace, and persisted legacy recovery receipts. This reduces both
unsafe WIP reuse and repeated recovery loops.

### 3.5 Persona and task workflow resilience

PR #5510 made persona provisioning reconciliation skip orphan records instead
of blocking the entire batch. PR #5508 seeded a default evidence-manifest path
at assignment time so review evidence has a contract from task creation.

## 4. Detailed residual gaps

### 4.1 PSD-GAP-01 — BFF composition root is not yet minimal

Branch: `origin/task/OPGAP-BFF-MAIN-ASSEMBLY-V3-20260901`

Observed delta relative to the delivery baseline: 13 files, approximately
14,388 additions and 51,771 deletions.

The branch reconstructs `main.py` as a pure composition root, mounts all domain
routers, removes the final production callers of `read_store.py`, deletes
about 37,600 lines of orphaned handler/helper bodies, connects Pack D exception
handlers, and adds a governance rollback-review endpoint.

Risk if left open:

- two conceptual BFF implementations remain visible;
- new routes may be added to the wrong owner;
- static-route shadowing and undefined-symbol regressions remain easier; and
- large stale bodies increase merge and review cost.

Closure evidence:

- exact rebase onto current `dev`;
- route inventory before and after is equivalent except declared removals;
- zero inline production `@app` route decorators in `main.py`;
- zero production callers of deleted `read_store.py`;
- BFF architecture, route resolution, normalized uniqueness, undefined-symbol,
  smoke, auth, and affected domain suites pass; and
- PR review is bound to the exact post-rebase head.

### 4.2 PSD-GAP-02 — queue recovery can reattach corrupt SQLite sidecars

Branch: `origin/task/OPGAP-DISTILL-QUEUE-WAL-SIDECAR-20260901`

The delivered implementation quarantines the main SQLite queue after
corruption but can leave `-wal` and `-shm` at the original path. Reopening the
same database may consume those stale sidecars and recreate the corruption.

Closure requires atomically identifying the database family, quarantining the
main file and any existing sidecars, creating a fresh queue, preserving a
diagnostic receipt, and proving clean restart and continued distillation.
Missing sidecars must be harmless; rename failures must fail closed without
partially claiming successful recovery.

### 4.3 PSD-GAP-03 — unverified external data egress remains schedulable

Branch: `origin/remediation/EGRESS-YAHOO-TUNNEL-001`

The delivery baseline still registers scheduled Yahoo Taiwan broker/RSS
connectors and an Anue RSS placeholder whose permitted automated retrieval was
not established. FinMind exhaustion can also advertise a Yahoo HTML fallback.
Separately, the dashboard can automatically create a public quick tunnel and
workers retain standing permission to launch it.

Closure requires:

- Yahoo Taiwan and Anue connectors are `DISABLED_BY_BUILD`;
- no default active-universe schedule or quota fallback selects them;
- TWSE, TPEx, FinMind, TEJ, SEC EDGAR, StockTwits, and OpenAlex behavior is
  unchanged;
- catalog templates may remain inert but cannot imply schedulability;
- public tunnel management defaults off; and
- automated workers cannot publish the dashboard without a new operator
  decision.

### 4.4 PSD-GAP-04 — telemetry replay aborts otherwise healthy bootstrap

Branch: `origin/fix/bootstrap-telemetry-replay-auth`

Bootstrap Step 4 calls `/api/telemetry/replay` without a Bearer credential even
though the route requires operator/admin authority. The observed result is HTTP
401 after the service fleet is healthy, so Step 5 and successful completion are
never reached.

The service token is not sufficient because it has no operator role. Bootstrap
must never mint its own privileged assertion.

Closure semantics:

- caller-supplied `PANTHEON_TELEMETRY_OPERATOR_TOKEN` is forwarded only to the
  replay invocation;
- tenant resolution follows the deployed telemetry tenant contract;
- no token means a visible best-effort skip and successful continuation;
- a supplied but rejected token remains a hard failure;
- `--skip-telemetry-replay` remains an explicit bypass; and
- logs do not expose the credential.

### 4.5 PSD-GAP-05 — development control plane was host-bound

Post-interruption branch: `ops/bootstrap-orchestrator-runtime`

The lost host contained uncommitted operational prerequisites: provider homes
under `/home/lupin`, deployment-root assumptions, an immutable command-root
layout, local bridge keys, watchdog installation, and the authoritative V2
task-state journal. A new host therefore could not reconstruct the supervisor
from the repository alone.

The recovery direction currently includes machine-neutral provider homes,
`PANTHEON_DEPLOY_ROOT`, an idempotent runtime bootstrap, command-root-based
health verification, and a genesis tool that writes only to a genuinely empty
journal. This line must remain development-tooling work and must not create a
fallback from `ai-status.json`.

## 5. Cross-gap dependencies

| Predecessor | Dependent work | Reason |
|---|---|---|
| restore canonical task authority | all governed integration | delivery must not rely on stale local projection |
| PSD-GAP-03 | hosted bootstrap/acceptance | startup must not create unapproved egress |
| PSD-GAP-04 | repeatable dev bring-up | healthy fleet must reach bootstrap completion |
| PSD-GAP-01 | final BFF hosted regression | exact composed route surface must be tested |
| PSD-GAP-02 | sustained ingestion soak | queue recovery must survive restart before closeout |

PSD-GAP-01 and PSD-GAP-02 can be implemented independently. PSD-GAP-03 and
PSD-GAP-04 should be accepted before treating a rebuilt host as an operational
baseline. PSD-GAP-05 is enabling infrastructure, not proof that the product is
deployed or usable.

## 6. Priority and closure order

1. Recover the authoritative development-tooling runtime without reviving stale
   task truth (PSD-GAP-05).
2. Close unapproved egress and tunnel defaults (PSD-GAP-03).
3. Make bootstrap completion authorization-correct (PSD-GAP-04).
4. Land the small, isolated WAL/SHM recovery fix (PSD-GAP-02).
5. Rebase and land the high-churn BFF composition cleanup (PSD-GAP-01).
6. Deploy the exact accepted `dev` head and run hosted acceptance.

## 7. Program-level acceptance

The pre-shutdown program is closed only when:

- every residual branch is either merged through a validated PR or explicitly
  superseded with evidence;
- current `dev`, release, and hosted deployment identities are exact and
  recorded separately;
- no default schedule performs unverified Yahoo/Anue retrieval;
- no unattended process opens the dashboard to a public tunnel;
- bootstrap completes with no replay token and fails on an explicitly rejected
  replay token;
- corruption recovery proves the SQLite main/WAL/SHM family is isolated;
- `main.py` is a composition root and the route surface passes regression;
- canonical task truth is read from V2 TaskStore, never the stale dashboard;
  and
- product readiness, development-tooling health, and deployment identity are
  reported as separate acceptance planes.

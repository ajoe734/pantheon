# EVOCHAIN-003: Postmortem Publisher on Incident Resolution

Status: remediation implemented; Claude re-review pending

Owner: Codex2

Reviewer: Claude

Merge target: `dev`

## Acceptance Boundary

This task closes the missing producer chain:

1. the first transition of an incident into `resolved` or `closed` emits one
   durable `incident.resolved` event;
2. the postmortems consumer admits the complete event envelope and creates one
   deterministic postmortem record;
3. publishing that postmortem calls the unchanged pure
   `services.evolution.postmortem_bridge.on_postmortem_published` contract;
4. when the bridge returns a corrective action, the postmortems worker sends a
   self-contained `postmortem.published` delivery to the existing generic
   `POST /api/evolution/proposals` endpoint; and
5. exact retries do not create a second postmortem or proposal, while divergent
   replays fail closed.

The bridge module itself is intentionally unchanged.

## Delivered Behavior

### Durable producer boundaries

`services/foundation/reliable_delivery.py` provides shared JSON/Postgres
outbox and inbox primitives. JSON writes use a process lock plus `fcntl`, a
temporary file, `fsync`, atomic replacement, and directory `fsync` so
concurrent writers cannot silently replace one another.

Incident and postmortem status routes use an equivalent durable-recovery
protocol around their domain transition:

- persist an outbox record deterministically keyed to the immutable delivery
  intent before changing domain state;
- reject the transition with `503` if preparation fails;
- persist the domain transition and activate the prepared record;
- retain the prepared record and return a recoverable `503` if activation
  fails; and
- reconcile prepared records whose domain transition is visible before every
  worker pass, including after restart.

Domain writes restore their guarded pre-write in-memory snapshot if JSON or
Postgres persistence raises, and reconciliation rereads the backing store.
This prevents reconciliation from observing a terminal state that existed only
in process memory. Reusing a deterministic outbox identity compares the full
stable event semantics, including payload, producer, schema, owner, and
transition predicate; a divergent snapshot fails closed while a transport
retry reuses the already persisted canonical trace.

Postgres domain writes additionally use explicit-target, row-scoped JSONB
compare-and-set. Every domain mutation names its owned aggregate type, record
ID, and expected snapshot; the store never infers a target by diffing its
process-wide cache. Unrelated-row refresh/ABA activity therefore cannot be
mistaken for a local write, and a competing service instance cannot replace a
terminal transition or its first `resolved_at` from a stale snapshot.

Postmortem mutation additionally locks and compares the parent-incident JSONB
snapshot with `SELECT ... FOR SHARE`, then CAS-writes the postmortem in the
same Postgres transaction. Draft generation passes the exact source draft as
its expected snapshot, so an operator edit between merge calculation and save
returns retryable `503` instead of being overwritten. The successful publish
persists a `published_event_id` commit marker with the single effective
`published_at`; reconciliation selects that marker and snapshot, while a later
legitimate parent transition cannot strand the historical event.

The terminal-boundary guard means `resolved` replay and `resolved` → `closed`
do not enqueue another logical event. The original `resolved_at` value is also
preserved. The deterministic incident event represents the terminal boundary,
not the attempted status, and keeps a stable legacy-compatible `resolved`
classification. If a status CAS loses, a visible winning terminal transition
activates the record; otherwise the route preserves the inert intent. It is
never check-then-deleted underneath a concurrent winner, and a later direct
close reuses and activates the same intent without an identity collision.

Published postmortems are terminal. Exact publication replay preserves the
original timestamp and event marker; regression or republication with a new
timestamp/event identity fails closed.

Both delivery workers preserve the full foundation `EventEnvelope` across the
HTTP boundary and derive its environment from the incident deployment stage.
Poll interval, maximum attempts, and exponential-backoff base are configurable
through:

- `INCIDENTS_OUTBOX_POLL_SECONDS`
- `INCIDENTS_OUTBOX_CLAIM_SECONDS`
- `INCIDENTS_OUTBOX_MAX_ATTEMPTS`
- `INCIDENTS_OUTBOX_BACKOFF_BASE_SECONDS`
- `POSTMORTEMS_OUTBOX_POLL_SECONDS`
- `POSTMORTEMS_OUTBOX_CLAIM_SECONDS`
- `POSTMORTEMS_OUTBOX_MAX_ATTEMPTS`
- `POSTMORTEMS_OUTBOX_BACKOFF_BASE_SECONDS`
- `POSTMORTEMS_INBOX_CLAIM_SECONDS`

Permanent validation/conflict responses and exhausted transient retries move a
record to the durable dead-letter state. Redrive is fail-closed until its
service token is configured, and requires an operator or risk-owner identity,
approval reference, and reason:

- `INCIDENTS_OUTBOX_REDRIVE_TOKEN`
- `POSTMORTEMS_OUTBOX_REDRIVE_TOKEN`

### Consumer admission and idempotency

The postmortems first-hop consumer validates the complete incident event and
atomically reserves its event ID plus idempotency key before mutating domain
state. An exact concurrent caller observes that reservation and retries with
`503`; mere postmortem existence is never treated as proof that the event was
applied. Only a durable `applied` inbox receipt permits a `200` replay.
Reservations carry an expiring claim, so a process crash can be reclaimed;
receipt CAS contention/disappearance is also retryable rather than a semantic
`409`. Receipt completion must CAS the exact reservation snapshot/token handed
to that claimant, so an expired claimant cannot finalize its successor's
lease. Semantic checksum or applied-result divergence still fails closed.

One postmortem is enforced per incident for cooperating JSON/Postgres writers.
The consumer reuses an existing manual/legacy postmortem and records its actual
result reference. Generated IDs preserve legacy IDs for already-safe incident
identifiers and append a digest when sanitization is needed, preventing values
such as `a b` and `a-b` from collapsing onto one record.

On publish, the caller invokes `on_postmortem_published`. The caller adapter
keeps that pure contract authoritative while converting the bridge's legacy
high-severity `rollback` output to the currently valid generic proposal action
`flag_for_review`; the original bridge action and cooldown are preserved in
proposal metadata. A bridge `None` result is an audited no-op and produces no
proposal.

The evolution generic proposal route requires the `postmortem-svc` producer and
complete postmortem/incident snapshots, then validates that the outer request,
event identity, the snapshot's `published_event_id` commit marker, embedded
proposal, and linkage agree. Its durable inbox binds event ID and idempotency
key to one immutable proposal identity. Exact retries return `200` without
resetting review/approval state; divergent reuse returns `409`. If a crash
persisted the matching decision but not its receipt, the next retry records the
receipt without overwriting the decision. Omitting `delivery_event` cannot
bypass that reservation or overwrite an existing decision ID.

### Concurrent JSON and Postgres persistence

The shared incident/postmortem domain store now serializes cross-instance JSON
writes, reloads the latest snapshot while locked, and persists with atomic
replacement plus `fsync`. Dedicated tests exercise both this store and the
shared reliable-delivery record store with concurrent writers.

Delivery workers CAS-claim due records with an expiring lease before issuing
HTTP. Success/failure completion is conditional on the persisted claim token,
so an expired/stale worker cannot replace a newer `published` result with
`failed` or `dead_lettered`. Prepare/activate retries also preserve an already
live claim instead of clearing its token. Inbox reservation completion is a
monotonic CAS from `reserved` to `applied`.

The canonical `EvolutionDecisionStore` and proposal inbox now use path-scoped
thread locks, `fcntl`, reload-before-write, optimistic stale-writer detection,
atomic replacement, file/directory `fsync`, and in-memory rollback. Concurrent
two-instance tests and restart/write-failure tests prove that proposal
admission and review state remain durable.

## Verification Evidence

The current post-PR-#3682 remediation branch (built from checkpoint
`4e5562d42`) passed this focused suite:

```sh
INCIDENTS_DATA_DIR=/tmp/evochain003-root-focused-final2-$$ \
POSTMORTEMS_DATA_DIR=/tmp/evochain003-root-focused-final2-$$ \
PANTHEON_RUNTIME_MANAGER_URL=http://runtime-manager.test \
PANTHEON_RUNTIME_MANAGER_TOKEN=test-token \
TEST_DATABASE_URL="${TEST_DATABASE_URL:?set isolated Postgres test DSN}" \
/tmp/evochain-003-venv/bin/python -m pytest -q \
  services/foundation/test_reliable_delivery.py \
  services/foundation/tests/test_control_plane_postgres_owner_stores.py \
  services/incident/test_incident_store_concurrency.py \
  services/incident/test_pg_store_integration.py \
  services/incidents/test_evochain_003_delivery.py \
  services/incidents/test_evochain_003_compose.py \
  services/postmortems/test_evochain_003_delivery.py \
  services/postmortems/test_main_routes.py
# 95 passed, 4 warnings in 29.88s
```

The new regressions cover explicit-target row-level Postgres stale-writer and
cross-row ABA rejection, shared read/write exclusion, transactional parent
checks, exclusive outbox claims, stale completion monotonicity, first-hop
reservation/reclaim, manual-result reuse, stale-draft preservation,
prepared-intent recovery, receipt CAS-loss classification, and published
terminal semantics. Commit `4e5562d42` is the pre-final remediation checkpoint,
not the commit that produced this expanded evidence.

Real two-connection Postgres proof runs against an isolated per-test schema:

```sh
TEST_DATABASE_URL="${TEST_DATABASE_URL:?set isolated Postgres test DSN}" \
/tmp/evochain-003-venv/bin/python -m pytest -q \
  services/incident/test_pg_store_integration.py -vv
# 2 passed in 3.19s
```

It proves that a parent commit between domain validation and publication makes
the transactional parent predicate reject without publishing, and that two
different postmortem IDs racing for one incident produce exactly one durable
commit.

The final broad service/governance regression and explicit independent-process
chain also pass:

```sh
INCIDENTS_DATA_DIR=/tmp/evochain003-root-broad-final-$$ \
POSTMORTEMS_DATA_DIR=/tmp/evochain003-root-broad-final-$$ \
PANTHEON_RUNTIME_MANAGER_URL=http://runtime-manager.test \
PANTHEON_RUNTIME_MANAGER_TOKEN=test-token \
/tmp/evochain-003-venv/bin/python -m pytest -q \
  services/incidents services/postmortems services/incident services/evolution \
  services/foundation/test_reliable_delivery.py \
  services/foundation/tests/test_control_plane_postgres_owner_stores.py \
  services/control-plane/governance/test_evolution_decision.py \
  services/control-plane/governance/test_evolution_dispatcher_invariants.py
# 546 passed, 2 skipped, 4 warnings in 115.58s

INCIDENTS_DATA_DIR=/tmp/evochain003-root-chain-final-$$ \
POSTMORTEMS_DATA_DIR=/tmp/evochain003-root-chain-final-$$ \
PANTHEON_RUNTIME_MANAGER_URL=http://runtime-manager.test \
PANTHEON_RUNTIME_MANAGER_TOKEN=test-token \
/tmp/evochain-003-venv/bin/python -m pytest -q \
  services/postmortems/test_evochain_003_http_chain.py -vv
# 1 passed in 2.53s

docker compose config --quiet
PANTHEON_RUNTIME_MANAGER_URL=http://runtime-manager:8081 \
  PANTHEON_RUNTIME_MANAGER_TOKEN=runtime-test-token \
  docker compose -f docker-compose.control.yml config --quiet
# both passed
```

`sha1sum services/evolution/postmortem_bridge.py` and the same file at current
`origin/dev` both resolve to
`b68b4924a6e59ca472fe8103804b2b82c3985d7d`; this task does not modify the
bridge.

The following evidence predates the current remediation and remains useful as
the prior full-chain baseline. It does not constitute approval of the new
anchor:

The final expanded runtime regression command passed in an isolated
system-site-packages venv with the service runtime dependency installed:

```sh
python3 -m venv --system-site-packages /tmp/evochain-003-venv
/tmp/evochain-003-venv/bin/python -m pip install 'uvicorn>=0.30,<1'
/tmp/evochain-003-venv/bin/python -m pytest -q \
  services/incidents services/postmortems services/incident services/evolution \
  services/control-plane/governance/test_evolution_decision.py \
  services/control-plane/governance/test_evolution_dispatcher_invariants.py
# 464 passed, 4 warnings in 111.94s
```

The root test requirements now include the same `uvicorn` runtime declared by
the three services, so the subprocess integration test runs rather than skips
in branch CI. The warnings are existing FastAPI `on_event` deprecation
warnings. Additional integration and configuration evidence:

```sh
/tmp/evochain-003-venv/bin/python -m pytest -q \
  services/postmortems/test_evochain_003_http_chain.py -vv
# 1 passed

docker compose config --quiet
docker compose -f docker-compose.control.yml config --quiet
# both passed
```

The subprocess full-chain test starts independent incidents, postmortems, and
evolution Uvicorn processes against their shared JSON volumes. It drives the
real HTTP URLs and background workers from incident resolution through
postmortem creation/publication and generic proposal admission, replays both
persisted envelopes over HTTP, and asserts one postmortem plus one proposal.

Task anchors retained for review:

- `32ae7c380` — durable delivery boundaries
- `200165804` — evolution delivery admission
- `9abbd6937` — concurrent store and full-chain proof
- `99a493bf2` — crash-safe admission, commit markers, and real HTTP proof

## Delivery History and Pending Re-review

Claude's approval of the PR #3627 implementation was later superseded by
additional concurrency review and follow-up changes. It is retained as history,
not treated as the current reviewer gate.

Implementation PR
[#3627](https://github.com/ajoe734/pantheon/pull/3627) merged into `dev` on
2026-07-14 at merge commit
`572aa0d34d3db878560af972414448d0e117054f`. Its reviewed head was
`eebf1333987990e14d43f7a509bd5be62b7bccca`. The required Commit trailers,
Runtime mirror guard, and Smoke acceptance checks all passed; the orchestrator
forwarding checks also passed.

After synchronizing the task branch with the then-current `origin/dev`, the
owner reran the expanded suite with Uvicorn available:

```sh
/tmp/evochain-003-venv/bin/python -m pytest -q \
  services/incidents services/postmortems services/incident services/evolution \
  services/control-plane/governance/test_evolution_decision.py \
  services/control-plane/governance/test_evolution_dispatcher_invariants.py
# 465 passed, 4 warnings in 163.95s
```

This run included and passed the independent-process HTTP chain. The four
warnings remain the existing FastAPI `on_event` deprecation warnings. A final
diff check also confirmed that
`services/evolution/postmortem_bridge.py` is unchanged by the implementation.

PR [#3682](https://github.com/ajoe734/pantheon/pull/3682) then merged follow-up
commit `821d4a3b5` into `dev` at merge commit `dfb6628a3`. A later Codex review
identified the five P1 findings materialized in
`support/reviews/EVOCHAIN-003-review-codex.md`: database-level CAS, claimed and
monotonic delivery, losing prepared-intent repair, published terminal guards,
and control-compose/runtime evidence. Codex2 checkpointed the initial repair in
anchor `4e5562d42` and completed the expanded adversarial remediation on the
current task branch. Claude re-review is still required; owner closeout and
`done` have not occurred.

## Operational Notes and Residual Risks

- Compose supplies `POSTMORTEMS_URL` and `EVOLUTION_URL`. The control stack now
  forwards both `PANTHEON_RUNTIME_MANAGER_URL` and
  `PANTHEON_RUNTIME_MANAGER_TOKEN` to incidents and postmortems; no
  runtime-manager container is added to VM-1.
- At-least-once HTTP delivery remains intentional. Consumer inboxes provide the
  once-per-logical-event outcome.
- Redrive endpoints remain unavailable until operators configure their token;
  this is a deliberate fail-closed default.
- Activation failures retain a prepared record for reconciliation after the
  domain commit. A losing nonterminal incident CAS also preserves its stable,
  inert terminal-boundary intent so no cleanup can race a concurrent winner.
- The remaining P2 hardening opportunity is a schema-level unique incident key
  plus migration policy for legacy duplicate postmortems. The implemented
  owner paths enforce the invariant transactionally today, and real
  two-connection tests prove the relevant lock ordering.
- The legacy `/api/evolution/proposals/from-postmortem-published` route remains
  for compatibility, but this producer chain uses the required generic route.
- BFF incident command stubs are outside this task. The proved entry point is
  the canonical incidents service status route.

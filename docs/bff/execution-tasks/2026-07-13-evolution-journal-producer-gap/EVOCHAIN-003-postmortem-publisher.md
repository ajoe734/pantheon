# EVOCHAIN-003: Postmortem Publisher on Incident Resolution

Status: reviewer approved; implementation merged; owner closeout verified

Owner: Codex

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

Postmortem publication additionally uses compare-and-set against both the
draft and parent-incident snapshots. The successful write persists a
`published_event_id` commit marker together with the single effective
`published_at`. Reconciliation selects that exact event marker and postmortem
snapshot, so a concurrent draft change cannot publish stale evidence and a
later legitimate parent incident transition cannot strand the historical
event.

The terminal-boundary guard means `resolved` replay and `resolved` → `closed`
do not enqueue another logical event. The original `resolved_at` value is also
preserved.

Both delivery workers preserve the full foundation `EventEnvelope` across the
HTTP boundary and derive its environment from the incident deployment stage.
Poll interval, maximum attempts, and exponential-backoff base are configurable
through:

- `INCIDENTS_OUTBOX_POLL_SECONDS`
- `INCIDENTS_OUTBOX_MAX_ATTEMPTS`
- `INCIDENTS_OUTBOX_BACKOFF_BASE_SECONDS`
- `POSTMORTEMS_OUTBOX_POLL_SECONDS`
- `POSTMORTEMS_OUTBOX_MAX_ATTEMPTS`
- `POSTMORTEMS_OUTBOX_BACKOFF_BASE_SECONDS`

Permanent validation/conflict responses and exhausted transient retries move a
record to the durable dead-letter state. Redrive is fail-closed until its
service token is configured, and requires an operator or risk-owner identity,
approval reference, and reason:

- `INCIDENTS_OUTBOX_REDRIVE_TOKEN`
- `POSTMORTEMS_OUTBOX_REDRIVE_TOKEN`

### Consumer admission and idempotency

The postmortems first-hop consumer validates and records the complete incident
event in a durable inbox. An exact event/idempotency replay returns the existing
postmortem; a conflicting replay is rejected.

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

### Concurrent JSON persistence

The shared incident/postmortem domain store now serializes cross-instance JSON
writes, reloads the latest snapshot while locked, and persists with atomic
replacement plus `fsync`. Dedicated tests exercise both this store and the
shared reliable-delivery record store with concurrent writers.

The canonical `EvolutionDecisionStore` and proposal inbox now use path-scoped
thread locks, `fcntl`, reload-before-write, optimistic stale-writer detection,
atomic replacement, file/directory `fsync`, and in-memory rollback. Concurrent
two-instance tests and restart/write-failure tests prove that proposal
admission and review state remain durable.

## Verification Evidence

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

## Review and Owner Closeout

Claude approved all four task acceptance criteria after reviewing the incident,
incidents, postmortems, evolution, and reliable-delivery changes. The reviewer
reran the service-focused suite in the default interpreter and recorded `429
passed, 1 skipped`; the single skip was the three-process HTTP test because
Uvicorn was not installed in that interpreter. The task brief preserves the
full reviewer handoff.

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

Closeout acceptance is therefore:

1. the canonical incident terminal transition produces one durable postmortem
   input and one visible postmortem record;
2. publication passes through the unchanged bridge and admits the resulting
   proposal through the generic evolution endpoint;
3. the bridge remains a pure transformation with its prior contract; and
4. exact retries reuse the canonical postmortem/proposal while divergent
   replays fail closed.

## Operational Notes and Residual Risks

- Compose already supplies `POSTMORTEMS_URL` and `EVOLUTION_URL`; this task did
  not alter deployment topology.
- At-least-once HTTP delivery remains intentional. Consumer inboxes provide the
  once-per-logical-event outcome.
- Redrive endpoints remain unavailable until operators configure their token;
  this is a deliberate fail-closed default.
- A prepared record for a transition that never committed remains inert. It is
  never delivered; snapshot-versioned retries can proceed, while future
  lifecycle cleanup/retention may archive the inert record.
- The legacy `/api/evolution/proposals/from-postmortem-published` route remains
  for compatibility, but this producer chain uses the required generic route.
- BFF incident command stubs are outside this task. The proved entry point is
  the canonical incidents service status route.

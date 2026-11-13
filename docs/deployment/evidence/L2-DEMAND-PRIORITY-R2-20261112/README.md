# L2 demand priority and durable cap deferral

Task: `L2-DEMAND-PRIORITY-R2-20261112` · owner: Codex2 · reviewer: Claude

## Delivered behavior

The production controller constructs the demand reader through
`make_distillation_worker`, then enters `catch_up` / `run_pending`. Records
denied by the daily extraction budget return to the durable SQLite outbox as
`retry_wait`, available at the next UTC midnight. This does not consume the
transport retry budget. First cap deferral starts a 14-day lifetime; later cap
deferrals do not restart it. Once due at that deadline, the worker records
`budget_deferral_expired: 14 days since first cap deferral` and skips extraction.
Producing a card ends cap waiting so later compiler/Registry retries retain
their existing lifetime. Manual redispatch explicitly resets deferral metadata.

Before claiming a bounded batch, the queue orders the entire due deferred
backlog by descending fixed-vocabulary overlap, then arrival time and job ID.
Deferred records precede new records; new records retain their existing
available-time / arrival-time / job-ID order. Without open demand, or when its
owner cannot be read, deferred records retain arrival order. The default cap
remains **400**. Budget reservations serialize across independent ledger
instances so concurrent consumers cannot overspend it.

Queue state carries `deferred_since`, `priority_reason`, and `demand_score`.
For compatibility with existing reason readers, a capped nonterminal job also
has `skip_reason=budget_exhausted: deferred to next cap window`; **status is
`retry_wait`, not `skipped`**. The controller exposes deferred counts separately
and excludes planned cap waits from Registry-failure classification. Real
transport retries and dead letters still fail that classification.

Schema upgrades add columns under one writer transaction; they preserve the
existing SQLite family, source versions, leases, inbox and dead letters. A
lease token and unexpired lease are required to defer or finish cap waiting.

## Research owner contract and safety

The read is `GET /api/research/persona-demands?status=open`, with a rotating
dedicated `DISTILLATION_RESEARCH_SERVICE_TOKEN` bearer credential and explicit
`X-Tenant-ID`. Compose mounts the existing consumer-specific credential volume;
the dev paper issuer supplies only the reader's `viewer` / owner-read profile.
Missing credentials do not trigger an anonymous read. Reads have a five-second
timeout, never follow redirects, reject foreign-tenant responses, ignore closed
or expired demands, and record sanitized fallback reasons without credentials
or exception messages. Network reads occur before the SQLite claim transaction.

The current persona-owned `research_demand.v1` schema has a **string `mandate`**,
not the retired allocator's structured mandate profile. This consumer derives
mandate terms and strategy-family terms from that string using the explicit
vocabulary in `demand_priority.py`. It intersects those terms with the committed
source title/text, abstract, keywords, mandate terms and strategy families.
Occurrences do not multiply the score; unknown words have no effect. Tried
families and missing datasets are not invented into positive demand signals.
No owner schema change, model-based ranking, new scheduler, synthetic market
data, pool member, execution baseline, capital binding or production action is
introduced. Extraction remains gated by its existing enable flag, admission,
citations and compiler rules.

## Acceptance replay

`test_cap_two_five_records_across_windows_real_worker_queue_and_extractor`
uses the real production worker factory, queue, extraction client, budget
ledger and citation validation. Five ordered records consume a cap of two;
the other three defer. After reconstructing the worker, queue and ledger in
the second window, demand selects records 4 and 3 ahead of record 2 and a new
record. Empty-demand and unavailable-owner variants select records 2 and 3.
A third window proves eventual extraction and FIFO fallback.

`test_environment_controller_tick_drives_demand_deferral_across_two_windows`
uses the real `run_controller_tick`, environment-enabled extractor/compiler,
queue and owner reader. Model transport is replayed, and compilation refuses
the fixture rather than executing or creating an approved artifact. It proves
two controller ticks extract 0/1 then 4/3, persist remaining cap waits, and do
not misreport those waits as a Registry outage.

Additional tests cover 14-day expiry across daily cap hits and restart, stale
lease rejection, shared-ledger concurrency, tenant isolation, rotating/missing
credential files, sanitized outages and fixed-vocabulary matching. A real
Research API TestClient verifies the newly issued synthetic reader JWT can
read its tenant and gets HTTP 403 for another tenant. Existing multiprocess
queue-lifetime regressions exercise concurrent schema initialization.

The extraction response fixture is explicitly hand-authored model-shaped
data, replayed from the already committed screen/entry fixture. Its commentary
quotes existing TWSE recordings; this task introduces no market observations
and claims no live provider call, strategy performance or hosted deployment.
See `evidence.json` and `acceptance.log` for the final bounded validation.

Independent review and supervisor integration remain required; this evidence
does not grant owner approval or declare the task done.

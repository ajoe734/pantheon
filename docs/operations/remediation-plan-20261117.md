# Remediation Plan 2026-11-17

Status: approved by the operator on 2026-11-17 ("依照你的建議來做"), covering
all four decisions in section 4. The binding rules are in `AGENTS.md`
("Remediation Plan 2026-11-17"). The living state is
`docs/operations/problem-register.md`.

## 1. Why this plan exists

Between 2026-11-10 and 2026-11-17 every incident was handled as a single bug.
The operator's assessment on 2026-11-17 was that the coordinators were treating
symptoms one at a time. The audit below shows the incidents come from five root
causes; fixing instances does not stop them.

## 2. Audit (measured 2026-11-17, window 2026-11-10 to 2026-11-17)

### Development and deployment

| Item | Measured |
|---|---|
| Tasks opened | 406, of which 368 marked done |
| Hosted acceptance tasks | 23, of which 3 done and 16 not started |
| Dev deploy runs | 51, of which 10 succeeded (20%) |
| Failing steps | 15 different steps; acceptance gate 14, Taiwan market refresh 6, VM stack deploy 6 |
| Pipeline size | 89 workflow steps, 6,700-line deploy script, 67 minutes for a successful run |
| Hosted version | `e7ec353d6`, 205 commits behind `dev` |
| Dev Branch CI since 11/15 | 100 runs, 75 cancelled by a newer merge; the deployer accepts only a green branch tip, which leaves a window of about 3 minutes |
| Review | 68 of 277 tasks reviewed by the same model family as the owner |

### Persona system on hosted

- Enrollment of the three trading personas is refused
  (`GOVERNANCE_DECISION_INVALID`) and has been retried 4,121 times. The fixes
  are merged but not deployed.
- Daily cycle: no persona enrolled; `per_persona_ooda` has never ticked.
- Memory: every Hindsight table has 0 rows. Retrieval holds 11,996 objects and
  has never been queried (`retrieval_query_log` is empty).
- No consultation since 2026-11-01. Meetings are not deployed.
- Strategy pools: three pools with one member each (the original artifact).
- Paper trading runs one decision per trading day. Position size fell to one
  share for two personas from 11/14. Fill price and cost handling must be
  re-checked on the first fills made by the current hosted version.

### Hosted host

| Finding | Measured |
|---|---|
| Source-ingest scheduler | Exited (137) since 2026-11-16 19:19Z after a failed deploy compensation; no ingestion since |
| Strategy distillation | fails every cycle since 2026-11-13 10:50Z ("expired distillation lease completion"); the worker writes 9 to 10 GB per hour |
| `public.telemetry_events` | 29 GB, 13.7M rows; 9.7M heartbeats (one per runtime every 1.2 s instead of 15 s), 2.75M infrastructure_health; about 0.75 GB per day |
| `trade_journey_projection.event_receipts` | 4.4 GB, a 1:1 mirror of telemetry; more than 99.99% are `ignored` receipts |
| search-svc memory | 10.4 GiB RSS on a 32 GB host without swap; a global OOM on 2026-11-16 10:58Z killed a 15.5 GB process |
| Health that lies | source-ingest `/readyz` returns ok while its freshness block reports 21 of 29 connectors stale and `data_ready=false` |
| Orphans | 4.85 GB of orphan `*.tmp` in runtime-data paper-performance; 988 MB of snapshot pre-compact backups; 49.8 GB of build cache |
| Other unbounded state | BFF `downstream_health.sqlite3` 1.03 GB; `reconciliation_drift.work_claims` never deleted |

### Collector host

- 19 of 21 timers succeed. TWSE and TPEx price history are paused on purpose
  until the receipt fix is accepted on hosted; the same-day close capture does
  not handle a disabled source and will fail for them.
- Hosted `tw_price_daily` has 149 sessions; 2025-04 to 2026-07 is missing.

### Persistent writers

The code names 74 distinct `.jsonl` state files and 92 tables. No inventory
records which of them is bounded.

## 3. Root causes and work packages

### RC1. Deployment bundles "go live" with "functional acceptance"

Any failing functional check rolls the whole release back, so four of five
deploys fail and hosted stays old. Compensation itself can break the stack.

- WP1.1 Dev deploy is build, switch and stack health. Only an unhealthy stack
  rolls back.
- WP1.2 Functional checks move to a post-deploy acceptance stage. A failure
  marks the release `deployed, not accepted` and opens a task; no rollback.
- WP1.3 After every deploy and every rollback, verify that each expected steady
  service runs at the expected version.
- WP1.4 The automatic deployer deploys the newest commit whose Branch CI
  passed, not only a green tip.
- Target: at least 90% success, at most 25 minutes, hosted at most one day
  behind `dev`.

### RC2. "Done" means merged, not working

- WP2.1 Every feature chain ships an executable read-only acceptance probe. The
  acceptance stage runs it after deploy and writes the result to the task.
- WP2.2 Reporting uses three states: merged, deployed, accepted.
- WP2.3 One daily probe "a persona's day": enrolled, cycle ticked, decision
  used memory, meeting held, results written back. It is the single measure of
  whether the system runs, and it is shown in Management.

### RC3. Nobody owns data volume

- WP3.1 Fix every unbounded or wasteful writer found by the audit in one batch.
- WP3.2 Storage manifest: every persistent writer declares growth per day, cap
  and cleanup. A test fails when code adds an undeclared state file or table.
- WP3.3 Daily hosted measurement against the manifest; exceeding a bound opens
  an incident before the disk is at risk.
- WP3.4 Memory limits per container.

### RC4. Nobody looks at the whole design

- WP4.1 Runtime budget in acceptance for every recurring job or writer.
- WP4.2 Residual findings are recorded in the handoff and turned into register
  entries; a chain with open residuals cannot close.
- WP4.3 Cross-family review.
- WP4.4 Weekly whole-domain check against hosted numbers.

### RC5. Coordinators each fix their own symptom

- WP5.1 One owner per domain.
- WP5.2 The problem register: incidents attach to a root-cause class instead of
  becoming one-off tasks.

## 4. Operator decisions (2026-11-17)

1. Dev deploy rule: functional checks no longer roll back; only an unhealthy
   stack does. Staging and production stay strict.
2. Feature freeze until the three personas complete a full day on hosted.
3. Coordinators are split by domain; the split is proposed in the register.
4. If no deploy has succeeded by 2026-11-17 05:00Z, the source-ingest scheduler
   on hosted may be started by hand as a temporary live repair.

## 5. Order of execution

1. Today: one successful deploy (restores the scheduler and brings the
   enrollment fixes); stop the distillation write churn.
2. This week: RC1 and WP3.1.
3. Next week: RC2, WP3.2 and WP3.3.
4. In parallel: RC4 and RC5 rules (already in `AGENTS.md`).

# Problem Register

Living document for the remediation plan
(`docs/operations/remediation-plan-20261117.md`). Rules for using it are in
`AGENTS.md` ("Remediation Plan 2026-11-17").

How to use it:

- A new incident is first matched to a root-cause class below and added as an
  instance. Open a task only if the class has no work package that covers it.
- Update this file through the development-tooling delivery path. Keep entries
  to one line; details belong in the task.
- An instance leaves the list when its fix is `accepted` on hosted, not when it
  is merged.
- State column: `open` (not merged), `merged`, `deployed` (the merge is
  contained in the backend commit hosted serves) or `accepted`. Hosted serves
  backend `13a08deaa` with frontend `0e886b9ce` since 2026-11-17 23:26Z (Root
  44187386408, release accepted; before that `80d4cf2ca` from 15:07Z). The
  source for a release's state is the hosted `acceptance/latest.json`. Changes that only run in
  GitHub Actions or on the collector host stay `merged` here.

## Domains and owners

Proposed by the remediation coordinator on 2026-11-17 under operator decision 3.
Conversation names rotate, so each owner is identified by what it has been
doing and by the first eight characters of its session id.

| Domain | Scope | Owner conversation |
|---|---|---|
| Release and hosted operations | dispatching deploys, hosted rescue and cleanup, collector host operations | the conversation that handled Root compensation and image cleanup (`4d1476a3`) |
| Market data and source storage | market data store, snapshots, receipts, collector intake on hosted | the market-data-store design conversation (`cb4f6795`); the snapshot-read (`a2c3dcbe`) and pool/receipt (`b1134a03`) conversations hand their open data items to it |
| Persona loop going live | enrollment, daily cycle, pools, paper trading correctness | the enrollment-wiring conversation (`77b586b6`); the image/enrollment conversation (`1c084f88`) hands over |
| Remediation mechanisms | deploy pipeline restructure, storage manifest and measurement, acceptance probes, rules; hosted acceptance of memory, meetings and regime | the conversation that ran the 2026-11-17 audit (`f4c32790`) |
| Frontend | execute-plans build, gate and hosted pages | the frontend-build conversation (`065c0719`) |
| Development tooling | supervisor, dashboard, fleet capacity | the dashboard conversation (`1ce974b6`) |

## RC1. Deployment bundles go-live with functional acceptance

| Work package | Task | State |
|---|---|---|
| Design | `docs/deployment/SD_DEV_DEPLOY_ACCEPTANCE_SPLIT_2026-11-17.md` (PR #7484) | written |
| WP1.1 automatic releases stop turning on functional gates | DEV-AUTO-DEPLOY-NO-FUNCTIONAL-PROBE-INPUT-20261117 (#7486) | merged |
| WP1.1 in-deploy functional checks become advisory | DEV-DEPLOY-FUNCTIONAL-CHECKS-ADVISORY-20261117 (#7497) | merged |
| WP1.1 deploy workflow stays valid: at most 25 dispatch inputs, guard test | OPS-DEPLOY-WORKFLOW-INPUT-LIMIT-FIX-20261117 (#7507) | merged |
| WP1.2 acceptance stage and verdict | DEV-DEPLOY-ACCEPTANCE-STAGE-20261117 (#7531) | merged; first ran on Root 44187386408 with one check (deployed pair identity) |
| WP1.2 frontend gate hosted checks advisory (execute-plans) | FE-GATE-HOSTED-CHECKS-ADVISORY-DEV-20261117 (execute-plans #1035) | merged |
| WP1.2 frontend gate admits a controller commit that is not the tip (execute-plans) | FE-GATE-ADMIT-NEWEST-GREEN-CONTROLLER-20261117 (execute-plans #1037) | merged |
| WP1.3 steady-service helpers | ROOT-COMPENSATION-STEADY-SERVICES-20261117 (#7481) | merged |
| WP1.3 stack completeness from compose, wired into deploy, compensation and restore | DEV-DEPLOY-STACK-COMPLETENESS-20261117 (#7524) | merged |
| WP1.3 a restart-looping service is an unhealthy stack | DEV-DEPLOY-STEADY-STATE-RESTART-LOOP-20261117 (#7540); repair OPS-DEPLOY-RESTART-LOOP-SCRIPT-PATH-20261117 (#7547) | merged; first real run on Root 44187386408 (two samples 92 s apart, none flagged) |
| WP1.3 revision equality only for images the release builds; restore brings every steady service back | DEV-DEPLOY-STACK-CHECK-REVISION-SCOPE-20261117 (#7545) | deployed |
| WP1.4 deploy the newest green commit | DEV-AUTO-DEPLOY-NEWEST-GREEN-20261117 (#7506) | merged |
| WP1.4 dev CI runs are not cancelled in progress; a green dev commit triggers the deployer | DEV-GREEN-COMMIT-DEPLOY-TRIGGER-20261117 (#7541) | merged; in effect since 19:40Z |
| WP1.5 a rollback does not restore data: reconcile tolerates state written by a newer release | SOURCE-RECONCILE-TOLERATES-NEWER-STATE-20261117 (#7517) | deployed |
| Persisted config that a rollback cannot read: OpenClaw persona-tools plugin paths | OPENCLAW-PERSONA-TOOLS-PLUGIN-ROLLBACK-SAFE-20261117 (#7510) | deployed |
| Integrator re-dispatches the review gate and cancels its own queued run | AUTO-INTEGRATOR-GATE-REDISPATCH-LIVELOCK-20261117 (#7478) | merged |
| Projection bootstrap rebuilds then drops a redundant event_receipts index on every deploy and deadlocks with live writers | PROJECTION-BOOTSTRAP-INDEX-CHURN-DEADLOCK-20261117 (#7518) | deployed |
| Required check that fails under CI load | SOURCE-INGEST-FULL-SIZE-CLOSE-TEST-DETERMINISTIC-20261117 (#7501, did not hold: same assertion failed again at 18:56Z); SOURCE-INGEST-FULL-SIZE-CLOSE-TEST-DETERMINISTIC-R2-20261118 (#7546, market-data owner) | merged |
| Deployer reports failure after a successful dispatch (`dispatch sent but run could not be correlated`, 18:08Z, 20:04Z, 20:46Z) | no task yet | open |
| The advisory check summary carries its own `acceptance` field and wrote `failed` on Root 44187386408 because a housekeeping budget check failed (docker build cache cap), while the acceptance record passed; one coordinator reported the release as not accepted from it | no task yet | open |
| Frontend unit test red inside the integration gate (execute-plans) | FE-TAILWIND-REPRO-TEST-GATE-ISOLATION-20261117 (execute-plans #1036) | merged |

The workflow and deploy-script tasks are serialised in this order: no probe
input, advisory checks, newest green, stack completeness, docker maintenance
bounds, acceptance stage, restart loop, then the revision scope and the
restart-loop script path repairs.

Instances: 40 failed dev deploys 11/10 to 11/17; scheduler left stopped by
compensation 11/16 19:19Z and again 11/17 02:42Z, 06:19Z and 09:13Z; restore
check compares only `/bff/version`; deployer window of about 3 minutes after a
green tip; OpenClaw gateway crash-looped after the 11/17 06:18Z compensation
because the candidate wrote plugin paths into the shared volume config (hosted
rescue: back up `openclaw.json`, remove the two persona-tools entries); a
baseline scheduler started on candidate-written connector state fails every
tick with HTTP 400, so intake returns only with a release; Root 44023618158
rejected by an execute-plans unit test in the gate; Root 44049970156 failed at
projection_bootstrap with a Postgres deadlock on event_receipts; the deploy
workflow was invalid from 04:00Z to 05:04Z on 11/17 because the advisory-checks
task added a 26th dispatch input (its acceptance asked for an input; use
repository variables instead); Root 44083921576 rejected by the frontend gate
because the controller commit was no longer the tip after newest-green
selection; Root 44097697049 succeeded at 15:07Z on 11/17, the first release
since 11/15, while the OpenClaw adapter restarted 54 times unnoticed (see RC3
WP3.4). Evening of 11/17: dev push CI was cancelled by every merge (3 green of
15 runs in five hours, none between 14:05Z and 18:09Z) and the publish-cut cron
did not fire for 45 hours (11/15 11:38Z to 11/17 08:48Z), so merged fixes could
not be released; Root 44144749607 (cron, 9ab80dd25, 32 seconds before the first
commit with the memory fix went green) failed on source-ingest-scheduler health
and on the openclaw-gateway revision label after PR #7524 widened the check
from 31 to 57 services; Root 44165496691 (f43b47eb7, dispatched 12 seconds
before PR #7545 merged) failed on the gateway label alone; both restores were
reported incomplete because dev-paper-principal-issuer stayed on the candidate
image; Root 44172901470 (3e244d209) passed the stack check and was rolled back
by the restart-loop step, which ran scripts from a workspace path that does not
exist in the deploy job (the task's tests compared workflow text and never ran
the step in that job's checkout layout); Root 44187386408 (13a08deaa) succeeded
at 23:26Z. Since PR #7541 every green dev commit dispatches a release, so
hosted is redeployed about every 75 minutes while merges continue. The Smoke
acceptance step for source ingestion takes about 880 s; its cap was 900 s and
is 25 minutes since PR #7541; the suite duration has no budget.

## RC2. Done means merged, not working

| Work package | Task | State |
|---|---|---|
| WP2.1 probe runner and contract | DEV-DEPLOY-ACCEPTANCE-STAGE-20261117 | open |
| WP2.1 probes for chains already merged (replace the manual hosted acceptance tasks) | to be opened once the probe contract exists | open |
| WP2.2 three delivery states in reporting | rule in AGENTS.md | in force |
| WP2.3 daily probe "a persona's day" | PERSONA-DAY-ACCEPTANCE-PROBE-20261117 (PR #7539) | open |
| Health that lies: source-ingest readiness | SOURCE-INGEST-READINESS-TRUTHFUL-20261117 (#7511) | deployed |
| Same-day close: official rows never fetched for a collector-covered session | TW-OFFICIAL-RECONCILE-AFTER-COLLECTOR-20261117 (#7479) | deployed |

Instances: 20 hosted acceptance tasks not done; enrollment refused 4,121 times;
daily cycle never ticked; Hindsight empty; retrieval never queried; no
consultation since 11/01; pools with one member; held-symbol priority for the
collector pull merged (#7438, #7459) and not deployed; TPEx same-day close took
62 minutes on 11/16. Update 11/17 15:25Z from the persona-loop owner: the three
trading personas' daily-cycle jobs are registered and ran once at 10:00Z on
release e7ec353d6; all three failed at observe
(`observe_prerequisites_unavailable:canonical_memory`); the first run on
80d4cf2ca is 2026-11-18 10:00Z. Measured on hosted 11/17 15:40Z (deployed,
not accepted): strategy distillation writes fell from 54 GB in 5.8 hours to
14 MB in 80 minutes and the stuck lease is gone, but every tick still reports
failed because 241 dead letters from Registry 401 responses (enqueued 11/13
10:51Z to 11/16 17:35Z) are unresolved; infrastructure_health rows fell from
2991 to 202 per hour; heartbeat cadence cannot be measured until the paper
runtimes run again (they stop at the 05:30Z close).
Root 44187386408 is `accepted`: the hosted acceptance record
(`acceptance/latest.json`, 23:26:33Z) says passed with its one check, the
deployed pair identity, and that record is the source for a release's delivery
state; the five functional advisory checks of the run passed as well. A release
being accepted does not make a task accepted: no task of 11/17 has an
acceptance probe of its own yet, so `merged` and `deployed` are still the only
states those tasks have reached. A workflow change (PR #7540) passed
review and its tests and failed on its first real run.

## RC3. Nobody owns data volume

| Work package | Task | State |
|---|---|---|
| WP3.1 heartbeat cadence | PAPER-RUNTIME-HEARTBEAT-CADENCE-20261117 | deployed |
| WP3.1 heartbeat state on change only | PAPER-RUNTIME-STATE-SNAPSHOT-ON-CHANGE-20261117 (#7482) | deployed |
| WP3.1 raw heartbeat retention and reclaim | TELEMETRY-HEARTBEAT-RETENTION-20261117 (#7480) | deployed |
| WP3.1 untagged image enumeration | DEV-UNTAGGED-IMAGE-ENUMERATION-ALL-20261117 (#7476) | merged |
| WP3.1 distillation stuck lease and unbounded claim | DISTILLATION-LEASE-PER-JOB-AND-BOUNDED-CLAIM-20261117 (#7492) | deployed |
| WP3.1 distillation seed store rewrite and temp files | DISTILLATION-SEED-STORE-BOUNDED-WRITE-20261117 (#7498) | deployed |
| WP3.1 lifecycle receipts for non-lifecycle events | TRADE-JOURNEY-RECEIPTS-LIFECYCLE-ONLY-20261117 (#7495) | deployed |
| WP3.1 infrastructure_health emission and BFF health database | BFF-DOWNSTREAM-HEALTH-STORAGE-BOUND-20261117 (#7490) | deployed |
| WP3.1 infrastructure_health retention and reclaim of receipts | TELEMETRY-INFRA-HEALTH-RETENTION-20261117 (#7494) | deployed |
| WP3.1 reconciliation drift work claims | RECON-DRIFT-WORK-CLAIMS-RETENTION-20261117 (#7493) | deployed |
| WP3.1 paper runtime temp files and lifecycle outbox | PAPER-RUNTIME-STATE-FILES-BOUND-20261117 (#7489) | deployed |
| WP3.1 search-svc full-table reloads and history | SEARCH-SVC-MEMORY-BOUND-20261117 (#7496) | deployed |
| WP3.1 build cache after build, stale one-shot containers | DEV-DEPLOY-DOCKER-MAINTENANCE-BOUNDS-20261117 (#7528) | merged; on Root 44187386408 the prune hit its 45 s budget with 39.8 GB against the 16 GB cap (advisory check failed) |
| WP3.1 collector close capture with a disabled source | COLLECTOR-CLOSE-CAPTURE-DISABLED-SOURCE-20261117 (#7491) | merged |
| WP3.1 hosted reclaim of deleted rows (coordinator) | TELEMETRY-HEARTBEAT-HOSTED-ACCEPTANCE-20261117 | open; its three dependencies are deployed |
| WP3.1 two snapshot pre-compact backup files on hosted (988 MB, created by hand) | release and hosted operations owner | open |
| WP3.1 hosted redrive of 241 distillation dead letters (Registry 401 between 11/13 and 11/16) | remediation coordinator; hosted write, waits for operator consent | open |
| WP3.1 market data stored once per key (market-data owner) | SOURCE-HOSTED-LISTING-STATE-AND-PIN-SOURCE-20261118 (#7521), SOURCE-DAILY-BARS-READERS-COMPLETE-20261118 (#7522) | deployed |
| WP3.1 market data retention sweeper (market-data owner) | SOURCE-RETENTION-SWEEPER-20261118-R2 (#7526) | deployed |
| WP3.1 persona OODA packet store rewrites the whole file per append (persona-loop owner) | PERSONA-OODA-PACKET-STORE-BOUNDED-20261117 (#7544) | deployed |
| WP3.2 storage manifest and test | STORAGE-MANIFEST-AND-GUARD-20261117 (#7488) | merged |
| WP3.3 daily hosted measurement | DEV-STORAGE-BUDGET-DAILY-MEASUREMENT-20261117 (#7499) | merged; first scheduled run 2026-11-18 03:17Z |
| WP3.3 daily measurement of container memory, OOM kills and restarts | DEV-MEMORY-BUDGET-DAILY-MEASUREMENT-20261117 (#7542) | merged |
| WP3.4 container memory limits | DEV-COMPOSE-MEMORY-LIMITS-20261117 (#7515) | deployed; sized wrongly, repaired by the next row |
| WP3.4 limits sized from the measured cgroup peak | DEV-COMPOSE-MEMORY-LIMITS-PEAK-SIZED-20261117 (#7537) | deployed |
| WP3.4 persona cycle controller polls the adapter less often (persona-loop owner) | PERSONA-CYCLE-CONTROLLER-CRON-POLL-BUDGET-20261117 (#7534) | deployed |
| WP3.4 adapter has no cap on concurrent `openclaw` CLI calls, about 400 MiB each (persona-loop owner) | no task yet; owner decides after the 2026-11-18 10:00Z cycle | open |
| WP3.5 development host: worker test temp is never removed | DEV-WORKER-TEMP-DIR-BOUND-20261117 (#7538) | merged; takes effect only after a supervisor runtime promotion (live runtime is still 6356e852 from 11/16) |

Instances: see section 2 of the plan. Added 11/17: on 80d4cf2ca the OpenClaw
adapter was OOM-killed under its 256 MiB limit about every 45 seconds from
14:16Z (54 restarts; temporary live repair to 1 GiB at 15:11Z by the
persona-loop owner, reset by the next deploy); the limits were sized from one
`docker stats` sample, which leaves out page cache, child processes and peaks;
postgres reached its 2 GiB limit over a million times in an hour because the
limit caps its page cache; paper-fleet-reconciler and alpha-replication-worker
also reached their limits; the development host disk filled to 100 percent on
11/15 and again on 11/17 from worker test temp under `/tmp` (41 GB pytest
directories and 19 GB leaked test directories on 11/17, up to 6 GB per hour).
Later on 11/17: hosted `/data/bff/ooda_packets.json` was 11.5 MB after six
packets of one cycle (two packets embed a whole quarter equity series) and is
rewritten in full on every append; the storage manifest listed it under the
wrong name (`ooda_packets.jsonl`). Temporary live memory settings on hosted
(adapter 1 GiB, persona 512 MiB, by the persona-loop owner) were re-applied by
hand after each of three rollbacks until 13a08deaa was served. The first
container limits also capped hindsight at 94 percent and persona at 90 percent
of their limits within an hour.

## RC4. Nobody looks at the whole design

| Work package | Task | State |
|---|---|---|
| WP4.1 runtime budget in acceptance | rule in AGENTS.md | in force |
| WP4.2 residual findings in handoff | rule in AGENTS.md; tooling support to be opened | partly in force |
| WP4.3 cross-family review | rule in AGENTS.md | in force |
| WP4.3 reviewer reassignment picks the owner's family and records no exception; a commit made after the reassignment cannot be closed (`audited reviewer reassignment must follow the delivered commit`) | one supervisor tooling task with WP4.2, to be opened | open |
| WP4.4 weekly whole-domain check | to be scheduled by each domain owner | open |

Instances: heartbeat design built by four tasks (2026-04-19, 07-14, 08-14,
11-12); cause recorded in TELEMETRY-SUMMARY-PERSIST-COST-20261114 evidence and
not fixed.
On 11/17 the supervisor reassigned reviewers from Antigravity to the owner's
own family on at least three tasks (DEV-DEPLOY-ACCEPTANCE-STAGE-20261117,
PERSONA-CYCLE-CONTROLLER-CRON-POLL-BUDGET-20261117,
PERSONA-ENROLLMENT-READBACK-STABLE-WHEN-PAUSED-20261117) without recording the
no-capacity exception, and an archived task cannot take the note afterwards.
Two merged tasks cannot be closed because their commit was made after such a
reassignment: DEV-COMPOSE-MEMORY-LIMITS-20261117 (#7515) and
SOURCE-DAILY-BARS-VALUE-TIME-STATUS-COLUMNS-20261118 (#7530), which also gates
SOURCE-UNIVERSE-SEED-READS-DAILY-BARS-20261118.

## RC5. Coordinators each fix their own symptom

| Work package | Task | State |
|---|---|---|
| WP5.1 one owner per domain | table above | in force (operator decision 3) |
| WP5.2 this register | this file | in force |

Instances: the snapshot journal incident was opened by four conversations.

## Notes for task authors

- A task that adds a persistent reader or writer must list
  `docs/operations/storage-manifest.json` in its artifacts, otherwise the
  manifest guard fails its checks and the handoff is refused.
- A task that changes a workflow step must show the step running in the
  checkout layout of its own job; a test that compares workflow text is not
  enough (`scripts/test_nonprod_dev_artifact_workflow.py` now rejects a script
  path with no matching checkout in the same job).
- A resource limit is sized from the measured peak that the kernel enforces
  (cgroup `memory.peak`, including page cache and child processes), not from
  one `docker stats` sample.
- Fleet capacity on 2026-11-17: both Codex accounts are quota-terminal until
  2026-11-20 and Antigravity ran out of quota at 06:32Z, so reviews inside the
  Claude family are Review Independence exceptions for this period.

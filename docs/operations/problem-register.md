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
| WP1.1 automatic releases stop turning on functional gates | DEV-AUTO-DEPLOY-NO-FUNCTIONAL-PROBE-INPUT-20261117 | open |
| WP1.1 in-deploy functional checks become advisory | DEV-DEPLOY-FUNCTIONAL-CHECKS-ADVISORY-20261117 | open |
| WP1.2 acceptance stage and verdict | DEV-DEPLOY-ACCEPTANCE-STAGE-20261117 | open |
| WP1.2 frontend gate hosted checks advisory (execute-plans) | FE-GATE-HOSTED-CHECKS-ADVISORY-DEV-20261117 | open |
| WP1.3 steady-service helpers | ROOT-COMPENSATION-STEADY-SERVICES-20261117 (PR #7481) | merged; workflow not wired |
| WP1.3 stack completeness from compose, wired into deploy, compensation and restore | DEV-DEPLOY-STACK-COMPLETENESS-20261117 | open |
| WP1.4 deploy the newest green commit | DEV-AUTO-DEPLOY-NEWEST-GREEN-20261117 | open |
| Persisted config that a rollback cannot read: OpenClaw persona-tools plugin paths | OPENCLAW-PERSONA-TOOLS-PLUGIN-ROLLBACK-SAFE-20261117 | open |
| Integrator re-dispatches the review gate and cancels its own queued run | AUTO-INTEGRATOR-GATE-REDISPATCH-LIVELOCK-20261117 (PR #7478) | merged |

The workflow and deploy-script tasks are serialised in this order: no probe
input, advisory checks, stack completeness, newest green, acceptance stage.

Instances: 40 failed dev deploys 11/10 to 11/17; scheduler left stopped by
compensation 11/16 19:19Z and again 11/17 02:42Z, 06:19Z and 09:13Z; restore
check compares only `/bff/version`; deployer window of about 3 minutes after a
green tip; OpenClaw gateway crash-looped after the 11/17 06:18Z compensation
because the candidate wrote plugin paths into the shared volume config (hosted
rescue: back up `openclaw.json`, remove the two persona-tools entries); a
baseline scheduler started on candidate-written connector state fails every
tick with HTTP 400, so intake returns only with a release; Root 44023618158
rejected by an execute-plans unit test in the gate
(FE-TAILWIND-REPRO-TEST-GATE-ISOLATION-20261117, #1036 merged).

## RC2. Done means merged, not working

| Work package | Task | State |
|---|---|---|
| WP2.1 probe runner and contract | DEV-DEPLOY-ACCEPTANCE-STAGE-20261117 | open |
| WP2.1 probes for chains already merged (replace the manual hosted acceptance tasks) | to be opened once the probe contract exists | open |
| WP2.2 three delivery states in reporting | rule in AGENTS.md | in force |
| WP2.3 daily probe "a persona's day" | PERSONA-DAY-ACCEPTANCE-PROBE-20261117 | open |
| Health that lies: source-ingest readiness | SOURCE-INGEST-READINESS-TRUTHFUL-20261117 | open |
| Same-day close: official rows never fetched for a collector-covered session | TW-OFFICIAL-RECONCILE-AFTER-COLLECTOR-20261117 (PR #7479) | merged |

Instances: 20 hosted acceptance tasks not done; enrollment refused 4,121 times;
daily cycle never ticked; Hindsight empty; retrieval never queried; no
consultation since 11/01; pools with one member; held-symbol priority for the
collector pull merged (#7438, #7459) and not deployed; TPEx same-day close took
62 minutes on 11/16.

## RC3. Nobody owns data volume

| Work package | Task | State |
|---|---|---|
| WP3.1 heartbeat cadence | PAPER-RUNTIME-HEARTBEAT-CADENCE-20261117 | merged |
| WP3.1 heartbeat state on change only | PAPER-RUNTIME-STATE-SNAPSHOT-ON-CHANGE-20261117 | review approved |
| WP3.1 raw heartbeat retention and reclaim | TELEMETRY-HEARTBEAT-RETENTION-20261117 | review approved |
| WP3.1 untagged image enumeration | DEV-UNTAGGED-IMAGE-ENUMERATION-ALL-20261117 | see task |
| WP3.1 distillation stuck lease and unbounded claim | DISTILLATION-LEASE-PER-JOB-AND-BOUNDED-CLAIM-20261117 | open |
| WP3.1 distillation seed store rewrite and temp files | DISTILLATION-SEED-STORE-BOUNDED-WRITE-20261117 | open |
| WP3.1 lifecycle receipts for non-lifecycle events | TRADE-JOURNEY-RECEIPTS-LIFECYCLE-ONLY-20261117 | open |
| WP3.1 infrastructure_health emission and BFF health database | BFF-DOWNSTREAM-HEALTH-STORAGE-BOUND-20261117 | open |
| WP3.1 infrastructure_health retention and reclaim of receipts | TELEMETRY-INFRA-HEALTH-RETENTION-20261117 | open |
| WP3.1 reconciliation drift work claims | RECON-DRIFT-WORK-CLAIMS-RETENTION-20261117 | open |
| WP3.1 paper runtime temp files and lifecycle outbox | PAPER-RUNTIME-STATE-FILES-BOUND-20261117 | open |
| WP3.1 search-svc full-table reloads and history | SEARCH-SVC-MEMORY-BOUND-20261117 | open |
| WP3.1 build cache after build, stale one-shot containers | DEV-DEPLOY-DOCKER-MAINTENANCE-BOUNDS-20261117 | open |
| WP3.1 collector close capture with a disabled source | COLLECTOR-CLOSE-CAPTURE-DISABLED-SOURCE-20261117 | open |
| WP3.1 hosted reclaim of deleted rows (coordinator) | TELEMETRY-HEARTBEAT-HOSTED-ACCEPTANCE-20261117 | open |
| WP3.1 two snapshot pre-compact backup files on hosted (988 MB, created by hand) | release and hosted operations owner | open |
| WP3.2 storage manifest and test | STORAGE-MANIFEST-AND-GUARD-20261117 | open |
| WP3.3 daily hosted measurement | DEV-STORAGE-BUDGET-DAILY-MEASUREMENT-20261117 | open |
| WP3.4 container memory limits | DEV-COMPOSE-MEMORY-LIMITS-20261117 | open |

Instances: see section 2 of the plan.

## RC4. Nobody looks at the whole design

| Work package | Task | State |
|---|---|---|
| WP4.1 runtime budget in acceptance | rule in AGENTS.md | in force |
| WP4.2 residual findings in handoff | rule in AGENTS.md; tooling support to be opened | partly in force |
| WP4.3 cross-family review | rule in AGENTS.md | in force |
| WP4.4 weekly whole-domain check | to be scheduled by each domain owner | open |

Instances: heartbeat design built by four tasks (2026-04-19, 07-14, 08-14,
11-12); cause recorded in TELEMETRY-SUMMARY-PERSIST-COST-20261114 evidence and
not fixed.

## RC5. Coordinators each fix their own symptom

| Work package | Task | State |
|---|---|---|
| WP5.1 one owner per domain | table above | proposed |
| WP5.2 this register | this file | in force |

Instances: the snapshot journal incident was opened by four conversations.

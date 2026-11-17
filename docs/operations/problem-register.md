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
| WP1.1 deploy is build, switch, stack health | to be opened after the pipeline map | open |
| WP1.2 functional checks after the switch, no rollback | to be opened after the pipeline map | open |
| WP1.3 steady-service verification after deploy and rollback | ROOT-COMPENSATION-STEADY-SERVICES-20261117 | review approved |
| WP1.4 deploy the newest green commit | to be opened | open |

Instances: 40 failed dev deploys 11/10 to 11/17; scheduler left stopped by
compensation 11/16 19:19Z; restore check compares only `/bff/version`; deployer
window of about 3 minutes after a green tip.

## RC2. Done means merged, not working

| Work package | Task | State |
|---|---|---|
| WP2.1 acceptance probes shipped with each chain | to be opened | open |
| WP2.2 three delivery states in reporting | rule in AGENTS.md | in force |
| WP2.3 daily probe "a persona's day" | to be opened | open |

Instances: 20 hosted acceptance tasks not done; enrollment refused 4,121 times;
daily cycle never ticked; Hindsight empty; retrieval never queried; no
consultation since 11/01; pools with one member.

## RC3. Nobody owns data volume

| Work package | Task | State |
|---|---|---|
| WP3.1 heartbeat cadence | PAPER-RUNTIME-HEARTBEAT-CADENCE-20261117 | merged |
| WP3.1 heartbeat state on change only | PAPER-RUNTIME-STATE-SNAPSHOT-ON-CHANGE-20261117 | review approved |
| WP3.1 raw heartbeat retention and reclaim | TELEMETRY-HEARTBEAT-RETENTION-20261117 | review approved |
| WP3.1 untagged image enumeration | DEV-UNTAGGED-IMAGE-ENUMERATION-ALL-20261117 | see task |
| WP3.1 remaining writers (distillation queue, event receipts, infrastructure_health, work claims, orphan tmp, health sqlite, pre-compact backups, build cache) | to be opened | open |
| WP3.2 storage manifest and test | to be opened | open |
| WP3.3 daily hosted measurement | to be opened | open |
| WP3.4 container memory limits | to be opened | open |

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

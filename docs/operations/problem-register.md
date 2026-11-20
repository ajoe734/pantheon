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
  backend `2fb152cd3` with frontend `a318cc2d6` since 2026-11-19 (Root
  44560086559; the gateway was recreated at 16:22Z; release accepted: the
  record passed at 17:19:54Z with the deployed pair identity check, and the
  persona pool member probe was skipped because release probes receive no
  viewer credentials, see RC2). Earlier on 11/19: `1c9fbee93` from 08:57Z, then
  Roots 44512666272 (`45ec8d53d`) and 44542784932 (`d685eb962`, record passed
  15:46Z). The source for a release's state is the hosted
  `acceptance/latest.json` (`managed-deploy-worktrees/dev-root/acceptance/`
  under the deploy root). Changes that only run in GitHub Actions or on the
  collector host stay `merged` here.
  `merged` here.

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
| Required check that fails under CI load (market-data owner) | SOURCE-INGEST-FULL-SIZE-CLOSE-TEST-DETERMINISTIC-20261117 (#7501) and its R2 (#7546); snapshot-store wall-clock bounds made opt-in: SOURCE-SNAPSHOT-STORE-TEST-TIMING-OPT-IN-20261118 (#7595); a test loop that ran to its limit past the 300 s cap: COLLECTOR-WHOLE-MARKET-DAY-TEST-LOOP-EXIT-20261118 (#7596); class task SOURCE-INGEST-TEST-WALL-CLOCK-BOUNDS-DETERMINISTIC-20261118 (PR #7593) | merged; class task open |
| Same class, not load: `services/control-plane/bff/test_training_session_service_client.py::test_live_tampered_credential_rejected_by_owner` fails about 1 run in 16 because it "tampers" an HS256 JWT by replacing the last base64url character, which carries only padding bits when the original is `A` (replaced by `B`), and `exp` changes the signature every run; it returned the approved PR #7625 to its owner on 11/19 and passed on rerun (found by the persona strategy visibility chain) | BFF-TRAINING-TAMPER-TEST-DETERMINISTIC-20261119 (operator request 11/19; #7631, 26db13afa) | merged |
| Same class, wall-clock bounds on the required path, seen together in run 44534477621 (PR #7631, a test-only change) and passed on the next head: `services/control-plane/bff/test_downstream_health_monitor.py::...drain_under_500ms` (533.2 ms against 500 ms), `scripts/test_dev_remote_watchdog.py::...test_large_envelope_exceeding_pause_succeeds_within_startup_timeout` (exit 75 under a 1.0 s startup timeout) and `services/control-plane/bff/tests/test_persona_league_concurrent_read.py` (elapsed-time and concurrency bounds) | TEST-WALL-CLOCK-BOUNDS-DETERMINISTIC-20261119 (operator request 11/19; #7638) | merged; before it, the league test also failed on dev CI 44541777061, so the cut skipped 2dabd6073 (#7630) for one round |
| A required frontend check fails on a browser-install race inside `npm run test`: Root 44466874746 (fd4903dcc) was rejected and compensated on 11/19 06:44Z although its frontend commit had passed the gate eleven times (frontend owner) | FE-GATE-BROWSER-INSTALL-BEFORE-TESTS-20261119 | open |
| WP1.2 on dev a frontend gate failure for an unchanged frontend rolled the backend back, against operator decision 1 | DEV-RELEASE-UNCHANGED-FE-GATE-FAILURE-NO-ROLLBACK-20261119 (#7622) | merged |
| Deployer reports failure after a successful dispatch: correlation compares the run's head commit with the selected backend, and dev has usually advanced; for the same reason a failed pair can be invisible to the retry guard and be dispatched again (release owner) | DEV-DEPLOY-DISPATCH-CORRELATION-BY-PAIR-TITLE-20261118 (#7597) | merged |
| The stock persona-tools plugin reported `invalid plugin config` on 5c0463e3b and blocked every persona tool (persona-loop owner) | PERSONA-TOOLS-PLUGIN-EMPTY-CONFIG-ENV-FALLBACK-20261118 (#7601) | deployed (e09a12d17, 11/18 20:40Z) |
| Every persona tool call is refused with `call_not_authorized_by_hook` although the broker authorized it: in the 11/19 10:00Z cycle the hook ran and the broker answered 200 to all 14 `/authorize` requests, then all 14 calls failed in the tool. The plugin keeps the hook's authorization in the memory of one plugin instance; OpenClaw 2026.7.1 most likely runs the tool from a separate registry load (`activate: false`, `toolDiscovery: true`) with its own instance. The unit test uses one instance (persona-loop owner) | PERSONA-TOOLS-HOOK-AUTHORIZATION-ACROSS-PLUGIN-INSTANCES-20261119 (#7630): the broker records each authorized tool call id and the tool consumes it once; the first CI run of the PR was red for an undeclared writer (the storage manifest guard) and two test doubles that called `/call` directly | deployed (2fb152cd3): the gateway image built in that Root carries the new plugin (no in-memory binding; the call sends the tool call id); accepted when the persona-day probe after the 11/20 10:00Z cycle reads memory through the broker for a trading persona |
| The advisory check summary carries its own `acceptance` field and wrote `failed` on Root 44187386408 because a housekeeping budget check failed (docker build cache cap), while the acceptance record passed; one coordinator reported the release as not accepted from it | no task yet | open |
| WP1.2 the acceptance stage runs release probes only | ACCEPTANCE-STAGE-RELEASE-PROBES-ONLY-20261118 (#7576) | merged |
| WP1.3 capture removes a never-started replacement container and the failure line names the service | DEV-DEPLOY-CAPTURE-NEVER-STARTED-CONTAINER-20261118 (#7587) | deployed |
| WP1.3 container health encodes a durable functional failure: consultation-svc was unhealthy for as long as a dead letter was recorded (persona-loop owner) | CONSULTATION-HEALTHCHECK-DEAD-LETTER-NOT-LIVENESS-20261118 (#7582) | deployed; consultation-svc is healthy on 5c0463e3b |
| WP1.3 the shared health routes computed the service's metrics on the event loop; `/livez` froze source-ingest for 11.5 s and 20.5 s while the hourly sweep held the universe lock | HEALTH-LIVENESS-NO-METRICS-ON-EVENT-LOOP-20261119 (#7619); the source-ingest side SOURCE-INGEST-HOURLY-SWEEP-PROCESS-STALL-20261119 (#7615, market-data owner) | deployed (1c9fbee93) |
| WP1.3 same shape, recovered by itself: the shared outbox-worker health of paper-signal-producer fails on `status=degraded` (reported by the persona-loop owner, not verified by the register owner) | no task yet | open |
| WP1.4 a change that touches no runtime path does not trigger a release | DEV-DEPLOY-SKIP-NON-RUNTIME-CHANGES-20261117 (#7550) | merged; not yet observed on a real change |
| WP1.4 the deployer holds a dispatch during the Taiwan session and the persona daily cycle | DEV-DEPLOY-QUIET-WINDOWS-20261118 (#7566) | merged; not yet observed holding; until then timers on the development host pause the publish-cut workflow in both windows; for 11/20: Taiwan session 00:30Z to 05:35Z, persona window 08:00Z (earlier, because a release takes up to two hours) to 10:45Z; pause timers do not run late after a host restart |
| An interrupted refresh run leaves source-ingest stopped and a leftover container (release owner) | TW-REFRESH-STEADY-RESTORE-ON-INTERRUPT-20261118 (#7585) | merged; first real run 44421268724 (11/18 23:30Z) showed the steady check is refused after any guarded failure (the lease is quarantined); repair TW-REFRESH-STEADY-CHECK-AFTER-LEASE-QUARANTINE-20261119 (#7607) merged and seen working in refresh runs 44481377958 (07:40Z) and 44509299247 (10:50Z): the steady restore check reported healthy and the summary step failed the run visibly |
| Refresh preflight allows 3 s, and the first snapshot lookup after a restart ran a 20 s sweep before answering (market-data owner) | SOURCE-INGEST-READY-COVERS-SNAPSHOT-LOOKUP-20261118 (#7589) | deployed |
| Gateway provider credentials are hand-made hosted state: a stored auth profile shadows the deployed secret | the operator's two OpenAI API keys come from the environment since OPENCLAW-OPENAI-API-KEY-ROUTE-TWO-KEYS-R2-20261118 (#7594) and OPENCLAW-API-ROUTE-LOOP-STDIN-SECOND-KEY-20261118 (#7603, the deploy loop lost its second key to `docker compose exec` reading stdin) deployed in 08d748790; the two hand-stored OpenAI profiles were removed 11/18 23:28Z; the Anthropic api_key profile and the OpenAI OAuth profile are still hand-stored (persona-loop owner) | open |
| Scheduled workflows start hours late or not at all, so the daily measurement and the probes do not run on time | no task yet (dispatch on time from the development host) | open |
| Which step of a failed release invalidates the distillation worker's Registry credential (bursts of 401 inside failed-deploy windows) | release owner; no task yet | open |
| A release recreates the containers and their `docker logs` go with them: the adapter log of the 11/19 10:01Z to 10:08Z meeting failures was gone by 15:50Z; only a gateway log copied by hand at 10:52Z survived, and OOM kills are left only in the host kernel log (release owner) | no task yet | open |
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
On 11/18 four releases succeeded (72fc1b861 at 00:30Z, 638f5b1bd at 02:28Z,
9fcc47112 at 04:15Z, 01c51aa69 at 05:57Z). Two of them switched hosted during
the Taiwan session; the second because a pause of the publish-cut workflow at
04:04:31Z missed a run that had started 27 seconds earlier. Root 44295978076
(89b0b0695) failed after 2 minutes 20 seconds at capture
(`dev_release_artifacts.py:718`, a service with two Compose containers): Dev
Taiwan Market Daily Refresh run 44273374890, dispatched by the release owner's
timer on the development host at 09:10Z, hit its 35-minute step timeout at
09:56:25Z in the middle of a recreate and left a never-started replacement
container for source-ingest; source-ingest itself was down from 09:56:45Z to
12:40:04Z. Every deploy would fail at the same line while that container
exists; the market-data owner removed it at 13:06Z on the operator's
instruction. Behind it consultation-svc has been unhealthy since 06:14Z. All
five refresh runs between 11/17 23:30Z and 11/18 09:10Z failed (RC2).
Evening of 11/18: the publish-cut workflow was paused from 13:06Z until about
16:30Z so that no release would switch and roll back on consultation-svc
health. The fix merged at 15:10Z, but required checks kept failing on two tests
that have nothing to do with the changes under test: a wall-clock bound in the
snapshot-store test (at least seven failures between 10:20Z and 14:42Z with
values from 300 to 773 ms against 250 ms; one documentation PR needed four
runs) and a test loop that ran to its 2,000-tick limit and passed the 300 s
per-test cap. The 16:17Z cron did not fire; Root 44349573838 (5c0463e3b) was
dispatched at 16:38Z by the completion of a dev CI run, finished at 18:02Z and
is accepted. Its configure step wrote the model chain from the dev variable, so
the order set by hand at 14:16Z did not have to be re-applied.

On 11/19 Root 44475158547 (`1c9fbee93`) passed at 08:57Z, before the 09:05Z
persona-window pause, with the liveness, sweep, Agora egress and BFF
owner-state fixes. In the 10:00Z cycle Anthropic was skipped for billing on
every turn and `openai-api/gpt-6.2-sol` answered all 11 turns of the three
trading personas: the provider fallback works. The persona tools loaded (no
`invalid plugin config` line since the gateway started at 07:13Z) and the
personas called them 14 times, but none returned data (row above). The memory
bridge (`DEV_MEMORY_LLM_BRIDGE_ENABLED`) stays false until a cycle shows a tool
returning data.

## RC2. Done means merged, not working

| Work package | Task | State |
|---|---|---|
| WP2.1 probe runner and contract | DEV-DEPLOY-ACCEPTANCE-STAGE-20261117 (#7531) | merged |
| WP2.1 probes for chains already merged (replace the manual hosted acceptance tasks) | to be opened once the probe contract exists | open |
| WP2.1 release probes receive no viewer credentials: the acceptance step of `nonprod-deploy.yml` passes only the URLs and SHAs, so a probe that reads an authenticated BFF route can only skip (found 2026-11-19 while specifying the persona pool probe; remediation mechanisms owner) | DEV-ACCEPTANCE-PROBE-VIEWER-CREDENTIALS-20261119 (operator request 11/19): the stage step gets the existing dev viewer secret `DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET` (already used by `persona-day-acceptance.yml`), the stage redacts secret values from captured output before writing the verdict, and the job stays advisory | open (todo); Root 44560086559 skipped `probe:persona-pool-member-rules` (exit 77) for this reason; accepted when the first Root verdict after deployment shows that probe with an exit code other than 77 |
| WP2.2 three delivery states in reporting | rule in AGENTS.md | in force |
| WP2.3 daily probe "a persona's day" | PERSONA-DAY-ACCEPTANCE-PROBE-20261117 (#7539) with repairs #7573 and #7577; selection repaired by PERSONA-DAY-PROBE-CONTROLLER-SET-20261119 (#7606), which needs BFF-PERSONA-OWNER-LIFECYCLE-TRUTH-20261119 (#7610) on hosted | deployed (1c9fbee93); run 44510111282 (11/19 10:55Z) judged exactly the three trading personas and left out 174 with a reason each; all three failed first at `terminal_packet_today` (no proposal submitted) because every persona tool call was refused (RC1) |
| Health that lies: source-ingest readiness | SOURCE-INGEST-READINESS-TRUTHFUL-20261117 (#7511) | deployed |
| Same-day close: official rows never fetched for a collector-covered session | TW-OFFICIAL-RECONCILE-AFTER-COLLECTOR-20261117 (#7479) | deployed |
| Distillation dead letters from Registry 401 responses | DISTILLATION-AUTH-DEAD-LETTER-RECOVERY-20261117 (#7552) | deployed; 257 of 260 recovered without a manual hosted write |
| Three distillation dead letters with a Registry content mismatch keep the loop reporting failed; 1268 pending rows belong to a retired runtime | no task yet | open |
| A persona turn that falls back to a model backend without host identity loses every Pantheon tool and still counts as a success (persona-loop owner) | PERSONA-TOOLS-MODEL-FALLBACK-IDENTITY-20261118 (#7578) | deployed |
| Operator requirement 2026-11-18: every model provider must work as a fallback for the others, and one stuck provider must not stop everything; operator decision the same day: the model chain is one deployment setting and no code names models (persona-loop owner) | OPENCLAW-MODEL-CHAIN-SINGLE-SOURCE-20261118 (#7590); dev variable `OPENCLAW_MODEL_CHAIN` | deployed; a test turn on 5c0463e3b was answered by openai-api/gpt-6.2-sol; persona agents take the chain at their next ensure |
| Gateway config writes are refused under the plugin overlay, so the consultation agent cannot be ensured (persona-loop owner) | OPENCLAW-CONFIG-WRITE-UNDER-PLUGIN-OVERLAY-20261118 (#7579) | deployed; it introduced the plugin config regression listed under RC1 |
| Operator instruction 2026-11-18: gpt-5.6-sol becomes gpt-6.2-sol; the operator supplied two OpenAI API keys that belong to two organizations | separate providers openai-api and openai-api-2, first by a temporary live repair at 14:16Z, since 17:02Z through the chain variable; OPENCLAW-OPENAI-API-KEY-ROUTE-TWO-KEYS-R2-20261118 (PR #7594) turns the hand-stored keys into environment references; two earlier tasks that put gpt-6.2-sol on the subscription provider were superseded | open |
| MT-2 meeting never triggers: nothing produced its input (persona-loop owner) | MEETING-MT2-PENDING-LESSONS-PRODUCER-20261118 (#7580) | deployed |
| Meetings are convened and not held: in the 11/19 10:00Z cycle all three meetings ended in dead letter after 3 attempts (`consultation agent ensure transport failed`): the adapter starts one `openclaw` CLI per gateway call (5 to 7 s) and holds the agent registry lock against persona ensures, past the executor's 30 s. A cycle packet's `meetings.convened` does not say a meeting was held (persona-loop owner) | CONSULT-AGENT-ENSURE-WITHIN-EXECUTOR-BUDGET-20261119 (#7642) | merged |
| The daily cycle cannot publish its own pool: owner credential not configured (persona-loop owner) | PERSONA-POOL-PUBLISH-BFF-CREDENTIAL-20261118 (#7591) | deployed (1c9fbee93): publication no longer fails on the credential; it now opens a persona panel consultation (next row) |
| Pool publication consultations (`consult-envelope-publication-*`) end in dead letter after 55 to 57 attempts against a maximum of 3: reading the panel case file returns HTTP 401 because consultation-svc has an empty `PANTHEON_PERSONA_SERVICE_TOKEN`, which `issue_dev_paper_principals.py` never issues (persona-loop owner) | CONSULTATION-POOL-READ-PERSONA-PRINCIPAL-20261119 (#7641) | merged |
| Regime evaluation is refused by Registry for the persona principal and reported as a different error (persona-loop owner) | REGIME-EVALUATE-PERSONA-PRINCIPAL-20261118 (#7608) | deployed (1c9fbee93): the refusal is gone; evaluation fails differently (next row) |
| Regime evaluation in the 11/19 10:00Z cycle: all three `registry_unavailable:RemoteDisconnected`; the host kernel log shows Registry killed at its 256 MiB limit at 10:01:20, 10:03:02 and 10:05:30Z (normal peak 93 MB) because evaluate reads all 7,162 entries twice; `REGISTRY_REGIME_STORE_ROOT` is one level above the normalized store, so it read 0 rows (persona-loop owner) | REGIME-EVALUATE-HOSTED-INPUTS-AND-MEMORY-20261119 (#7636); five of the seven input datasets do not exist on hosted and stay with REGIME-DATA-HOSTED-ACCEPTANCE-20261114 (market-data owner) | merged |
| Proposals marked `requires_owner_gate` are consumed by nothing (persona-loop owner) | no task yet | open |
| The cycle records `consult_requests` (trigger `macro_regime_shift`) and nothing reads them; cannot fire while regime is unavailable (persona-loop owner) | attached to REGIME-EVALUATE-PERSONA-PRINCIPAL-20261118; no task until regime evaluates on hosted | open |
| Paper-to-canary promotion is advisory only: the candidacy is recorded and nothing acts on an approval (persona-loop owner; outside the freeze criteria) | no task yet | open |
| Standing questions are refused on hosted: the rollout switch `DEV_MEMORY_LLM_BRIDGE_ENABLED` was never set | MEMORY-INDEX-MODEL-CHAIN-PROVEN-PRIMARY-20261118 (#7588) is deployed and the memory-index agent follows the chain variable; the switch stays off until the persona tools work on hosted, so that the first run of the bridge is not in the same release as that fix (remediation coordinator) | open |
| Taiwan refresh: profiles do not finish within 300 s; partly the cost of a steady tick (87 to 112 s), mainly that the afternoon run asks six chip providers for a session they publish later (market-data owner) | SOURCE-INGEST-STEADY-TICK-OVERHEAD-20261118 (#7574) deployed; TW-CHIP-REFRESH-PUBLISHED-SESSION-20261118 (#7592) merged | merged |
| Taiwan refresh on 11/19 07:40Z and 10:50Z: FinMind monthly revenue and quarterly statements end with source-ingest-scheduler exit 1 in both runs; TPEx institutional flow exit 1, then the 300 s timeout; TWSE margin balances the 300 s timeout in both. The 16:54Z run failed at the shared environment lease while Root 44560086559 held it (market-data owner) | TW-FINMIND-FUNDAMENTALS-CONTROLLER-FRESHNESS-20261119 (#7634) for FinMind; no task yet for the two chip datasets and the lease | FinMind merged; the rest open |
| Taiwan refresh: TAIFEX responses fail to decode; the TAIFEX failures after 07:18Z were the session rule, not decoding (market-data owner) | TW-TAIFEX-OPENAPI-ALL-DATASETS-BOTH-FORMS-20261118 (#7565) deployed; stock-futures contracts were still on the 13:30 rule: TW-TAIFEX-SSF-REFRESH-PUBLISHED-SESSION-20261119 (#7599) merged | merged |
| The refresh test file `scripts/test_dev_tw_market_refresh.py` ran in no CI workflow and 13 of its tests had drifted (an earlier count of 42 came from a copy without .git); a provider session newer than scheduled counted as a failure; FinMind fundamentals used the 13:30 rule (market-data owner) | TW-REFRESH-SESSION-RULES-AND-TESTS-IN-CI-20261119 (#7602) | merged |
| Every snapshot reader waits about 13 s once an hour: the hourly universe sweep held the lock inside a lookup and inside the health route (market-data owner) | SOURCE-SNAPSHOT-SWEEP-OFF-REQUEST-PATH-20261119 (#7598) for the lookup path; SOURCE-INGEST-HOURLY-SWEEP-PROCESS-STALL-20261119 (#7615) for the health path | deployed (1c9fbee93) |
| The BFF reports a projected lifecycle (`paper_running`) for a persona the owner records as `research_only`, also in the field named `owner_lifecycle_state`, so the probe judged a persona the controller never runs (persona-loop owner) | BFF-PERSONA-OWNER-LIFECYCLE-TRUTH-20261119 (#7610) | deployed (1c9fbee93): hosted shows `ownerLifecycleState` `paper_owner` for the three trading personas and `research_only` for the deploy-probe persona; residual: 165 rows the BFF shows as retired |
| Test files that no CI job runs drift silently: 152 of 209 `scripts/test_*.py` files were run by no workflow, including the model pool, persona-day probe and storage measurement tests | CI-EVERY-TEST-FILE-RUNS-20261119 (#7620): a required check fails on a test file that no required job runs and that has no listed reason | merged |
| The Agora source connector called `urlopen` with its bearer token, no host check and default redirects; the egress guard test that would have failed runs in no CI job (persona-loop owner) | AGORA-SOURCE-INTERNAL-EGRESS-GUARD-20261119 (#7616) | deployed (1c9fbee93) |
| A refresh step marked `continue-on-error` failed while the run showed green; FinMind fundamentals never passed since 11/10 (release owner) | TW-REFRESH-NON-BLOCKING-STEP-FAILURE-VISIBLE-20261118 (#7605) | merged |
| Collector intake re-verifies six whole-day TPEx bundles (131 MB, larger than the 72 MB metadata cache) on every pull, so TPEx history advances 16 rows per pull (market-data owner) | COLLECTOR-PRIORITY-METADATA-REVERIFY-20261119 after COLLECTOR-RAW-READS-KEEPALIVE-CONCURRENT-20261119 (#7611) | open |
| The persona page cannot show what a persona trades (operator question 2026-11-19): the pool view returns only member ids; the strategy page labels the trading member a no-order rollback baseline because the pool envelope names it as a parent, and shows the envelope as an unsupported kind; pool health is always `cycle_pool_snapshot_mismatch` because the cycle's refused reweight appends a version after its observation (deliberate: trial counts are read from versions). The only member of each pool is the shared base momentum strategy (persona-loop owner) | BFF-PERSONA-STRATEGY-VISIBILITY-20261119 (#7625, f146fd196), then FE-PERSONA-STRATEGY-VISIBILITY-20261119 (execute-plans #1043, a318cc2d6) | accepted: the repaired probe (PERSONA-POOL-PROBE-CHECKS-LIVE-PERSONAS-20261119, #7640, 394b02a46) read all 177 personas and passed for the three trading personas against hosted at 16:36Z (BFF 2fb152cd3 while its release was switching, frontend a318cc2d6; both backends since d685eb962 contain the fix); its first version had passed with zero personas checked; both pages were read in a browser |

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
Second natural daily cycle, 11/18 10:00Z on 01c51aa69 (reported by the
persona-loop owner): enrolled yes; cycle ticked yes; a decision used memory no;
one MT-1 meeting convened; results written back no. All three turns ended
`tool_record_no_calls`: the gateway's Anthropic key returned 401, the turn fell
back to the claude-cli backend, and that backend blocks every Pantheon tool.
Key history from the gateway's own session files: last successful Anthropic
call 11/01 19:45Z; `credit balance too low` until 11/17 08:53Z; `invalid
x-api-key` from 11/17 09:24Z. The stored gateway profile and the deployed
secret were the same key before and after, so the change was at Anthropic and
not in a deploy. The operator supplied a new key on 11/18 (secret set 12:58Z,
stored profile 13:00Z); it authenticates and the account has no credit: at
13:06Z the running gateway reported `billing` for Anthropic and answered
through openai/gpt-5.6-sol. The
gateway's OpenAI OAuth profile refreshed by itself at about 12:50Z. The
persona-day probe merged without ever running against hosted and had four
defects (secret missing; exit code masked by a pipe; due time set before the
10:00Z cycle; 12 personas selected instead of 3). Taiwan refresh runs
(market-data owner, from job logs): 44199899154, 44208313860, 44253021385,
44260667906 and 44273374890 failed in four forms: preflight timeout after the
run's own recreate (RC1), the 300 s bound, TAIFEX decode errors, and
`market_input_stale` on the same day while TPEx close rows were half delivered.
Evening of 11/18. OpenAI side: the operator's two API keys answer gpt-6.2-sol
on api.openai.com; the model is not accepted on the ChatGPT subscription route
for the development host's login, and the hosted login was not tested. Three
facts about the pinned gateway (OpenClaw 2026.7.1), found by running its image
on the development host before touching hosted: a custom provider with one
stored api_key profile works; a 401 moves the turn to the next provider; a
model_not_found puts the whole provider profile into cooldown, so later
candidates on the same provider are skipped without a call. Two tasks that
would have placed gpt-6.2-sol ahead of gpt-5.6-sol on the subscription provider
were superseded for that reason. A change of the fallback list needs a gateway
restart although the CLI reports that it applies without one. On 5c0463e3b the
default, structured-extraction and memory-index chains are Opus,
openai-api/gpt-6.2-sol, openai-api-2/gpt-6.2-sol, openai/gpt-5.6-sol,
openai/gpt-5.5; with Anthropic in billing cooldown a test turn was answered by
openai-api/gpt-6.2-sol in under a second. The three trading persona agents
still carry Opus, claude-sonnet-4-6, gpt-5.5 until their next ensure. Whether
the 11/19 10:00Z cycle has tools depends on the plugin fix under RC1. The
hourly snapshot stall is the live counterpart of the wall-clock test under RC1:
the test measured a store mode hosted does not use (`MARKET_SNAPSHOT_STORE` is
unset, so journal), while the mode hosted does use blocks readers for 13 s
every hour.

The persona-day probe now selects personas by the owner's lifecycle, read from
the BFF field that carries it since #7610. Its first hosted run after the
repair (44510111282, dispatched by hand) judged the three trading personas and
no other. GitHub's 11:30Z schedule for the probe started at 19:29Z on 11/18,
eight hours late, so the day's verdict can come after the next release; the
dispatch on 11/19 did not wait for it.

Third natural daily cycle, 11/19 10:00Z on 1c9fbee93: enrolled yes; cycle
ticked yes (terminal, no proposal submitted); a decision used memory no (every
tool call refused, RC1); a meeting was held no (three convened, none held);
results written back no. #7630 is deployed in 2fb152cd3. #7634, #7636, #7641
and #7642 merged after that release was cut at 15:54Z and are merged, not
deployed; they need a release served before the 11/20 10:00Z cycle, which is
the first that can meet the freeze criteria.

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
| WP3.1 hosted reclaim of deleted rows (coordinator) | TELEMETRY-HEARTBEAT-HOSTED-ACCEPTANCE-20261117 | open; dry run 11/18 07:09Z: 5.48 million rows to delete, 17.5 GB to return; the operator chose a one-time window on Saturday 2026-11-21 at about 09:20Z |
| WP3.1 two snapshot pre-compact backup files on hosted (988 MB, created by hand) | release and hosted operations owner | open |
| WP3.1 heartbeat state digest contained a timestamp, so a snapshot was written on almost every heartbeat | PAPER-RUNTIME-STATE-SNAPSHOT-CHANGE-DETECTION-20261118 (#7559) | deployed; not yet re-measured in a trading session |
| WP3.1 market data stored once per key (market-data owner) | SOURCE-HOSTED-LISTING-STATE-AND-PIN-SOURCE-20261118 (#7521), SOURCE-DAILY-BARS-READERS-COMPLETE-20261118 (#7522) | deployed |
| WP3.1 market data retention sweeper (market-data owner) | SOURCE-RETENTION-SWEEPER-20261118-R2 (#7526) | deployed |
| WP3.1 persona OODA packet store rewrites the whole file per append (persona-loop owner) | PERSONA-OODA-PACKET-STORE-BOUNDED-20261117 (#7544) | deployed |
| WP3.2 storage manifest and test | STORAGE-MANIFEST-AND-GUARD-20261117 (#7488) | merged |
| WP3.3 daily hosted measurement | DEV-STORAGE-BUDGET-DAILY-MEASUREMENT-20261117 (#7499) | merged; the scheduled run did not start, first run by hand on 11/18 (44245690990): red with four findings (telemetry_events 31.7 GB against 5 GB, event_receipts 4.29 GB against 0.5 GB, work_claims 191 MB against 50 MB) |
| WP3.3 daily measurement of container memory, OOM kills and restarts | DEV-MEMORY-BUDGET-DAILY-MEASUREMENT-20261117 (#7542) | merged |
| WP3.4 container memory limits | DEV-COMPOSE-MEMORY-LIMITS-20261117 (#7515) | deployed; sized wrongly, repaired by the next row |
| WP3.4 limits sized from the measured cgroup peak | DEV-COMPOSE-MEMORY-LIMITS-PEAK-SIZED-20261117 (#7537) | deployed |
| WP3.4 persona cycle controller polls the adapter less often (persona-loop owner) | PERSONA-CYCLE-CONTROLLER-CRON-POLL-BUDGET-20261117 (#7534) | deployed |
| WP3.4 adapter has no cap on concurrent `openclaw` CLI calls, about 400 MiB each (persona-loop owner) | no task yet; owner decides after the 2026-11-18 10:00Z cycle | open |
| WP3.5 development host: worker test temp is never removed | DEV-WORKER-TEMP-DIR-BOUND-20261117 (#7538) | merged; live since the supervisor runtime promotion of 11/18 04:06Z |
| WP3.4 incidents exceeds its limit while starting | DEV-MEMORY-BUDGET-STARTUP-PEAK-20261118 (#7572) | merged |
| WP3.4 the gateway container reached its 1280 MiB limit 73 times on 01c51aa69 without a kill (persona-loop owner; re-measure after the 11/19 10:00Z cycle) | no task yet | open |
| WP3.4 source-ingest glibc thread arenas grew from 36 to 48 (1.17 to 1.89 GB) in two hours; same shape as the BFF on 11/07; temporary limit of 10 GiB on hosted from 11/19 02:11Z (market-data owner) | SOURCE-INGEST-MALLOC-ARENA-LIMIT-20261119 (#7617) | merged |
| WP3.2 the 5 GB manifest bound for telemetry_events cannot be met: after the reclaim about 14 GB remain, of which 8.5 GB are event types with no retention | needs an operator decision on retention | open |

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
| WP4.3 reviewer reassignment picks the owner's family and records no exception; a commit made after the reassignment cannot be closed | SUP-REVIEWER-REASSIGNMENT-RECORD-AND-DONE-GATE-20261117 (#7553) | merged; live since 11/18 04:06Z; the two stuck tasks were closed with `resume_integration` |
| WP4.3 a handoff whose commits carry another owner's trailer is refused at admission | SUP-HANDOFF-OWNER-TRAILER-ADMISSION-20261118 (#7569) | merged; live since 11/18 08:02Z; not yet exercised |
| WP4.3 lost-lease recovery moves the owner lane to another family and pairs it with a reviewer of that same family; the recorded exception says `no capacity evidence supplied` for Claude although its account was healthy, and the new owner cannot hand off the head already on the PR because its trailers name the previous owner and reviewer (development tooling owner) | no task yet | open; instance DEV-ACCEPTANCE-PROBE-VIEWER-CREDENTIALS-20261119: the Claude worker process vanished at 11/20 00:18Z, recovery set Antigravity/Antigravity2, the green head 82f18a910 of #7651 was refused at `reject_stale_reviewer_trailer_at_handoff`, and the task waited on Human/Ops until a Human/Ops `assign` restored Claude/Antigravity at 00:33Z |
| WP4.2 a worker whose fix needs a file outside the artifact contract waits for a coordinator with no signal: two persona-loop tasks sat blocked for over two hours on 11/18 | joins the WP4.2 tooling task, to be opened | open |
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
On 11/18 OPENCLAW-OPENAI-API-KEY-ROUTE-TWO-KEYS-R2-20261118 sat blocked from
15:28Z to 18:15Z waiting for one file to be added to its artifact contract; its
own coordinator did not notice, because a blocked row sends no signal. The 250
ms bound that stopped the required check all afternoon was written into an
acceptance item as a bound and accepted although the delivering commit already
showed one failure in three runs on the development host.

On 11/19 00:45Z the remediation coordinator told the probe worker to select
personas by a field of the BFF cycle view instead of the controller's rule,
after reading only BFF projections; the worker refused because that was a
second rule, and the owner table showed the cause was the BFF projection. The
fix went to the source (#7610).

## RC5. Coordinators each fix their own symptom

| Work package | Task | State |
|---|---|---|
| WP5.1 one owner per domain | table above | in force (operator decision 3) |
| WP5.2 this register | this file | in force |
| Production first release: `post_identity` ran without the release environment and reported a failed compose lookup as "active container not found", which rolled back Prod run 44485045441 on 11/19 (production release) | ENV-PROD-CONTROL-IMPL-001 g70 (#7623) | merged; passed on Prod run 44546522009 |
| Two bootstrap copies: staging's runtime-manager caller token (2efb0d291, 10/30) never reached `deploy/production/bootstrap.sh`; on Prod run 44546522009 (Release 111, 11/19 14:52Z) paper-fleet-reconciler and paper-signal-producer send the opaque secrets-file value, runtime-manager answers 403 `AUTH_TOKEN_FORMAT`, and both stay unhealthy while the switch reported `prod_active`, because it checks only BFF readiness and the projector count (production release) | ENV-PROD-CONTROL-IMPL-001 g71 | open |
| The paper reconciler and signal producer read one static runtime-manager bearer at start-up, while BFF, persona and consultation mint a short-lived HS256 token per call, so any minted bearer expires under them (persona-loop owner) | no task yet | open |
| Production runtime-manager runs the default `permissive` auth mode and accepts an unsigned `actor:role` bearer from any container on its network; staging not checked (production release) | no task yet | open |

Instances: the snapshot journal incident was opened by four conversations.
On 11/18 04:06Z two conversations started a supervisor runtime promotion 14
seconds apart because the operator had approved it in both; the second stopped
on the integrator lock and changed nothing. Since then the market-data owner
runs promotions and announces each one first.

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
  Antigravity reviews again since 2026-11-18.
- A task whose product is a probe, a measurement or a gate is run once
  against hosted by its coordinator after the merge, and the artifact is
  read, before the task is reported as more than `merged`. The persona-day
  probe was green for hours without checking anything.
- This repository is public and its secret scanning is off: workflow logs
  can be read by anyone and artifacts by any signed-in GitHub user. Never
  write a credential, a container environment or a rendered Compose config
  to a log, an artifact or an evidence file.
- To compare two credentials, compare their length and the first eight
  characters of their SHA-256; do not print either value.
- A cycle packet that lists a meeting as convened does not say it was held;
  read the consultation work item.
- Container logs are gone after the next release recreates the container.
  Copy the log you need before then, and read OOM kills from the host kernel
  log.
- A rule that must agree with an owner's decision is read from the owner's
  data, not from a projection of it. Check the owner store before writing a
  selection or a probe condition.
- A new test file is run by a required CI job by default; an exemption needs a
  reason and an owner in `docs/operations/ci-test-inventory.json`.
- A time bound in an acceptance item is proven by a measurement recorded at
  delivery or by the daily hosted measurement. It is not written as a
  wall-clock assertion on the required check path: on shared CI that measures
  the machine. Where a bound must gate, gate on an event (a read completes
  while a write transaction is open), not on elapsed time.
- On the pinned gateway a refused model cools down its whole provider. Never
  place a model that is not proven for an account ahead of a proven one on the
  same provider; give it a provider of its own.
- A coordinator reads the `blocked` rows of its own tasks at least once an
  hour while work is in flight; a worker that needs a file added to the
  artifact contract can only say so in the row.

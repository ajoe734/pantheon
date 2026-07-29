# L12-MANIFEST-001 — runtime manifest readback

Owner `Claude2`, reviewer `Antigravity`. Branch `task/L12-MANIFEST-001`, head
`94abec5f84e18481511496955d322c7842a01612` over base `dev`
`f12daadc29b86db5cdcf5160a17c9fbdc9f83ad8`. Cut v1.0.1 at `2026-07-29T00:36:31Z`.

> **Correction in v1.0.1.** Cut v1.0.0 read the kill-one probe in §6 as proof
> that the `unless-stopped` policy restarted the worker unaided. Re-readback of
> the same container reports `RestartCount=0`, which refutes that. The claim is
> withdrawn; see §6 for what the readback does and does not support. No
> implementation file changed between the two cuts — `docker-compose.yml` and
> `scripts/deploy_nonprod_vm.sh` are byte-identical to the v1.0.0 validation
> head `4cf8feed`, so every other result below stands unchanged.

## 1. The gap, reproduced before changing anything

`docker compose config --services` under the deploy script's default profile set
did not resolve `policy-learning-shadow-eval-scheduler`. The live dev stack
(`docker compose project pantheon`, 55 containers) had no such container. The
human-imitation shadow-evaluation loop worker was therefore never started by a
normal deploy — the loop existed in the file and never ticked.

This independently reproduces Gap 1 of
`docs/bff/execution-tasks/2026-07-26-twelve-loop-gap/POST_DISPATCH_RUNTIME_GAP_DELTA.md`.

Secondary findings from the same audit:

- `search-index-scheduler` started only because the *deploy script's profile
  string* happened to list its profile, not because the manifest said so.
- `search-index-scheduler` had no `restart:` key at all.
- No required loop worker declared `stop_grace_period`, except the four
  `evolution*` services.
- `strategy-distillation-worker` wrote a heartbeat file that nothing read.
- `deployment-outbox-consumer` supported a heartbeat file whose compose default
  was the empty string, so it was never written.

## 2. What changed

### `docker-compose.yml`

| change | services |
| :-- | :-- |
| profile gate removed, now default-on | `policy-learning-shadow-eval-scheduler`, `search-index-scheduler` |
| `restart: unless-stopped` added | `search-index-scheduler` |
| `stop_grace_period: 30s` added | all 27 required loop workers |
| heartbeat healthcheck wired | `strategy-distillation-worker`, `deployment-outbox-consumer` |
| heartbeat file defaulted on | `deployment-outbox-consumer` |

### `scripts/deploy_nonprod_vm.sh`

- New `validate_required_loop_workers`: resolves the selected profile set through
  `docker compose config --services` and **aborts the deploy** if any required
  loop worker is missing, or if the legacy `pantheon-paper-runtime` resolves
  alongside the reconciler-owned fleet.
- `search-index-scheduler` dropped from the default profile string, because the
  worker is now default-on in the manifest itself.
- The adjudication for `source-ingest-scheduler` is recorded in place.

The inversion this encodes: **the manifest, not the deploy script's profile
list, is the single source of truth for which loops run.** A narrowed
`PANTHEON_DEV_COMPOSE_PROFILES` can no longer silently deactivate a loop.

## 3. Required loop worker inventory

Rendered with `docker compose -f docker-compose.yml --env-file /dev/null config`
and **no profile selected** — i.e. this is what a bare `docker compose up` gives.

| service | restart | healthcheck | stop_grace | volumes |
| :-- | :-- | :-- | :-- | :-- |
| `source-ingest` | unless-stopped | yes | 30s | 1 |
| `strategy-distillation-worker` | unless-stopped | yes | 30s | 1 |
| `alpha-replication-worker` | unless-stopped | no | 30s | 2 |
| `training-session-svc` | unless-stopped | yes | 30s | 2 |
| `training-session-preview-worker` | unless-stopped | yes | 30s | 1 |
| `policy-learning-svc` | unless-stopped | yes | 30s | 1 |
| `policy-learning-shadow-eval-scheduler` | unless-stopped | no | 30s | 0 |
| `consultation-svc` | unless-stopped | yes | 30s | 1 |
| `deployment` | unless-stopped | yes | 30s | 3 |
| `deployment-outbox-consumer` | unless-stopped | yes | 30s | 0 |
| `runtime-manager` | unless-stopped | yes | 30s | 1 |
| `broker` | unless-stopped | yes | 30s | 1 |
| `capital` | unless-stopped | yes | 30s | 1 |
| `paper-fleet-reconciler` | unless-stopped | yes | 30s | 1 |
| `paper-signal-producer` | unless-stopped | no | 30s | 0 |
| `reconciliation-drift-svc` | unless-stopped | yes | 30s | 1 |
| `reconciliation-drift-consumer` | unless-stopped | no | 30s | 1 |
| `reconciliation-drift-scheduler` | unless-stopped | no | 30s | 0 |
| `reconciliation-drift-incident-listener` | unless-stopped | no | 30s | 1 |
| `evolution` | unless-stopped | yes | 30s | 2 |
| `evolution-dispatch-worker` | unless-stopped | yes | 30s | 1 |
| `evolution-daily-sweep-scheduler` | unless-stopped | yes | 30s | 0 |
| `evolution-threshold-sweep-producer` | unless-stopped | yes | 30s | 2 |
| `operator-bff` | unless-stopped | yes | 30s | 5 |
| `loop-run-projector-scheduler` | unless-stopped | yes | 30s | 1 |
| `search-svc` | unless-stopped | yes | 30s | 2 |
| `search-index-scheduler` | unless-stopped | no | 30s | 0 |

27 / 27 default-on, 27 / 27 supervised restart, 27 / 27 graceful stop,
21 / 27 with a healthcheck. The six without one are listed as a residual risk in
`evidence.json`: their worker modules publish no heartbeat surface, and adding
one is a service source change outside this task's artifact scope.

`pantheon-paper-runtime` does not resolve.

## 4. The egress adjudication

`source-ingest-scheduler` and `source-ingest-agora-projector` **stay opt-in**
behind the bounded `source-ingest-scheduler` profile.

Acceptance 1 ("every required worker started by the intended default") and
acceptance 3 ("source egress remains bounded and deny-by-default") pull in
opposite directions for exactly this one worker. An always-on scheduler ticking
every 60 seconds against Yahoo, CoinGecko, TWSE/TPEx, MOPS, FinMind,
SEC/FRED/FINRA and stooq is continuous crawling from one cloud egress IP.
Acceptance 3 wins, and this is now a stated decision with a gate behind it
rather than an incidental omission:

- `source-ingest` — the loop's owning service — **is** default-on, healthy, and
  durable, so the loop is reconcilable.
- `validate_source_refresh_profile` still refuses any non-`deny` egress posture
  when the bounded profile is not selected, and still bounds tick count,
  concurrency, record count, and the exact host allowlist when it is.
- The projector shares that profile because it must project *after* the bounded
  refresh completes; decoupling it would let it publish a stale projection.

Consequence, stated plainly: the `source_ingestion` loop's `scheduled_tick`
evidence still requires an explicit bounded operator run. It does not arrive
from the default deploy. That residual is recorded in `evidence.json`.

## 5. Safety readback

Rendered with no operator override:

| service | key | value |
| :-- | :-- | :-- |
| `runtime-manager` | `PANTHEON_LIVE_BROKER_ENABLED` | `false` |
| `runtime-manager` | `PANTHEON_CANARY_EXECUTION_ENABLED` | `false` |
| `broker` | `BROKER_PAPER_ENABLED` | `false` |
| `source-ingest` | `PANTHEON_EXTERNAL_EGRESS` | `deny` |
| `source-ingest` | `PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS` | *(empty)* |

Live execution and canary execution are denied by the file itself; paper
execution is opt-in and is what the deploy path turns on. No worker activated by
this change holds order, broker, or capital authority.

## 6. Proofs actually run

**Compose config.** `config --quiet` over four profile sets — empty, the deploy
default set, the default set plus `source-ingest-scheduler`, and
`static-paper-runtime`. All four render.

**Required-worker gate.** `validate_required_loop_workers` extracted verbatim
and exercised over five profile sets. Default / empty / bounded-refresh pass.
`static-paper-runtime` is refused with `duplicate legacy workers activated
alongside the loop manifest: pantheon-paper-runtime`. An injected non-existent
required worker is refused with `required loop workers not activated by the
selected profiles`. Both failure paths fire, not just one.

**Heartbeat healthchecks, in real containers.** The rendered healthcheck test
strings were executed inside the already-running live containers:

- `strategy-distillation-worker` — exit `0` against an alive file rewritten 49
  seconds earlier; exit `1` with a zero-second freshness bound.
- `deployment-outbox-consumer` — exit `1` with no heartbeat file (today's live
  state, env unset), exit `0` with a fresh one, exit `1` with one aged past the
  120-second bound.

**Local stack activation.** `docker compose -p pantheon up -d --no-deps
--no-recreate policy-learning-shadow-eval-scheduler` — scoped so no existing
container was recreated. The worker came up and logged:

```
{"startup_recovery": {"reset_count": 0, "status": "ok"}, "worker_id": "shadow-eval-…"}
{"cycle": {"processed_count": 0, "status": "ok"}, …, "result": {"eval_type": "shadow",
 "production_training": "fail_closed", "status": "ok", "tick_id": "shadow-tick-shadow-3600s-495912",
 "ticked_at": "2026-07-29T00:14:36Z"}, "tick": 1, …}
```

Authenticated, tenant-bound, no 401.

**Kill-one worker restart — partially proven, corrected in v1.0.1.**
`docker kill --signal=SIGKILL` on that container, then `docker inspect` and
`docker logs --timestamps` over the same window.

| readback | value |
| :-- | :-- |
| `.State.FinishedAt` | `2026-07-29T00:14:50.962927635Z` |
| `.State.StartedAt` | `2026-07-29T00:16:32.319332602Z` |
| `.RestartCount` | **`0`** |
| `.State.ExitCode` | `0` |
| `.HostConfig.RestartPolicy.Name` | `unless-stopped` |
| `.Config.StopTimeout` | `30` |

*Proven.* The rendered restart policy and graceful-stop value reach the runtime,
not just the file. And the worker recovers cleanly from an abrupt termination:
once running again it logged `startup_recovery` `status ok` with `reset_count 0`
and a fresh tenant-bound authenticated tick at `2026-07-29T00:16:33.111Z`, no
401. An unattended restart would not need manual state repair.

*Not proven, and withdrawn from v1.0.0.* That the `unless-stopped` policy
performed that restart with no operator action. `RestartCount=0` and
`ExitCode=0` are not what a policy-driven restart leaves behind, and the
101.4-second outage reads as operator-initiated recovery.

*Why the probe was unsound.* `docker kill` is not a valid test of a restart
policy at all: the daemon records an explicit kill or stop as operator intent
and suppresses `unless-stopped` by design. A sound probe crashes the container's
PID 1 from inside — `docker exec <c> kill -9 1` — so the exit is unsolicited,
and is confirmed by `RestartCount` incrementing to 1. That probe is destructive
against the shared live dev stack and was not authorised in this slice. It is
recorded in `evidence.json` as residual risk
`auto_restart_policy_not_proven_end_to_end` for the reviewer to adjudicate:
either authorise the crash probe, or accept the inspected configuration plus the
clean worker-side recovery as sufficient for AC2.

**Regression suites.** 24 compose-activation contract tests and 106 deploy-script
contract tests pass.

## 7. What a reviewer should check independently

```bash
# 1. every required worker resolves with no profile selected
docker compose -f docker-compose.yml --env-file /dev/null config --services | sort

# 2. the gate refuses a narrowed profile set that drops a worker,
#    and refuses the legacy static paper runtime
bash -n scripts/deploy_nonprod_vm.sh
sed -n '/^# One required scheduled/,/^}$/p' scripts/deploy_nonprod_vm.sh

# 3. safety defaults with no override
docker compose -f docker-compose.yml --env-file /dev/null config --format json \
  | jq '.services["runtime-manager"].environment
        | {PANTHEON_LIVE_BROKER_ENABLED, PANTHEON_CANARY_EXECUTION_ENABLED}'

# 4. the egress adjudication is still enforced
sed -n '/^validate_source_refresh_profile/,/^}$/p' scripts/deploy_nonprod_vm.sh

# 5. the corrected restart readback — confirm RestartCount is still 0,
#    i.e. that §6's withdrawal is right and the auto-restart trigger
#    remains unproven
docker inspect pantheon-policy-learning-shadow-eval-scheduler-1 --format \
  'RestartCount={{.RestartCount}} ExitCode={{.State.ExitCode}}
   Policy={{.HostConfig.RestartPolicy.Name}} StopTimeout={{.Config.StopTimeout}}'
```

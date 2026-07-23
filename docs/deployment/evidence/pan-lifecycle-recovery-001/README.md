# PAN-LIFECYCLE-RECOVERY-001 delivery evidence

Status: accepted on the replacement dev VM; closeout PR and governed task
finalization in progress.

Environment: replacement Pantheon dev VM only (`pantheon-lupin-dev`, project
`pantheon-lupin-dev-20260719`). Production, live capital, and order routing are
out of scope.

## Failure and repair

The projector had stopped at generation 5036 after `ENOSPC`; `current` last
advanced at `2026-07-21T11:58:00Z`, the scheduler had restarted 81 times, and
the hosted BFF did not expose projector freshness in readiness.

The delivered repair now:

- retains at most 32 recognized completed generations by default;
- always preserves the generation referenced by `current` and only changes
  `current` after a complete atomic publish;
- removes only projector-owned staging directories older than 3600 seconds,
  leaving unknown and recent directories untouched;
- reserves a retention slot before publish and survives `ENOSPC` during both
  primary and durable-error publication without advancing the checkpoint;
- avoids a new generation for an identical repeated source error;
- publishes a bounded `health_state.json` independently of generation bundles;
- aligns the BFF Trade Journey and loop-run read stores to the projector's
  atomic `current` bundle;
- fails root and `/bff/readyz` closed for stopped, stale, error, non-live,
  generation-mismatched, excessive-backlog, or low-disk state; and
- exposes worker/controller state, generation, checkpoint/high watermark,
  backlog, last poll/publish, deployment SHA, disk, freshness, retention, store
  alignment, and safe reasons.

Default thresholds are 32 retained generations, 3600-second staging cleanup,
120-second freshness, maximum backlog 5000, minimum free space 128 MiB and 5%.

## Repository delivery chain

All code changes were reviewed and merged to `dev`:

| PR | Merge commit | Scope |
|---|---|---|
| [#3958](https://github.com/ajoe734/pantheon/pull/3958) | `9a24478ed2570cfa542977deaa35942bc612ffe2` | retention, recoverable publish, readiness truth, compose health |
| [#3971](https://github.com/ajoe734/pantheon/pull/3971) | `cc3b90d3c5e257e1ec6d8ac00627bfb17676c93b` | remove redundant post-switch retention pass; set observed-safe 120-second freshness default |
| [#3976](https://github.com/ajoe734/pantheon/pull/3976) | `bb482ac3900cfd4ddbddf5464029386b42d0a42` | align canonical BFF read stores with projector `current` |
| [#3978](https://github.com/ajoe734/pantheon/pull/3978) | `3f71ed3859bd29651a7313c2aae653cbcf4ca766` | bounded worker/controller readiness state and publish ordering |
| [#3981](https://github.com/ajoe734/pantheon/pull/3981) | `fb2df8ec805754a3bf7a83ea544138ca9c32c521` | bounded hosted BFF surface convergence and redacted failure diagnostics |
| [#3983](https://github.com/ajoe734/pantheon/pull/3983) | `35d7e572445dab5f4702670771e50560955de49e` | use the provisioned MFA-bound operator A probe credential |
| [#3988](https://github.com/ajoe734/pantheon/pull/3988) | `d7961ed4cfd691e41f3bba6b7cc680c91406ab3a` | bind readback verification to the governed `operator_a` identity |

The accepted deployment candidate is the final merge commit
`d7961ed4cfd691e41f3bba6b7cc680c91406ab3a`.

## Hosted deployment chronology

The initial governed deploy
[run 29945331144](https://github.com/ajoe734/pantheon/actions/runs/29945331144)
put exact SHA `9a24478e...` on the replacement VM and correctly failed closed.
Disk pressure was gone (about 182 GB and 70.4% free), and the projector
converged at generation 5041/checkpoint 657255, but 61-second large-volume
polls exceeded the original 30-second freshness setting and made the BFF
health gate flap. PR #3971 removed a redundant post-switch retention interval
and set the still-fail-closed default to 120 seconds.

During [run 29952688803](https://github.com/ajoe734/pantheon/actions/runs/29952688803)
attempt 1, this worker's overly frequent GitHub REST polling exhausted the
shared API quota, so lease verification failed rather than accepting an
unverified deploy. This was worker-caused operational interference, not a
product failure. A **temporary live repair** through governed
[run 29956662015](https://github.com/ajoe734/pantheon/actions/runs/29956662015)
restored the prior accepted `6d1aadd...` strict-auth BFF while the repository
repair continued. No manual payload or `current` symlink edit was used.

Attempt 2 of run 29952688803 deployed `3f71ed38...`; the immutable source proof
passed at generation 5279/checkpoint 688865 with the complete lifecycle event
chain. Public BFF readback still failed. Later safe diagnostics in
[run 29962480922](https://github.com/ajoe734/pantheon/actions/runs/29962480922)
showed four bounded HTTP 401 responses while its source proof passed at
generation 5391/checkpoint 702981. Strict dev intentionally marks the generic
operator credential as not MFA-verified, so PR #3983 selected the already
provisioned MFA-bound operator A credential without weakening the generic auth
floor. PR #3988 then made the expected `operator_a` identity explicit.

[Run 29964133833](https://github.com/ajoe734/pantheon/actions/runs/29964133833)
deployed `35d7e572...` and proved restart continuity: pre-switch generation
5423/checkpoint 707162 was followed by a fail-closed 503 recovery state at
generation 5437/checkpoint 709229, then a 200 live state at generation
5438/checkpoint 709286. Its remaining readback identity mismatch was the input
to PR #3988.

[Run 29966009368](https://github.com/ajoe734/pantheon/actions/runs/29966009368)
deployed exact final SHA `d7961ed4...`. Public observation recorded the
expected 503 `recovering` state at generation 5482/checkpoint 715771, then a
brief 502 restart window, followed by live recovery. Three consecutive fresh
cycles were:

| Observed UTC | Generation / checkpoint | State | Freshness | Free disk |
|---|---:|---|---:|---:|
| `2026-07-22T23:56:50Z` | 5484 / 715964 | ready, live, accepted, backlog 0 | 6.9 s | 179.8 GB / 69.46% |
| `2026-07-22T23:58:06Z` | 5486 / 716162 | ready, live, accepted, backlog 0 | 43.8 s | 179.8 GB / 69.46% |
| `2026-07-22T23:59:07Z` | 5487 / 716352 | ready, live, accepted, backlog 0 | 25.0 s | 179.5 GB / 69.33% |

All three samples reported deployment SHA `d7961ed4...`, matching
worker/controller generations, no reasons, retention 32, staging cleanup 3600
seconds, and aligned BFF read stores.

The run's canonical source artifact failed before readback because one queued
paper stimulus was consumed but did not produce its exact target
`position_snapshot` within 180 seconds. The same binding/runtime continued to
publish later canonical snapshots, and the identical governed stimulus had
passed in five seconds during run 29952688803 attempt 2. Because this is not an
accepted stimulus/readback result, it was retained as negative evidence and
not used for acceptance.

Exact-SHA governed rerun
[29967962811](https://github.com/ajoe734/pantheon/actions/runs/29967962811)
completed successfully at `2026-07-23T00:21:20Z`. The source artifact observed
at `00:20:08Z` proved:

- baseline high watermark 719976 advanced through candidate sequence 720119 to
  source high watermark 720174;
- generation 5516/checkpoint 720174 was ready, live, accepted, backlog 0, and
  bound to exact deployment SHA `d7961ed4...`;
- one stable paper identity linked a canonical sequence of eight events:
  `signal_generation`, `trade_decision`, `risk_evaluation`,
  `order_submitted`, `order_accepted`, `paper_fill_simulated`,
  `position_snapshot`, and terminal `reconciliation_failed`; and
- the resulting loop/journey was formal `completed_with_variance` truth in
  generation 5516.

The authenticated public-BFF artifact observed at `00:20:29Z` proved strict
auth with stub disabled and exact hosted SHA `d7961ed4...`. The MFA-bound
identity was `operator_a`; missing credentials and arbitrary bearer tokens
returned 401 for both loop and journey routes. Loop detail, Trade Journey
detail, and Trade Journey evidence each returned 200 on the first attempt with
no transient status, the same generation/controller, and exact 8/8 event-ID
correlation. The artifact records no access token, credentials, response
payloads, DSN, or source payloads. Its source artifact SHA-256 is
`961f67afa574a2da3c6543c5526e9125334c51a09f889d4cf4e65d3d47c2d220`.

The same governed run also passed public exact-version proof, OpenClaw smoke,
Agora restart persistence, lease release, and deployment summary. This is the
final accepted deployment run.

## Local verification

The final task branch passed:

```text
python3 -m py_compile \
  services/trade_journey/hosted_bff_readback.py \
  services/trade_journey/lifecycle_projector.py \
  services/control-plane/bff/main.py \
  services/control-plane/bff/test_lifecycle_projector_readiness.py

/tmp/pan-lifecycle-recovery-001-venv/bin/python -m pytest -q \
  services/trade_journey/test_hosted_bff_readback.py \
  services/trade_journey/test_lifecycle_projector.py \
  services/trade_journey/test_lifecycle_projector_compose.py \
  services/control-plane/bff/test_lifecycle_projector_readiness.py
# 33 passed

docker compose -f docker-compose.yml config --quiet
git diff --check
```

## Acceptance ledger

| Evidence | Result |
|---|---|
| PR and independent review | Seven task PRs merged; canonical status reviewer approved closeout |
| Merge/deployed SHA | `d7961ed4cfd691e41f3bba6b7cc680c91406ab3a` |
| Safe cleanup and retention | deterministic 32-generation bound; active generation protected; staging age 3600 seconds; tests and hosted readiness passed |
| Three healthy projection cycles | generations 5484, 5486, 5487 on exact final SHA |
| Fail-closed restart and recovery | 503 recovering, brief 502 restart, then exact-SHA live/accepted 200 |
| Disk/freshness | healthy in all three samples; values above |
| New lifecycle stimulus and BFF readback | run 29967962811: generation 5516/checkpoint 720174; 8-event chain; loop/journey/evidence 200; 8/8 correlation |
| Final governed deploy conclusion | run 29967962811 succeeded at `2026-07-23T00:21:20Z` |

## Residual risks

- Syntactically valid controller JSON with corrupted numeric fields can still
  raise `ValueError` and yield HTTP 500 instead of a structured degraded
  readiness payload. Atomic projector writes only emit controlled integers;
  this remains separate hardening.
- Shared lease verification uses the GitHub API rate pool. Worker-side
  monitoring must remain low frequency so observation cannot consume the rate
  budget required by the gate.
- Hosted BFF surface convergence is deliberately bounded to four attempts at
  five-second intervals and remains fail closed with redacted diagnostics.
- One pre-acceptance stimulus attempt was consumed without producing its exact
  target position snapshot before the 180-second timeout. The identical
  exact-SHA rerun passed end to end; the failed artifact remains linked above
  so recurrence can be measured rather than hidden.

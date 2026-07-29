# L12-MANIFEST-HC-IMIT-CAP-20260729 — Worker heartbeat closure

Owner: `Codex`  
Reviewer: `Claude2`  
Parent integration owner: `L12-MANIFEST-001`  
Evidence manifest: `evidence.json`

## Outcome

This workstream closes the manifest health gap for:

- `policy-learning-shadow-eval-scheduler`
- `paper-signal-producer`

Both scheduler-style workers now atomically publish a JSON heartbeat after
every tick. Their Compose healthchecks fail closed until a successful tick has
been recorded, reject stale heartbeat files, and reject the wrong worker
identity.

The policy-learning probe additionally requires
`production_training=fail_closed`. The paper signal probe requires all three
capital-safety markers:

```text
execution_mode=paper
live_capital_enabled=false
live_order_submission_enabled=false
```

`paper-signal-producer` also refuses to start if either
`PANTHEON_LIVE_BROKER_ENABLED` or `PANTHEON_CANARY_EXECUTION_ENABLED` is true.
Compose supplies literal `false` values for both flags on this service. No live
broker or capital authority was added.

## Operational contract

| Worker | Heartbeat file | Tick interval | Freshness ceiling | Compose probe |
| --- | --- | ---: | ---: | --- |
| `policy-learning-shadow-eval-scheduler` | `/tmp/policy-learning-shadow-eval-scheduler-health.json` | 3600 s default | `max(60, interval × 3)` | `python services/policy-learning/scheduler_worker.py healthcheck` |
| `paper-signal-producer` | `/tmp/paper-signal-producer-health.json` | 60 s default | `max(60, interval × 3)` | `python -m services.execution.lean_runtime.paper_signal_producer healthcheck` |

The files are container-local operational state, not durable business
authority. Losing a container also loses its heartbeat, which intentionally
returns the replacement container to `starting` until it completes a real
tick.

## Verification

Focused worker, Compose, and topology tests:

```text
27 passed, 1 warning
```

Broader imitation, signal, runtime bootstrap, algorithm safety, and generic
heartbeat-contract regression:

```text
30 passed, 4 skipped, 1 warning
```

The four skips are existing optional real-Postgres / LEAN availability gates.
`docker compose ... config --format json`, the two rendered probe assertions,
`py_compile`, and `git diff --check` also passed. Both task images built, and
container-local positive health probes passed; the paper image completed one
real zero-binding tick before its probe was evaluated.

## Baseline-only failure kept out of the claim

`services/execution/lean_runtime/test_devloop_wire.py` is not counted as task
proof. Its two lifecycle assertions fail on unmodified `origin/dev`
`d9cbbbfa2b0d4076f939a6d0fcc921406993d7af` because the test defaults its
lifecycle outbox to unwritable `/data/runtime/lifecycle-outbox`; two unrelated
signal-isolation assertions pass. The task branch reproduced the same `2
failed, 2 passed` result. This workstream does not alter the lifecycle outbox or
claim that pre-existing test gap is closed.

## Composition boundary

This packet is source and Compose proof for the `L12-MANIFEST-001` owner. It
does not claim:

- the new images are hosted on the replacement dev VM;
- the parent manifest has completed restart/readback proof;
- all other loop workers have healthchecks; or
- a live-capital or canary path is enabled.

The parent manifest and runtime verifier must consume the merged task commit,
render the accepted Compose manifest, and capture hosted container health
readback separately.

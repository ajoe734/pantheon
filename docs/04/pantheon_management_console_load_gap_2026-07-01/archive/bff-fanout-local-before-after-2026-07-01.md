# MGMT-LOAD-005 - Local BFF Fanout Before/After Reproduction

Date: 2026-07-01
Environment: local FastAPI `TestClient` on a persistent event-loop portal
(same shape as a real long-lived uvicorn worker), synthetic 400 ms sleep
injected into the evidence/alerts/approvals/jobs backend read functions,
5 concurrent rounds fanning out `/health`, `/bff/management/evidence`,
`/bff/alerts`, `/bff/approvals`, `/bff/jobs` together (mirrors the
`MGMT-LOAD-001` hosted fanout probe shape).
Raw data: `bff-fanout-local-before-after-2026-07-01.json`

This is a local reproduction, not a hosted dev-BFF run. It isolates the one
variable MGMT-LOAD-005 changes (synchronous read offload + bounded wait vs.
inline synchronous read) with everything else held constant, which the
hosted probe cannot do because the hosted probe cannot be re-pointed at a
pre-fix build on demand. See "Hosted dev BFF re-run" below for why the
`MGMT-LOAD-001` hosted probe itself was not re-run against
`https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io` as part of this task.

## Before (inline synchronous read - pre-fix shape)

`_run_management_read` monkeypatched back to calling the read function
inline on the event loop, reproducing the behavior `main.py` had before this
task (evidence/alerts/approvals/jobs executed their synchronous read-store
work directly on the coroutine instead of `asyncio.to_thread`).

| Route | Count | Min ms | Max ms | p95 ms |
|---|---:|---:|---:|---:|
| /health | 5 | 4 | 1629 | 1629 |
| /bff/management/evidence | 5 | 1623 | 1795 | 1795 |
| /bff/alerts | 5 | 1621 | 1793 | 1793 |
| /bff/approvals | 5 | 1621 | 1793 | 1793 |
| /bff/jobs | 5 | 1619 | 1779 | 1779 |

`/health` queues behind the other four synchronous reads because they all
share the same single-threaded event loop; this reproduces the same
qualitative shape as the `MGMT-LOAD-001` hosted baseline
(`bff-fanout-baseline-2026-07-01.md`: `/health` p95 1328 ms while fanned out
with the same four routes).

## After (current main.py - MGMT-LOAD-005 fix)

Same synthetic 400 ms slow reads, current `_run_management_read`
(`asyncio.to_thread` + `asyncio.wait` bounded by
`PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS`, default 0.6 s).

| Route | Count | Min ms | Max ms | p95 ms |
|---|---:|---:|---:|---:|
| /health | 5 | 4 | 189 | 189 |
| /bff/management/evidence | 5 | 411 | 425 | 425 |
| /bff/alerts | 5 | 410 | 587 | 587 |
| /bff/approvals | 5 | 413 | 588 | 588 |
| /bff/jobs | 5 | 412 | 591 | 591 |

`/health` p95 189 ms meets the `<= 200 ms` acceptance bound while the other
four routes are concurrently in flight; Evidence/alerts/approvals/jobs stay
near the injected 400 ms read time (plus thread-scheduling overhead) instead
of queuing to 1.6-1.8 s, and all stay under the 750 ms Evidence bound.

## Reproduce

```sh
python3 -m pytest services/control-plane/bff/test_mgmt_load_005_read_concurrency.py -q
# 7 passed
```

The pytest file covers the same before/after distinction with `assert`
bounds instead of hand-recorded numbers (`test_health_stays_fast_while_*`,
`test_*_timeout_returns_degraded_envelope_without_hanging`,
`test_evidence_returns_normal_payload_when_fast`); it is the durable,
CI-checked form of this evidence. The before/after script above is an
ephemeral local repro used only to produce human-readable before/after
numbers and was not committed to the repo.

## Hosted dev BFF re-run

`https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io` currently serves the
`dev` branch, which does not include this fix until `task/MGMT-LOAD-005`
merges. Re-running the `MGMT-LOAD-001` fanout probe against it now would only
reproduce the known pre-fix baseline, not validate this change, and this
worker had no `PANTHEON_BFF_ACCESS_TOKEN` / dev BFF credential configured
(same gap `MGMT-LOAD-002` recorded). Per `INDEX.md`, `MGMT-LOAD-007` closes
`MGMT-GAP-010` with "merged PR, deployed FE/BFF, hosted probe, and
residual-risk evidence" - the hosted `MGMT-LOAD-001` fanout probe re-run
against the post-merge dev BFF belongs there, not in this task. This
before/after reproduction plus the CI-checked contract tests are the
evidence accepted through the `MGMT-LOAD-001`/`MGMT-LOAD-005` probe path
referenced by the `MGMT-LOAD-002` review.

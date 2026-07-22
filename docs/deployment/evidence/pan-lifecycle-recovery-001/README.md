# PAN-LIFECYCLE-RECOVERY-001 delivery evidence

Status: implementation validated; hosted deployment and acceptance pending

Environment: replacement Pantheon dev VM only (`pantheon-lupin-dev`, project
`pantheon-lupin-dev-20260719`). No production or live-capital operation is in
scope.

## Pre-repair hosted truth

Public readback at `2026-07-22T17:32:34Z` showed:

- `GET https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io/readyz` returned
  `200` with only `runtime_manager`, `governance`, and `deployment`
  dependencies. It did not expose the lifecycle projector.
- `GET /bff/readyz` returned a static `status: ok` document with no dependency
  or freshness information.
- `GET /bff/version` identified the hosted BFF as
  `6d1aaddc7abc6a2601de8add908b20c5d2688eda`, strict auth, auth stub disabled,
  MFA required, assistant kernel enabled.

The task packet records the stopped projector at generation 5036, last current
advance `2026-07-21T11:58:00Z`, and the prior `ENOSPC` error. A direct VM
read-only probe was attempted before cleanup:

- `gcloud compute instances list` could not refresh the local credential in a
  non-interactive worker (`gcloud auth login` required).
- batch SSH to `lupin@35.201.204.12` was rejected by public-key authentication.

No live generation, `.tmp` directory, `current` symlink, lifecycle payload, or
container was changed by those failed probes. Therefore no manual live cleanup
or restart is claimed. The repository repair logs an active-generation-aware
cleanup plan before it removes any recognized debris during the governed dev
deployment.

## Delivered safety behavior

- Keep at most 32 recognized completed generations by default.
- Always preserve the generation referenced by `current` until a replacement
  bundle has been fully written and atomically switched.
- Clean only projector-owned staging names older than 3600 seconds; unknown
  directories and recent staging remain untouched.
- Reserve one retention slot before a publish so repeated failed publishes
  remain bounded.
- Keep the worker process alive when both the main publish and durable error
  publication encounter `ENOSPC`; retry from the unchanged checkpoint.
- Avoid publishing a new generation for an identical repeated source error.
- Fail root and `/bff/readyz` closed on stopped/stale/error/non-live projector
  state, active/controller generation mismatch, backlog policy breach, or low
  disk.
- Expose worker/controller status, current and controller generations,
  checkpoint, source high watermark, backlog, last poll, last successful
  publish, deployment SHA, disk, freshness, retention policy, and reasons.

Default thresholds:

| Setting | Default |
|---|---:|
| Generation retention | 32 |
| Abandoned staging age | 3600 seconds |
| Maximum last-poll age | 30 seconds |
| Maximum backlog | 5000 lifecycle rows |
| Minimum free bytes | 134217728 |
| Minimum free percent | 5% |

## Local verification

Anchor commit: `1d984e3e9`

Passed:

```text
python3 -m py_compile \
  services/trade_journey/lifecycle_projector.py \
  services/control-plane/bff/main.py \
  services/control-plane/bff/test_lifecycle_projector_readiness.py

/tmp/pan-lifecycle-recovery-001-venv/bin/python -m pytest -q \
  services/trade_journey/test_lifecycle_projector.py \
  services/trade_journey/test_lifecycle_projector_compose.py \
  services/control-plane/bff/test_lifecycle_projector_readiness.py
# 21 passed

docker compose -f docker-compose.yml config --quiet
```

Adjacent lifecycle/BFF/health regression selection completed with 35 passed
and 2 skipped. Its single failure was the pre-existing missing Agora
ask/inbox-route assertion. The identical assertion also fails on the untouched
`52d9ed234af280fc239459bfeddf76886ae35f08` baseline with the same three routes,
so it is not attributed to this task.

## Hosted acceptance ledger

These fields must be completed from immutable GitHub and hosted artifacts
before the task can close:

| Evidence | Result |
|---|---|
| PR and reviewer approval | pending |
| Merge commit on `dev` | pending |
| Governed nonprod deploy run | pending |
| Deployed BFF/projector SHA | pending |
| Cleanup-plan log and retained active generation | pending |
| Three consecutive fresh readiness samples | pending |
| New lifecycle stimulus advances `current` and is readable | pending |
| Scheduler restart preserves accepted generation and resumes | pending |
| Disk/freshness readback | pending |

## Residual risk before hosted closeout

The first successful publish after deployment can remove many old,
non-active generations. The cleanup is intentionally limited to strict
projector-generated names, logs the classified plan first, and never removes
the `current` target before the new atomic switch. Hosted closeout still needs
the workflow artifact and cleanup log to prove those guards against the actual
dev volume.

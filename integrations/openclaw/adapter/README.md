# openclaw-gateway-adapter Boundary

Last updated: 2026-04-16
Owner: BP5-OSS-002 (Codex)
Reviewer: Claude
Status: live gateway adapter implemented and smoke-tested

This directory is the only approved home for Pantheon-side OpenClaw adapter code.

## Scope

Allowed responsibilities:

- map Pantheon persona and capability state into OpenClaw runtime calls
- manage session create / resume / terminate flows
- capture raw upstream outputs for governed normalization
- normalize those outputs into canonical `StrategySpec` and `WorkflowHandoff`
- emit Pantheon-governed telemetry and error envelopes

## Non-Goals

The adapter must not:

- vendor or rewrite the OpenClaw source tree
- claim that Pantheon's future `/control/*` facade is a native upstream API
- write directly into registry, governance, telemetry, or LEAN without going through Pantheon-owned services
- bypass Pantheon permission filtering or secret isolation

## Locked Inputs

The adapter may assume only these locked upstream inputs from `BP5-OSS-001`:

- Git tag `v2026.6.8`
- Commit `8c802aa683510c7f7503597b54c3021733245e59`
- Container image `ghcr.io/openclaw/openclaw:2026.6.8`
- Verified command surface:
  - `openclaw --help`
  - `openclaw gateway --help`
  - documented gateway health endpoints `/healthz` and `/readyz` once configured

Anything beyond that must be re-verified during `BP5-OSS-002`.

## Runtime Files

The runnable Pantheon-side adapter now lives here:

- `gateway_runtime.py`
  - manages the pinned upstream Docker runtime
  - probes `/healthz`
  - executes `gateway health`, `gateway status`, and `gateway call ...`
- `cron_transport.py`
  - maps Pantheon dispatch envelopes into upstream `cron.add`, `cron.run`, and
    `cron.runs`
  - emits deterministic `systemEvent` summaries to prove the live substrate
    without claiming native Pantheon workflow semantics upstream

## Smoke Path

Executable proof for the live adapter path:

```bash
bash scripts/openclaw-gateway-adapter-smoke.sh
```

What it proves:

- a pinned upstream gateway container can be started from Pantheon
- Pantheon can probe runtime health over `/healthz` plus gateway RPC health
- the adapter can schedule, force-run, and observe real upstream cron jobs
- the governed cron wrapper still emits Pantheon-local handoffs and deployment
  projections after the upstream execution succeeds

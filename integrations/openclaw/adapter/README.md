# openclaw-gateway-adapter Boundary

Last updated: 2026-04-15
Owner: BP5-OSS-001 (Codex)
Reviewer: Claude
Status: boundary locked for BP5-OSS-002

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

- Git tag `v2026.4.7`
- Commit `5050017543011b61df67744ebc6368d889c25a95`
- Container image `ghcr.io/openclaw/openclaw:2026.4.7`
- Verified command surface:
  - `openclaw --help`
  - `openclaw gateway --help`
  - documented gateway health endpoints `/healthz` and `/readyz` once configured

Anything beyond that must be re-verified during `BP5-OSS-002`.

# PFG-MGMT-AI-PROVIDER-20260820 evidence

This task makes Management NL accept only a successful, non-empty provider
answer as provider success. A configured URL, gateway health response, or an
outer adapter HTTP 200 no longer qualifies.

`evidence.json` records the single BFF orchestration path, the configured
fallback contract, bounded-answer readiness semantics, durable conversation
replay coverage, and the exact local validation. It is the task-scoped review
manifest for `Antigravity2`; it records owner evidence only and does not claim
review approval or hosted deployment acceptance.

## Operational configuration

- `PANTHEON_MANAGEMENT_NL_PROVIDER_DEADLINE_SECONDS` bounds one complete
  primary-plus-fallback provider attempt sequence (default: 45 seconds).
- `PANTHEON_MANAGEMENT_NL_ASSISTANT_FALLBACK_PROVIDERS` is an explicit,
  comma-separated list of already-configured fallback providers. If none yields
  a completed non-empty answer before the deadline, Management NL returns the
  existing typed degraded status plus deterministic synthesis.
- `OPENCLAW_ASSISTANT_READINESS_TIMEOUT_SECONDS` bounds an OpenClaw answer
  probe (default: 20 seconds). `ready=true` follows a completed non-empty CLI
  answer; an inventory read returns `not_checked` instead of a false-ready
  gateway signal.

No development-tooling, repository-write, live-capital, or UI-action behavior
is enabled by this task.

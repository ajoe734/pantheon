# OpenClaw Integration — Governance Mapping

Last updated: 2026-06-16
Owner: BP5-OSS-001 (Codex)
Reviewer: Claude
Status: governed baseline locked — bumped to 2026.6.6
Related: `OPENCLAW_RUNTIME_CONTRACT.md`, `OC-001`, `OC-002`, `OC-003`

## 1. Purpose

This document defines how the pinned OpenClaw runtime baseline is governed by Pantheon's internal policy framework.

Pinned baseline:

- Git tag: `v2026.6.6`
- Commit: `8c802aa683510c7f7503597b54c3021733245e59`
- Runtime image: `ghcr.io/openclaw/openclaw:2026.6.6`

OpenClaw remains an **external runtime substrate**. Pantheon never delegates governance authority to it.

## 2. Governance Principle

> **OpenClaw provides runtime capability. Pantheon provides governance authority.**

Every action that flows through OpenClaw must first pass through Pantheon's:

- permission model (`OC-001`)
- workflow orchestration (`OC-002`)
- `StrategySpec` normalization (`OC-003`)
- promotion gate (`REG-002`)

## 3. Permission Governance (OC-001 Alignment)

### 3.1 Deny-First Model

OpenClaw's native tool / skill resolution is wrapped by Pantheon's deny-first permission contract:

```text
Pantheon request
  -> Router (P4-001)
  -> Permission evaluator (OC-001)
  -> If denied: reject before the adapter calls OpenClaw
  -> If allowed: forward to openclaw-gateway-adapter
  -> OpenClaw runtime executes only within that filtered capability set
```

### 3.2 Mandatory Deny Rules

The following are always denied regardless of any upstream capability:

| Rule | Rationale |
|---|---|
| direct LEAN calls from OpenClaw tools | live execution must remain `registry -> signal -> LEAN`, never `OpenClaw -> LEAN` |
| paper/backtest-only tools in live context | promotion state must be checked before execution surfaces change |
| cross-persona tool invocation without consult routing | persona isolation boundary |
| unaudited tool calls | all invocations must emit telemetry events |
| secret / credential access outside persona auth profile | no implicit credential sharing |
| file system access outside the persona workspace | workspace isolation requirement |

### 3.3 Approval Hooks

Tools with `effect: "allow_with_approval"` require:

1. human or policy approval
2. an audit record in the feedback / approval store
3. a session-scoped approval token rather than a global runtime grant

## 4. Workflow Governance (OC-002 Alignment)

### 4.1 Baseline Rule

During `BP5-OSS-001`, workflow governance is locked conceptually but not yet implemented end-to-end against a live adapter.

That means:

- fixture-driven normalization is allowed for smoke validation
- real runtime-triggered workflow execution is deferred to `BP5-OSS-002`

### 4.2 Cron / Workflow Mapping

| Pantheon workflow | OpenClaw mechanism | Governance constraint |
|---|---|---|
| `ingest` | research task / agent turn | output must normalize into a governed handoff |
| `review` | review / audit session | output must become an `approval_request` candidate |
| `retrain` | trainer session | output must become a registry submission candidate |
| `deploy` | adapter-mediated workflow trigger | must pass through promotion gates before runtime effects |

### 4.3 Workflow Input / Output Contract

Every governed workflow path must:

1. receive a Pantheon-governed input payload
2. produce an output that validates against Pantheon schema
3. emit audit events for started / completed / failed lifecycle stages

## 5. StrategySpec Normalization (OC-003 Alignment)

### 5.1 Normalization Pipeline

```text
OpenClaw runtime output or smoke fixture
  -> openclaw-gateway-adapter captures raw payload
  -> normalize into StrategySpec + WorkflowHandoff
  -> validate against canonical schemas
  -> attach governance metadata
  -> submit downstream to registry / approval paths
```

### 5.2 Governance Metadata Attachment

Every normalization produces two canonical artifacts:

1. `StrategySpec`
2. `WorkflowHandoff`

`StrategySpec` carries only its own `governance` and `provenance`.

`WorkflowHandoff` carries:

- `registry_hints`
- `governance_context`
- handoff-level `provenance`

Those fields stay on the handoff envelope rather than leaking into the spec itself.

## 6. Error Governance

### 6.1 Error Escalation Ladder

| Error layer | Pantheon response |
|---|---|
| known typed errors | map to the internal error domain, retry only when safe |
| transport / system errors | backoff, alert, and trip circuit breakers if persistent |
| `unknown_upstream_error` | isolate, degrade, escalate |

### 6.2 Unknown Error Governance Chain

When OpenClaw produces an `unknown_upstream_error`:

1. record the full envelope in telemetry
2. mark the affected session as degraded
3. trip the circuit breaker when the threshold is reached
4. open an incident if the pattern persists
5. never let that failure path directly affect LEAN live execution

### 6.3 Kill-Switch Independence

> **The kill-switch fast path must never depend on OpenClaw availability.**

Kill-switch flows through `runtime-manager` directly and bypasses OpenClaw entirely.

## 7. Audit and Telemetry

All events listed in `OPENCLAW_RUNTIME_CONTRACT.md` section 11 must be:

- emitted by the adapter
- written to Pantheon's telemetry / audit store
- queryable by `persona_id`, `session_id`, and time range
- retained under Pantheon's retention policy

OpenClaw internal diagnostics remain upstream diagnostics. Pantheon only treats the adapter-emitted governance events as canonical telemetry.

## 8. Upgrade Governance

When the pinned OpenClaw version changes:

1. re-run `scripts/openclaw-smoke-test.sh`
2. confirm the deny rules still dominate any new upstream capability
3. confirm canonical normalization still validates against current schemas
4. obtain reviewer approval before changing the governed pin
5. record the change in `ai-activity-log.jsonl`

## 9. Pin Bump Record

### 2026-06-16: `v2026.4.7` → `v2026.6.6` (OPENCLAW-GOVERNED-BUMP-2026-6-6)

**Reason for bump:** `v2026.4.7` only supports a localhost-callback paste-back OAuth flow for OpenAI/Codex accounts, which is unusable on headless VMs. `v2026.6.6` adds `openclaw models auth login --provider openai --device-code` (ChatGPT device-code flow), enabling headless subscription-account binding with zero API keys.

**Auth mode change:** Subscription OAuth via device-code flow (`openai/oauth`); no `OPENAI_API_KEY` required or used. The `openclaw-gateway` service in `docker-compose.yml` carries no `OPENAI_API_KEY` env entry (the field is absent — not merely blank).

**Model ref change:** `v2026.6.6` uses `openai/gpt-5.5` with `plugins.entries.codex.enabled=true`; the legacy `openai-codex/*` namespace is deprecated upstream. `openclaw doctor --fix` migrates existing config automatically.

**`~/.codex` removal:** The `v2026.4.7` onboarding attempted to import `~/.codex` on container startup. `v2026.6.6` removes this import; the env vars `PANTHEON_ASSISTANT_CODEX_HOST_HOME` / `PANTHEON_ASSISTANT_CODEX_CONTAINER_HOME` in `docker-compose.yml` are preserved for backwards-compat volume mounts but are no longer read by OpenClaw itself.

**Smoke gates:** `bash scripts/openclaw-smoke-test.sh` and `bash scripts/openclaw-gateway-adapter-smoke.sh` rerun against `ghcr.io/openclaw/openclaw:2026.6.6`; results recorded in `integrations/openclaw/evidence_pack.md §7`.

**Deny rules:** All deny rules in §3.2 remain unchanged; the bump does not relax any capability boundary.

### 2026-06-16: Pantheon-derived gateway image + Claude CLI (multi-model persona routing)

**What:** The `openclaw-gateway` runtime artifact is no longer the bare upstream image; it is a **Pantheon-derived image** built from `integrations/openclaw/gateway/Dockerfile` = `FROM ghcr.io/openclaw/openclaw:2026.6.6` + the Claude Code CLI (`@anthropic-ai/claude-code`, version-aligned with the adapter Dockerfile) layered on top. The governed **upstream pin (tag `v2026.6.6` / commit `8c802aa683510c7f7503597b54c3021733245e59`) is unchanged** — this only layers a CLI on top.

**Reason:** OpenClaw's `claude-cli` agent runtime spawns `claude -p` **inside the gateway container**, so the `claude` binary must live there (the adapter image carrying claude does not help the gateway). Baking it in makes Claude CLI reuse stable across container recreate; ephemeral `npm i -g` in the running container is wiped on every full redeploy. This enables **multi-model persona routing** — e.g. debate personas split across `openai/gpt-5.5` (Codex OAuth) and `anthropic/claude-*` (Claude CLI), each on subscription auth with zero API keys.

**Auth:** Claude side uses Claude CLI subscription login (`claude auth login`, one-time interactive), persisted on the `openclaw-data` volume via `CLAUDE_CONFIG_DIR=/home/node/.openclaw/claude-cli`. No `ANTHROPIC_API_KEY`. **Billing caveat:** per Anthropic policy effective 2026-06-15, subscription `claude -p` usage draws from the account's monthly Agent SDK credit. Anthropic can change Claude Code billing/rate-limits without an OpenClaw release; for shared production automation an Anthropic API key is the predictable path.

**Capability boundary:** unchanged. Layering a CLI does not relax any deny rule; the claude-cli runtime is subject to the same OC-001 deny-first filtering and OC-002 job governance as any model route.

### 2026-06-17: OpenClaw CLI baked into the gateway-adapter image (assistant `openclaw` provider)

**What:** `services/openclaw-gateway-adapter/Dockerfile` now multi-stage-copies the OpenClaw CLI runtime (`/app` + its Node 24) from `FROM ghcr.io/openclaw/openclaw:2026.6.6 AS openclaw_cli` into the adapter image, exposing an `openclaw` wrapper on PATH. The governed **upstream pin (tag `v2026.6.6` / commit `8c802aa683510c7f7503597b54c3021733245e59`) is unchanged**; this reuses the same pin the gateway image already carries.

**Reason:** the assistant `openclaw` provider (`assistant_openclaw_provider.py`, OPENCLAW-AGENT-TURN-LIVE-FIX) shells out to `openclaw agent --url ws://openclaw-gateway:18789 --token … --agent main` as a remote WS-RPC client, but the adapter image had no `openclaw` binary — so every Management-AI turn degraded with `OPENCLAW_BINARY_NOT_FOUND` while unit tests (which mock the CLI) and the skip-when-absent pytest live smoke stayed green. OpenClaw is not on npm, so it cannot be `npm i -g`'d like codex/claude; it must be copied from the governed image.

**Lockstep requirement:** the adapter Dockerfile `FROM` tag MUST be bumped together with `integrations/openclaw/gateway/Dockerfile` and the pin in `OSS_INTEGRATION_CHECKLIST.md` / this file's §1. The Node 24 runtime is isolated under `/opt/openclaw` so it does not shadow the apt Node 20 used by the codex/claude CLIs.

**Gate added:** `scripts/openclaw-assistant-openclaw-live-smoke.sh` drives a real agent turn against a deployed adapter and FAILS (non-skip) on degradation — the live evidence layer the prior change lacked. `compose` also now passes `OPENCLAW_GATEWAY_TOKEN` / `OPENCLAW_AGENT_ID` to the adapter (previously absent, a second latent degrade-cause).

**Capability boundary:** unchanged. The adapter CLI is a remote client of the gateway; model auth stays on the gateway side, and all turns remain subject to OC-001 deny-first filtering.

## 10. Relationship to Other Governance Documents

| Document | Relationship |
|---|---|
| `OPENCLAW_RUNTIME_CONTRACT.md` | defines the runtime boundary; this file adds the governance overlay |
| `OC-001` | deny-first capability filtering |
| `OC-002` | workflow ownership and job governance |
| `OC-003` | canonical `StrategySpec` and `WorkflowHandoff` boundary |
| `REG-002` | promotion gate for any OpenClaw-originating artifact |
| `PAPER_CANARY_LIVE_POLICY.md` | OpenClaw has no authority to mutate deployment stage directly |

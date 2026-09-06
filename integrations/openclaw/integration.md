# OpenClaw Integration — Pin and Adapter Boundary

Last updated: 2026-07-21
Owner: BP5-OSS-002 (Codex)
Reviewer: Claude
Status: governed runtime adapter realized
Upstream repo: https://github.com/openclaw/openclaw
Canonical runtime contract: `OPENCLAW_RUNTIME_CONTRACT.md`

## 1. Locked Upstream Pin

| Field | Value |
|---|---|
| Repository | `https://github.com/openclaw/openclaw` |
| Selected stable tag | `v2026.7.1` |
| Selected commit | `2d2ddc43d0dcf71f31283d780f9fe9ff4cc04fe4` |
| Release date | `2026-07-13` |
| npm package | `openclaw@2026.7.1` |
| Container image | `ghcr.io/openclaw/openclaw:2026.7.1` |
| Container digest | `sha256:6a31d44b2944e7adcd2b582bf6fb463111264ebca97a0201795b799135bd102c` (multi-arch index) |
| Website | `https://openclaw.ai` |
| Docs | `https://docs.openclaw.ai` |

## 2. Why This Pin Was Bumped to `v2026.7.1`

**Current bump (2026-07-21):** `v2026.7.1` is the latest stable release and
had completed Pantheon's 48-hour soak requirement. It keeps the OpenAI-compatible
Gateway endpoints used by Management AI while adding the official native Codex
app-server runtime, named multi-login OpenAI auth profiles, auth-profile
rotation on Codex usage limits, canonical Google/Gemini CLI routing, stronger
provider diagnostics, and more reliable CLI-session/fallback behavior. Pantheon
also installs the Gemini CLI in its derived gateway image and registers the
five-route shared model pool idempotently after each root deploy.

**History:** The baseline was originally locked at `v2026.4.7` for `BP5-OSS-001` (2026-04-15) because:

- `v2026.4.14` had not satisfied the 48-hour soak policy at that time
- `v2026.4.15-beta.1` was a prerelease and ineligible for the governed baseline

**Prior bump (2026-06-16, OPENCLAW-GOVERNED-BUMP-2026-6-6):**

- `v2026.4.7` only provides a localhost-callback paste-back OAuth flow for OpenAI/Codex accounts, which is unusable on headless VMs
- `v2026.6.8` adds `openclaw models auth login --provider openai --device-code` (ChatGPT device-code flow) — the correct headless path for subscription-account binding with zero API keys
- auth mode is subscription OAuth (`openai/oauth`); no `OPENAI_API_KEY` required
- dev environment already validated: `openai:lupinchen@cctech-support.com`, agent turn confirmed
- model refs now include `openai/gpt-5.6-sol` and `openai/gpt-5.5` with `plugins.entries.codex.enabled=true`; `openclaw doctor --fix` migrates legacy config
- multi-LLM personas also route to `anthropic/claude-opus-4-8` via Claude CLI subscription (the derived gateway image bakes the Claude CLI). For how personas reference the shared model pool, see [`model-pool-and-persona-routing.md`](./model-pool-and-persona-routing.md)

Upgrade rule (for future bumps):

1. wait at least 48 hours after a stable upstream release is published
2. re-run `scripts/openclaw-smoke-test.sh`
3. update this file, `governance.md`, `evidence_pack.md`, and `OSS_INTEGRATION_CHECKLIST.md`
4. record the pin change in `ai-activity-log.jsonl`

## 3. Integration Mode

Pantheon integrates OpenClaw as an **external runtime dependency**, not as vendored source and not as a local rewrite.

Accepted mode:

- upstream GitHub source remains upstream-owned
- Pantheon may consume the published package / container artifacts
- Pantheon implements one governed boundary: `openclaw-gateway-adapter`

Rejected modes:

- vendoring or submodule-copying the OpenClaw source tree into Pantheon
- re-implementing OpenClaw runtime behavior inside LEAN or Pantheon services
- treating OpenClaw as the owner of registry, promotion, execution, or telemetry truth

## 4. Verified Upstream Runtime Surface

`BP5-OSS-001` only locks surfaces that were actually verified against the pinned upstream artifacts.

Verified upstream surface:

- the Git tag `v2026.7.1` resolves to commit `2d2ddc43d0dcf71f31283d780f9fe9ff4cc04fe4`
- the GHCR image `ghcr.io/openclaw/openclaw:2026.7.1` is published and pullable
- the container can execute `openclaw --help`
- the container can execute `openclaw gateway --help`
- the upstream Docker docs define a gateway process with HTTP health endpoints `/healthz` and `/readyz` once a configured gateway is running

Not verified here, and therefore **not** part of the locked upstream promise:

- any upstream `/control/*` REST API
- any direct upstream notion of Pantheon `StrategySpec` or `WorkflowHandoff`
- any direct upstream write path into Pantheon registry, governance, telemetry, or LEAN

## 5. Governed Adapter Boundary

The governed seam is the Pantheon-side `openclaw-gateway-adapter`. The implementation home is reserved under `integrations/openclaw/adapter/`.

Adapter responsibilities:

| Responsibility | Direction | Notes |
|---|---|---|
| agent provisioning | Pantheon -> OpenClaw | map persona and capability snapshots into upstream agent/runtime concepts |
| session lifecycle | Pantheon -> OpenClaw | create, resume, terminate, and inspect runtime sessions |
| tool / skill mapping | Pantheon -> OpenClaw | filter through Pantheon RBAC before any upstream resolution |
| consultation routing | Pantheon -> OpenClaw | bridge consult requests and sub-agent orchestration |
| workflow handoff capture | OpenClaw -> Pantheon | capture raw upstream output for governed normalization |
| normalization | Pantheon | emit canonical `StrategySpec` + `WorkflowHandoff` |
| error governance | Pantheon | classify upstream failures into known / transport / unknown buckets |

Boundary invariants:

- adapter code lives on the Pantheon side only
- adapter may expose Pantheon-internal facade endpoints later, but those endpoints are **not** treated as native OpenClaw API
- adapter is the only allowed place to map OpenClaw runtime objects to Pantheon domain objects
- OpenClaw never receives authority over registry state, approval state, capital pools, runtime bindings, or LEAN deployment

## 6. Pantheon Adapter Facade

The following facade is a **future Pantheon internal surface**, derived from `OPENCLAW_RUNTIME_CONTRACT.md`. It is not claimed to be a native upstream OpenClaw endpoint set.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/control/personas/{persona_id}/sessions` | create a governed runtime session for one persona |
| `POST` | `/control/sessions/{session_id}/invoke` | invoke one governed session action |
| `POST` | `/control/consult/spawn` | spawn a governed consultation session |
| `GET` | `/control/sessions/{session_id}` | read Pantheon-side session state |
| `GET` | `/control/personas/{persona_id}/capabilities` | resolve Pantheon-filtered capabilities |
| `POST` | `/control/jobs` | schedule a Pantheon-governed workflow or cron job |
| `GET` | `/control/jobs/{job_id}` | read Pantheon job status |

## 7. Smoke Baseline for BP5-OSS-001

The smoke baseline is intentionally narrower than `BP5-OSS-002`.

`BP5-OSS-001` proves:

1. the selected Git tag and container artifact both exist
2. the pinned container exposes the expected CLI / gateway command surface
3. a raw upstream-style handoff fixture can be normalized into canonical Pantheon objects with the repo's current schemas

The executable entrypoint is:

```bash
bash scripts/openclaw-smoke-test.sh
```

Supporting fixture:

- `integrations/openclaw/fixtures/raw_research_handoff.minimal.json`

Normalization script:

- `services/control-plane/specs/normalize_handoff.py`

## 8. Out of Scope for BP5-OSS-001

The following are explicitly deferred to `BP5-OSS-002`:

- deciding the final transport between Pantheon and the OpenClaw gateway
- bootstrapping a configured OpenClaw gateway with Pantheon-specific auth/runtime settings
- invoking a real workflow through a live adapter path
- proving end-to-end job execution and raw output capture from a configured runtime

That separation is intentional: this task locks the upstream source and the governed seam first, so the next task can implement against a stable target instead of undocumented assumptions.

## 9. BP5-OSS-002 Realization

`BP5-OSS-002` completes the next step that `BP5-OSS-001` explicitly deferred.

Implemented now:

- Pantheon-side runtime control in `integrations/openclaw/adapter/gateway_runtime.py`
- Pantheon-side cron transport in `integrations/openclaw/adapter/cron_transport.py`
- a compose-visible runtime dependency path under `docker-compose.yml` service
  `openclaw-gateway` (profile: `openclaw`)
- executable live smoke at `scripts/openclaw-gateway-adapter-smoke.sh`

The live smoke path proves:

1. the pinned upstream container can be started as a real gateway dependency
2. Pantheon can call real upstream `cron.add`, `cron.run`, and `cron.runs`
3. the governed wrapper still owns handoff normalization and deployment
   projection locally

Smoke evidence captured on `2026-04-16`:

- command: `bash scripts/openclaw-gateway-adapter-smoke.sh --container-name pantheon-openclaw-gateway-smoke4 --host-port 18795 --gateway-token pantheon-gateway-smoke-token --state-dir /tmp/pantheon-openclaw-gateway-smoke4`
- artifacts: `/tmp/openclaw-bp5-oss-002.fXeSom/smoke-results.json`
- result: `ingest`, `review`, `retrain`, and `deploy` all passed against the
  pinned upstream runtime

## 10. Unified ordinary-turn transport (SIMPLIFY-OPENCLAW-001)

Ordinary `invoke`, `stream`, structured extraction, and readiness answer probes
use the Gateway `POST /v1/responses` builder and terminal normalization in
`assistant_openclaw_provider.py`. Small and large prompts follow the same path;
ordinary turns require no CLI binary. Administrative cron/auth CLI and read-only
kernel delegation retain their existing owners.

The request selects `openclaw/<agentId>` and carries an admitted explicit model
in `X-OpenClaw-Model`. A successful explicit override does not change the next
ordinary request's model. Readiness tries only the configured primary model;
it neither retries on another model nor changes future routing. Tenant, actor,
and conversation components are positionally encoded and escaped in the
upstream session key. History, context, attachments, and trace use the same
builder. Socket reads, TLS reads, and SSE consumption share a total deadline.
Only one normalized terminal result is emitted, including interruption,
timeout, refusal, incomplete output, and upstream failures.

`POST /api/openclaw-adapter/assistant/providers/openclaw/structured` accepts a
schema for the fixed data-only `emit_extraction` tool. Arbitrary caller tool
names, tool definitions, tool choice, and unadmitted agents are rejected.
Returned arguments undergo recursive schema validation; wrong/missing calls
and invalid arguments are failures, never domain commands. The pinned Gateway
normally yields a function call in `response.completed` with nested status
`incomplete`; this tool handback differs from incomplete text generation.
The adapter retains the function-call identity and reported usage.

The Gateway agent's own native-tool policy remains a necessary server-side
boundary: configure the extraction agent with `tools.deny: ["*"]` so only the
request's data-emission client tool is offered. A client `tool_choice` or
post-response validation alone cannot prevent native execution. The local
fixture proves the pinned Gateway enforces that policy; it does not attest to
any hosted deployment's configuration.

Cron completion in `services/control-plane/cron/openclaw_client.py` requires
both `jobId` and `runId` to match. The pinned Gateway's exact `{id, runId}`
`cron.runs` lookup runs before pagination. Missing run IDs fail as unknown
without replaying `cron.run`; add, dispatch, RPC polling and sleep consume one
deadline. A late result is never accepted as success. Failed, cancelled, timed
out, and skipped runs are terminal failures.

### Reproduce local Gateway acceptance

Human/Ops clarified on 2026-09-06 that a pinned, reproducible local Gateway and
synthetic model fixture satisfy this task's functional acceptance; real account
credentials are not required. The opt-in runner is embedded in the declared
transport test file:

```bash
SIMPLIFY_REPLAY_OUTPUT=/tmp/simplify-replay-100.json timeout 1200 \
  .venv-pantheon/bin/python3 \
  services/openclaw-gateway-adapter/tests/test_openresponses_transport_contract.py
```

It uses the immutable local image ID recorded in `evidence.json` (built from
`integrations/openclaw/gateway/Dockerfile`, upstream 2026.7.1). Docker must have
that image available. The runner starts and removes its own uniquely named
container, mounts only a temporary synthetic workspace, uses loopback ports
and test-only tokens, and runs a deterministic local OpenAI-completions model.
It never mounts real credentials or changes another container. Ordinary pytest
collection does not start the container.

The runner asserts positive extraction, invalid arguments, wrong/missing tool,
and native `exec` denial against the actual Gateway. It then loads the CLI
provider from the frozen dev SHA, runs 100 prompts per arm, and records session
cold/warm TTFT/full p50/p95, model usage, errors, and transport subprocesses.
Ten agent sessions each receive ten sequential turns; up to four sessions run
concurrently. CLI TTFT means text availability from its buffered invoke API;
HTTP TTFT is the first normalized delta. Usage comes from the synthetic model
and does not measure tokenizer behavior or external model cost. CLI timing
includes Docker exec overhead. These are local transport measurements, not
production model quality, production latency, or hosted deployment proof.

The task-scoped evidence manifest records exact identities, executed checks,
results, and limitations. Earlier corrective-pass records remain in git history.
The separate `integrations/openclaw/adapter/cron_transport.py` production Docker
transport still has a most-recent-run fallback; that file is outside this task's
artifact grant and needs an explicit scope handoff to its owner.

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

## 10. SIMPLIFY-OPENCLAW-001 (2026-09-06)

Ordinary agent turns in `services/openclaw-gateway-adapter/assistant_openclaw_provider.py`
are now unified on a single HTTP request builder against the existing Gateway
`POST /v1/responses` endpoint:

- `invoke()`, `stream()`, and `readiness()`'s answer-probe all go through one
  `_invoke_via_http()` helper. The general-turn CLI subprocess path
  (`_invoke_single_model`) and the 96 KiB argv-size branch are removed
  entirely — transport selection for an ordinary turn no longer depends on
  prompt length, and ordinary turns never spawn the `openclaw` CLI binary.
- Administrative/cron CLI paths (`gateway_cron_call`, `_gateway_call`,
  `gateway_agents_list`, `_openclaw_bin`, `_openclaw_cli_state_env`) and
  `kernel_debug` Codex delegation are unchanged and still use the `openclaw`
  CLI — this cleanup only affects ordinary-turn invoke/stream/readiness.
- `stream()` gained `model`, `agent_id`, `messages`, `tools`, `tool_choice`,
  and `timeout_seconds` parameters, and now also reads a nested
  `response.completed`'s `response` object (`status`, `output[]`, `usage`,
  `id`) for function-call/usage/response-id extraction. **This nested-object
  shape is an assumption based on the OpenAI-Responses-API family and was not
  independently re-verified against a live pinned Gateway** (no live gateway
  reachable in the dev sandbox that implemented this change).
- A restricted, server-approved `emit_extraction` function tool
  (`emit_extraction_tool_schema()` / `invoke_structured()`) was added on the
  same transport: the caller supplies only a JSON-schema `parameters` body,
  never a full tool/tool-list; the tool call is pinned via `tool_choice` and
  never triggers a domain mutation. A new restricted endpoint
  `POST /api/openclaw-adapter/assistant/providers/openclaw/structured` in
  `main.py` exposes this for the (separate, later) SIMPLIFY-EXTRACTION-001
  task to consume.
- Cron exact-run correlation fix in
  `services/control-plane/cron/openclaw_client.py`
  (`_CliGatewayTransport._wait_for_terminal_run`): removed the
  `entries[0]` "most recent run" fallback; a run is only ever reported
  terminal when its `runId` exactly matches the dispatched `run_id`. A
  missing `run_id` from `cron.run` now fails fast instead of polling blindly
  or resubmitting `cron.run`. The `cron.runs` poll window was widened from
  `limit: 5` to `limit: 20` to reduce (not fix on its own) the chance of a
  target run falling outside the polled window; the exact-match check is
  what actually prevents crossed-run false positives/negatives.
- Known cross-scope finding (not fixed here, out of this task's declared
  artifact list): `integrations/openclaw/adapter/cron_transport.py`
  (`OpenClawCronGatewayTransport`, the production Docker-gateway cron path)
  contains the same `entries[0]`-fallback pattern as the CLI transport did.
  That file needs a separate scope-handoff task to receive the equivalent
  fix.

### 10.1 Corrective pass after independent review REJECT (2026-09-06)

An independent exact-head review of PR #5629 rejected the above pass for nine
functional defects, verified statically against the pinned upstream Gateway
sources (`resolveOpenAiCompatModelOverride` in `gateway/http-utils.ts`,
`MessageItemSchema`/`CreateResponseBodySchema` in
`gateway/open-responses.schema.ts`) and, for the streaming/deadline defects,
against a real local HTTP server (not a mocked `urlopen`). All nine are fixed
in this corrective pass:

1. **Model field vs. provider override.** The pinned Gateway's
   `resolveOpenAiCompatModelOverride` only accepts `openclaw`/
   `openclaw/<agentId>` in the JSON `model` field and rejects a raw provider
   id (e.g. `anthropic/claude-opus-4-8`) with HTTP 400. `stream()` now always
   sends `model: "openclaw/<effective_agent_id>"`; a requested provider/model
   override travels in the `x-openclaw-model` header instead.
2. **`input[]` item shape.** The pinned `MessageItemSchema` is `.strict()`
   and requires a discriminating `type: "message"`; a bare
   `{"role", "content"}` dict is rejected. `_normalize_input_item()` now
   normalizes every history/current-turn entry before it is sent.
3. **Session/tenant isolation.** A caller-supplied `session_id` no longer
   travels verbatim as the upstream `user` key — `derive_session_user()`
   mixes in the authenticated actor (`operator_id`) and tenant
   (`metadata.tenant_id`, when present) ahead of the caller's conversation
   name, so two different callers reusing the same session name can never
   collide onto the same upstream session. Used by both `_invoke_via_http`
   and the raw SSE stream endpoint in `main.py`.
4. **Structured endpoint agent admission gap.** `/structured` had no
   Persona-admission mechanism at all (unlike ordinary invoke's
   `agent_id`+`persona_admission` pairing), so an arbitrary `agent_id` was
   silently accepted. It is now restricted to the default agent only
   (`OPENCLAW_STRUCTURED_AGENT_NOT_ALLOWED`, 422, for anything else).
5. **Extraction schema validation gaps.** `_validate_extraction_arguments`
   now checks `enum` membership, recurses into nested-object
   `required`/`properties`, and handles a nullable `type: [<t>, "null"]`
   union without the previous unhandled `TypeError` (a list is unhashable
   and cannot key a plain dict `.get()`).
6. **SSE parsing.** Fixed three real defects reproduced against a genuine
   socket connection: (a) a duplicate `response.completed` no longer
   re-emits a second `done` event (a `return` now follows the first); (b) a
   legal multi-line SSE event (one JSON object split across consecutive
   `data:` lines with no blank-line separator) is now joined and parsed
   instead of being silently dropped as unparseable fragments; (c) a
   real mid-frame connection close now yields a single truthful
   `OPENCLAW_RESPONSES_EMPTY` error rather than any risk of a fabricated
   success.
7. **Shared total deadline.** `urlopen(timeout=...)` only bounds each
   individual socket read, not the whole streaming loop — a slow drip that
   stays under that per-read timeout on every chunk can keep the loop going
   far past the intended total budget (reproduced: a 0.05s budget completing
   only after 0.266s against a real slow server). The stream loop now checks
   one shared `read_deadline` before processing each line. The cron
   `_wait_for_terminal_run` polling loop has the same fix: `_call` is now
   given only the remaining budget (never a fixed 30s), the inter-poll sleep
   is capped to the remaining time, and a result that only arrives after the
   deadline has already passed is rejected as unknown rather than accepted
   as a late success.
8. **Cron exact-run lookup.** `cron.runs` now requests an exact `{id, runId}`
   lookup (the pinned Gateway supports this ahead of pagination) instead of
   a fixed `limit: 20` page — a sufficiently busy job with more than 20 newer
   runs ahead of the target previously timed out even though the target run
   existed and had already completed. Client-side exact-match filtering is
   kept as defense-in-depth.
9. **Evidence completeness.** See
   `docs/deployment/evidence/SIMPLIFY-OPENCLAW-001/evidence.json` for what
   is now verified (against the pinned Gateway TypeScript sources and real
   local-socket regression tests) versus what remains a genuinely
   unavailable-in-this-sandbox external blocker (the >=100-request live
   base-vs-candidate replay benchmark, which needs a reachable authenticated
   live Gateway/model backend that does not exist in this dev sandbox).

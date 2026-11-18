# OpenClaw Model Pool vs Persona Routing

> Read this before wiring personas to models. The common mistake is treating it
> as one-model-per-persona setup; it is not.

## The model chain: one deployment variable

The ordered model chain (first entry = primary) is held in **one** place: the
repository/dev-environment **variable** `OPENCLAW_MODEL_CHAIN` (comma separated
refs, e.g. `<provider>/<model>,<provider>/<model>,...`; never a workflow
dispatch input). When it is unset or blank the one shipped default
(`DEFAULT_MODEL_CHAIN` in `services/persona/runtime_profile.py`) applies. No other source file names a
model as part of a chain or an allowed-model list; tests only use fixture
values of the variable.

Everything follows that chain:

- the gateway default (`agents.defaults.model.primary` and `.fallbacks`) is written by
  `scripts/openclaw-configure-shared-model-pool.sh` (every root deploy, restore,
  return and compensation pass the variable to it; isolated qualification
  renders the same chain in `scripts/openclaw_isolated_model_bootstrap.py`);
- the `memory-index` and `structured-extraction` agents carry a copy of it;
- the Management AI adapter uses the first entry as its model;
- persona sync (`integrations/openclaw/persona_agent_sync.py`) reads the gateway
  default chain and the registered models (`agents.defaults.models`) **at sync
  time**: a pool-default persona gets that chain, a persona route policy
  (`preferred_pool_model`, `hard_pin`, `fallback`) is validated against the
  models registered in the gateway, and a persona carries its own fallback
  list only when its route policy pins one.

### How to change the chain

1. Set the variable `OPENCLAW_MODEL_CHAIN` (repo or dev environment).
2. Apply it with a dev deploy (the root deploy runs the configure step), or run
   the configure step by hand on the host:
   `OPENCLAW_MODEL_CHAIN='a/x,b/y' PANTHEON_DEV_REPO=$PWD bash scripts/openclaw-configure-shared-model-pool.sh`.
   The step **restarts the gateway** because a change to
   `agents.defaults.model.fallbacks` only takes effect after a restart on the
   pinned image (provider and allow-list changes apply live).
3. Re-run persona sync so persona agents copy the new chain
   (`scripts/openclaw-sync-persona-agents.py`). An existing persona agent whose
   *primary* changes is reported as `model_drift_update_unavailable` (recreate
   the agent); a fallback-only change is rewritten in place.

The configure step **refuses** (named error, previous chain kept, gateway never
left without a chain, exit status 3, advisory: it does not roll a dev deploy
back) a chain that is empty or malformed (`chain_empty`,
`chain_model_ref_malformed`), names a model whose runtime is a CLI backend
(`chain_model_cli_runtime`: persona tool calls lack host identity there), or
names a ref the gateway has not registered (`chain_model_not_registered`: not in
`agents.defaults.models`, not in a `models.providers.<p>.models` catalog and not
in the shipped default). A CLI-runtime model never appears in a persona fallback.

**Order matters per provider.** On the pinned image a `model_not_found`
refusal puts the provider's whole auth profile into cooldown and later
candidates of that provider are skipped without a call. A model not yet proven
on a provider must therefore not precede another model of the same provider in
the chain.

## Mental model

Think OS processes:

- **LLM models = a small shared resource pool (kernel processes).** Set up
  **once**, at the gateway/provider level. The dev pool has five routable model
  slots: two Codex, two Claude, and one Gemini. Model registration and provider
  credentials are deliberately separate: multiple model slots can share an
  auth profile, while providers that support named profiles can route to a
  specific account.
- **Personas = many lightweight identities (user processes).** A persona does
  **not** own or install a model. It just **references** a model ref from the
  pool via its OpenClaw agent config (`model.primary`; the shared pool entry
  declares `agentRuntime`). Many personas → the same model. It is a
  **many-to-few** mapping, never one-pin-per-persona.

The idempotent route declaration lives in
`scripts/openclaw-configure-shared-model-pool.sh` and is applied after every
root dev deploy (with isolated CI qualification mirrored in
`scripts/openclaw_isolated_model_bootstrap.py`). Default routing sets
`agents.defaults.model.primary` and ordered `fallbacks` from the chain above
(shipped default when the variable is unset: Opus, then `openai/gpt-5.6-sol`, then
`openai/gpt-5.5`). Native provider login
remains in the persistent OpenClaw volume; Anthropic API/workspace bindings and
an optional product Claude setup-token come from the gateway environment, as
described below.

Current model slots:

| Slot | Model ref | Runtime | Auth |
|---|---|---|---|
| Codex Sol | `openai/gpt-5.6-sol` | `openclaw` | Product main-profile OpenAI/Codex OAuth (native main-agent auth-profile store on `openclaw-data`) |
| Codex 5.5 | `openai/gpt-5.5` | `openclaw` | Product main-profile OpenAI/Codex OAuth (native main-agent auth-profile store on `openclaw-data`) |
| Claude Opus | `anthropic/claude-opus-4-8` | `openclaw` | `ANTHROPIC_API_KEY` + `models.providers.anthropic.headers["anthropic-workspace-id"]` |
| Claude Sonnet | `anthropic/claude-sonnet-4-6` | `claude-cli` | persisted Claude CLI login or explicit product `CLAUDE_CODE_OAUTH_TOKEN` |
| Gemini Pro | `google/gemini-3.1-pro-preview` | `google-gemini-cli` | Gemini CLI OAuth profile |

### Native main-profile OpenAI OAuth and `structured-extraction` pure inference

In OpenClaw **2026.7.1**, `agentRuntime.id = "codex"` selects the local Codex
app-server harness, which unconditionally rejects `structured-extraction`
(`tools.deny = ["*"]`, `tools.exec = { security: "deny", ask: "always" }`) with
`Codex app-server local execution is not available when tools.exec.mode=deny`.

Both `openai/gpt-5.6-sol` and `openai/gpt-5.5` therefore use
`agentRuntime.id = "openclaw"`. OpenClaw's embedded HTTP/OAuth runtime resolves
the product main-profile OpenAI/Codex OAuth credentials stored in the native
main-agent auth-profile store on `openclaw-data`,
which `structured-extraction` and other agents inherit natively without spawning
`codex` app-server, while keeping `tools.deny = ["*"]` and `tools.exec.security = "deny"`
enforced. Do not mount, symlink, or import developer-worker `.codex` CLI homes
or set `CODEX_HOME` on the product gateway.

### Primary Anthropic API and workspace routing (`anthropic/claude-opus-4-8`)

`anthropic/claude-opus-4-8` runs on `agentRuntime.id = "openclaw"` using
`ANTHROPIC_API_KEY` (Compose `PANTHEON_OPENCLAW_ANTHROPIC_API_KEY`) and the
workspace header `models.providers.anthropic.headers["anthropic-workspace-id"]`
(Compose `PANTHEON_OPENCLAW_ANTHROPIC_WORKSPACE_ID`).

On the persistent `openclaw-data` volume,
`scripts/openclaw-configure-shared-model-pool.sh` writes the non-secret
workspace ID as a literal value (removing `ANTHROPIC_WORKSPACE_ID` from the
`config set` subprocess environment so `restoreEnvVarRefs` never persists an
unresolved `${ANTHROPIC_WORKSPACE_ID}` reference on disk) and leaves any
existing persisted header untouched when the env var is absent, preserving
rollback compatibility with older Compose definitions. Isolated CI qualification
(`scripts/openclaw_isolated_model_bootstrap.py`) binds
`${ANTHROPIC_WORKSPACE_ID}` in its ephemeral container config.

### Isolated qualification OpenAI native fallback routing (`openai/gpt-5.6-sol`, `openai/gpt-5.5`)

On the persistent dev/prod `openclaw-data` volume, `openai/gpt-5.6-sol` and
`openai/gpt-5.5` (`agentRuntime.id = "openclaw"`) authenticate via the native
`main` agent auth-profile store
(`/home/node/.openclaw/agents/main/agent/openclaw-agent.sqlite`), which
`structured-extraction` inherits automatically when its own agent store has no
override.

In isolated FULL qualification (`isolated-l12-provider-qualification` in
`.github/workflows/nonprod-deploy.yml`), the task-scoped Compose project
(`l12q${GITHUB_RUN_ID}x${GITHUB_RUN_ATTEMPT}`) mounts a fresh empty
`openclaw-data` volume and never mounts or reads the host VM `openclaw-data`
volume or developer-worker `.codex` homes. Verified against pinned OpenClaw
`2026.7.1` (`ghcr.io/openclaw/openclaw:2026.7.1`,
`sha256:f435502b113f873768b64cd7f5f2a63f44d0a236ae3a5647ff42716652c74c31`),
OpenClaw's embedded `openclaw` runtime resolves native OpenAI fallback auth for
both `main` and `structured-extraction` directly from the provider `SecretRef`
without `auth.profiles` or SQLite store writes:

1. **Product ChatGPT/Codex OAuth bearer access token
   (`PANTHEON_OPENCLAW_OPENAI_OAUTH_TOKEN` -> container
   `OPENCLAW_OPENAI_OAUTH_TOKEN`)**:
   - `.github/workflows/nonprod-deploy.yml` binds GitHub `dev` environment
     secret `DEV_OPENCLAW_OPENAI_OAUTH_TOKEN` into
     `PANTHEON_OPENCLAW_OPENAI_OAUTH_TOKEN`, and `docker-compose.yml` passes it
     into `openclaw-gateway` as `OPENCLAW_OPENAI_OAUTH_TOKEN` (preserving the
     absence of `OPENAI_API_KEY` per `integrations/openclaw/governance.md` §9).
   - `scripts/openclaw_isolated_model_bootstrap.py` requires
     `PANTHEON_OPENCLAW_OPENAI_OAUTH_TOKEN` alongside
     `PANTHEON_OPENCLAW_ANTHROPIC_API_KEY` and
     `PANTHEON_OPENCLAW_ANTHROPIC_WORKSPACE_ID` and emits the non-secret
     `models.providers.openai` entry
     `{"baseUrl": "https://chatgpt.com/backend-api/codex", "api": "openai-chatgpt-responses", "auth": "token", "apiKey": {"source": "env", "provider": "default", "id": "OPENCLAW_OPENAI_OAUTH_TOKEN"}, "models": []}`.
2. **Access-only and teardown invariants**:
   - Only the product access token is passed via the non-secret provider
     `SecretRef`; single-use OAuth `refresh_token` material and raw JSON auth
     stores are refused fail-closed, and isolated qualification teardown removes
     all task-scoped containers and volumes (`docker compose down --volumes --remove-orphans`).

### Explicit product Claude CLI token (`anthropic/claude-sonnet-4-6`)

The optional Compose input `PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN` becomes
`CLAUDE_CODE_OAUTH_TOKEN` inside the product gateway for the `claude-cli`
backend (`anthropic/claude-sonnet-4-6`). OpenClaw **2026.7.1** clears
that ambient variable before starting its Claude CLI child. Therefore a direct
`claude auth status` or `claude -p` success does not establish gateway readiness.

When this product token is non-empty, the model-pool configure script sets only
the `claude-cli` backend command and its `CLAUDE_CODE_OAUTH_TOKEN` env override.
The persisted value is the literal `${CLAUDE_CODE_OAUTH_TOKEN}` reference; the
secret is not put in the script, command arguments, or committed configuration.
Other credential/endpoint variables remain subject to upstream environment
cleaning. Tool permissions and provider/model routing are unchanged. An absent
token does not create an unresolved reference on a new native-login deployment;
a configured reference whose token is later removed must fail validation, not
silently switch credentials.

Use only the credential already authorized for the product gateway. Do not copy
developer-worker logins into the product, disable `clearEnv`, or use the upstream
live-test preserve-env escape hatch. After configuration, validate a real
OpenClaw turn, then the authenticated Management journey; neither a non-empty env
value nor a passing schema check proves a terminal product answer/action receipt.

So "make a new persona that runs on Claude" is **not** an install task. It is a
single config field on the persona pointing at an already-pooled model ref.

## How a persona selects a model

The provider auth + runtime are already configured (kernel layer). A persona
(OpenClaw agent) only names the model:

```json5
// per-agent override; the provider auth profile is shared, not per-agent
{
  agents: {
    list: [
      { id: "persona-bull",  model: { primary: "openai/gpt-5.5" } },
      { id: "persona-bear",  model: { primary: "anthropic/claude-opus-4-8" } },
      { id: "persona-judge", model: { primary: "anthropic/claude-opus-4-8" } },
    ],
  },
}
```

The runtime (`openclaw` for embedded API/OAuth inference, or a CLI backend id)
and default failover order are set on the shared model pool, not re-declared per
persona:

```json5
{ agents: { defaults: {
  model: {
    primary: "anthropic/claude-opus-4-8",
    fallbacks: ["openai/gpt-5.6-sol", "openai/gpt-5.5"],
  },
  models: {
    "openai/gpt-5.6-sol": { alias: "codex-sol", agentRuntime: { id: "openclaw" } },
    "openai/gpt-5.5": { alias: "codex-5.5", agentRuntime: { id: "openclaw" } },
    "anthropic/claude-opus-4-8": { alias: "opus", agentRuntime: { id: "openclaw" } },
    "anthropic/claude-sonnet-4-6": { alias: "sonnet", agentRuntime: { id: "claude-cli" } },
    "google/gemini-3.1-pro-preview": {
      alias: "gemini",
      agentRuntime: { id: "google-gemini-cli" },
    },
  },
} } }
```

`persona-bear` and `persona-judge` above both ride the **same** Anthropic
API/workspace pool entry and the **same** `openclaw` runtime — they multiplex
over one pool entry (while personas targeting `anthropic/claude-sonnet-4-6`
multiplex over the `claude-cli` runtime).

## The real bottleneck: shared quota, not persona count

Because the pool is shared, every persona on a given auth profile draws from
that account's allowance, separated by actual runtime and credential bucket:

- `anthropic/claude-opus-4-8` (`openclaw` runtime) draws from the bound
  `ANTHROPIC_API_KEY` and `anthropic-workspace-id` API rate limit and spend
  quota.
- `anthropic/claude-sonnet-4-6` (`claude-cli` runtime) draws from the Claude
  CLI subscription / explicit product `CLAUDE_CODE_OAUTH_TOKEN` Agent SDK credit
  and rate limit (post-2026-06-15 billing; see `governance.md §9`).
- `openai/gpt-5.6-sol` and `openai/gpt-5.5` (`openclaw` runtime) share the
  product main-profile OpenAI/Codex OAuth (native main-agent auth-profile store on `openclaw-data`)
  subscription quota.
- `google/gemini-3.1-pro-preview` (`google-gemini-cli` runtime) draws from the
  Gemini CLI OAuth profile quota.

A multi-persona debate with ten personas targeting the same pool slot is ten
user-processes contending for **one** kernel resource. The
concurrency/throughput ceiling is that model account's or workspace's quota,
**not** the number of personas.

**To scale concurrency, grow the auth pool, not the persona count.** OpenClaw
2026.7.1 supports multiple named OpenAI/Codex OAuth logins in the native agent
auth store with `--profile-id`, explicit selection with `/model ...@<profileId>`,
and ordered rotation. On the Anthropic side, `anthropic/claude-opus-4-8` uses
the direct Anthropic API + workspace credential on the `openclaw` runtime,
whereas `anthropic/claude-sonnet-4-6` uses the `claude-cli` login/token;
separate CLI accounts require separate direct Anthropic profiles or isolated CLI
homes. Do not copy developer-worker credentials or a Claude/Codex token into
repo config to simulate an extra account.

## See also
- `integrations/openclaw/governance.md §9` — provider auth setup (Codex
  device-code OAuth, Claude CLI reuse) and the derived gateway image.
- `integrations/openclaw/integration.md` — pin and adapter boundary.

## `memory-index` agent (Hindsight background model calls)

`scripts/openclaw-configure-shared-model-pool.sh` and the isolated bootstrap add
a second deny-all agent, `memory-index` (tools deny `*`, exec security deny/ask
always), under the same adapter policy assertions as `structured-extraction`.
Its model is `agents.list[].model`: a copy of the one model chain (primary and
fallbacks), written by the configure step; there are no per-agent model
overrides any more. Hosted evidence 2026-11-18 (7 days, 576 sessions) showed
opus-4-8 failing 295 times in this role, which is why the chain order is an
operator decision made through the variable rather than code. Reached only through the adapter's internal
memory LLM bridge (see `deploy/hindsight/README.md`).

## OpenAI API route (`openai-api`, `openai-api-2`)

Shipped default chain: Opus, `openai-api/gpt-6.2-sol`, `openai-api-2/gpt-6.2-sol`, then the subscription
entries `openai/gpt-5.6-sol`, `openai/gpt-5.5` (`DEFAULT_MODEL_CHAIN`, `OPENCLAW_MODEL_CHAIN` overrides).
The default agent chain, `structured-extraction` and `memory-index` all carry it.

- Mechanism (pinned image 2026.7.1): one provider per key (`baseUrl https://api.openai.com/v1`, `api openai-responses`,
  `apiKey` env SecretRef) with model-level fallback. A 401/quota/rate-limit on key 1 falls to key 2; a refusal of both
  falls to the subscription entries. A `model_not_found` cools down the whole provider profile, so later refs on the
  same provider are skipped. A change of `agents.defaults.model.fallbacks` needs a gateway restart (the configure step restarts).
- Keys live in container variables `OPENCLAW_API_ROUTE_KEY_1/_2` only (never `OPENAI_API_KEY`/`CODEX_API_KEY`).
- Missing/empty key: provider unset and its refs removed from the default chain; a requested chain naming it is refused (advisory, exit 3).
- Isolated qualification: `scripts/openclaw_isolated_model_bootstrap.py` adds the same providers when `PANTHEON_OPENCLAW_OPENAI_API_KEY[_2]` are set.
- Per-token billing; see `governance.md` (2026-11-18).

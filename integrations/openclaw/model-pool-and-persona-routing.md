# OpenClaw Model Pool vs Persona Routing

> Read this before wiring personas to models. The common mistake is treating it
> as one-model-per-persona setup; it is not.

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
`agents.defaults.model.primary = "anthropic/claude-opus-4-8"` with ordered
fallbacks `["openai/gpt-5.6-sol", "openai/gpt-5.5"]`. Native provider login
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
(`/home/node/.openclaw/agents/main/agent/openclaw-agent.sqlite`) populated by
`openclaw models auth login --provider openai-codex`, which
`structured-extraction` inherits automatically when its own agent store has no
override.

In isolated FULL qualification (`isolated-l12-provider-qualification` in
`.github/workflows/nonprod-deploy.yml`), the task-scoped Compose project
(`l12q${GITHUB_RUN_ID}x${GITHUB_RUN_ATTEMPT}`) mounts a fresh empty
`openclaw-data` volume and never mounts or reads the host VM `openclaw-data`
volume or developer-worker `.codex` homes. Empirically verified against pinned
OpenClaw `2026.7.1` (`ghcr.io/openclaw/openclaw:2026.7.1`,
`sha256:f435502b113f873768b64cd7f5f2a63f44d0a236ae3a5647ff42716652c74c31`),
OpenClaw's embedded `openclaw` runtime resolves native OpenAI fallback auth for
both `main` and `structured-extraction` without persisting raw secrets on disk
through two non-secret `SecretRef` paths:

1. **Product ChatGPT/Codex OAuth bearer access token
   (`PANTHEON_OPENCLAW_OPENAI_OAUTH_TOKEN` -> container
   `OPENCLAW_OPENAI_OAUTH_TOKEN`)**:
   - `scripts/openclaw_isolated_model_bootstrap.py` emits non-secret batch
     entries `auth.profiles.openai:default = {"provider": "openai", "mode": "token"}`
     and `models.providers.openai = {"baseUrl": "https://chatgpt.com/backend-api/codex", "api": "openai-chatgpt-responses", "auth": "token", "apiKey": {"source": "env", "provider": "default", "id": "OPENCLAW_OPENAI_OAUTH_TOKEN"}, "models": []}`.
   - When `PANTHEON_OPENCLAW_ISOLATED_MODEL_BATCH` is active, `openclaw-gateway`
     seeds only the non-secret `tokenRef`
     (`{type: "token", provider: "openai", tokenRef: {source: "env", provider: "default", id: "OPENCLAW_OPENAI_OAUTH_TOKEN"}}`)
     in `/home/node/.openclaw/agents/main/agent/openclaw-agent.sqlite` before
     `config set --batch-json` and `gateway` startup.
   - `structured-extraction` inherits `openai:default` from `main` via
     `loadAuthProfileStoreForAgent` and resolves `openai/gpt-5.6-sol` and
     `openai/gpt-5.5` on `https://chatgpt.com/backend-api/codex`
     (`openai-chatgpt-responses`) with `mode: "token"`.
2. **Approved OpenAI API key (`PANTHEON_OPENCLAW_OPENAI_API_KEY` -> container
   `OPENAI_API_KEY`)**:
   - Emits `auth.profiles.openai:default = {"provider": "openai", "mode": "api_key"}`
     and `models.providers.openai = {"baseUrl": "https://api.openai.com/v1", "api": "openai-responses", "auth": "api-key", "apiKey": {"source": "env", "provider": "default", "id": "OPENAI_API_KEY"}, "models": []}`,
     seeding the non-secret `keyRef` in `main` so both `main` and
     `structured-extraction` resolve `mode: "api-key"` on
     `https://api.openai.com/v1` (`openai-responses`).
3. **Rotation and single-use `refresh_token` safety**:
   - Never duplicate a single-use OAuth `refresh_token` across concurrent
     refreshing endpoints (`refresh_token_reused`), never read or copy developer
     worker `.codex` homes, and never overwrite an existing `type: "oauth"`
     profile on a persistent volume with a static seed.
   - `scripts/openclaw_isolated_model_bootstrap.py` rejects placeholders,
     whitespace-corrupted values, raw JSON auth stores, and `refresh_token`
     payloads fail-closed before container startup.

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

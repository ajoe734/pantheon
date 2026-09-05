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
  pool via its OpenClaw agent config (`model.primary`, and an `agentRuntime` for
  CLI-backed models). Many personas → the same model. It is a **many-to-few**
  mapping, never one-pin-per-persona.

The idempotent route declaration lives in
`scripts/openclaw-configure-shared-model-pool.sh` and is applied after every
root dev deploy. Native provider login remains in the persistent OpenClaw volume;
an explicitly provisioned product Claude setup-token may instead come from the
gateway environment, as described below.

Current model slots:

| Slot | Model ref | Runtime | Auth |
|---|---|---|---|
| Codex Sol | `openai/gpt-5.6-sol` | `codex` | OpenAI/Codex OAuth profile |
| Codex 5.5 | `openai/gpt-5.5` | `codex` | OpenAI/Codex OAuth profile |
| Claude Opus | `anthropic/claude-opus-4-8` | `claude-cli` | persisted Claude CLI login |
| Claude Sonnet | `anthropic/claude-sonnet-4-6` | `claude-cli` | persisted Claude CLI login |
| Gemini Pro | `google/gemini-3.1-pro-preview` | `google-gemini-cli` | Gemini CLI OAuth profile |

### Explicit product Claude token

The optional Compose input `PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN` becomes
`CLAUDE_CODE_OAUTH_TOKEN` inside the product gateway. OpenClaw **2026.7.1** clears
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

For CLI-backed models (Claude), the runtime is set on the model entry, not
re-declared per persona:

```json5
{ agents: { defaults: { models: {
  "openai/gpt-5.6-sol": { agentRuntime: { id: "codex" } },
  "openai/gpt-5.5": { agentRuntime: { id: "codex" } },
  "anthropic/claude-opus-4-8": { agentRuntime: { id: "claude-cli" } },
  "anthropic/claude-sonnet-4-6": { agentRuntime: { id: "claude-cli" } },
  "google/gemini-3.1-pro-preview": {
    agentRuntime: { id: "google-gemini-cli" },
  },
} } } }
```

`persona-bear` and `persona-judge` above both ride the **same** Claude
subscription and the **same** claude-cli runtime — they multiplex over one pool
entry.

## The real bottleneck: shared quota, not persona count

Because the pool is shared, every persona on a given auth profile draws from
that account's allowance:

- All `anthropic/*` personas share the Claude subscription's Agent SDK credit and
  rate limit (post-2026-06-15 billing; see `governance.md §9`).
- All `openai/*` personas share the ChatGPT/Codex subscription's quota.

A multi-persona debate with ten Claude personas is ten user-processes contending
for **one** kernel resource. The concurrency/throughput ceiling is the model
account's quota, **not** the number of personas.

**To scale concurrency, grow the auth pool, not the persona count.** OpenClaw
2026.7.1 supports multiple named Codex logins with `--profile-id`, explicit
selection with `/model ...@<profileId>`, and ordered rotation. The two Claude
model slots intentionally reuse one Claude CLI login; separate Claude accounts
require separate direct Anthropic profiles or isolated CLI homes. Do not copy a
Claude token into repo config to simulate an extra account.

## See also
- `integrations/openclaw/governance.md §9` — provider auth setup (Codex
  device-code OAuth, Claude CLI reuse) and the derived gateway image.
- `integrations/openclaw/integration.md` — pin and adapter boundary.

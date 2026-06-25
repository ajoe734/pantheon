# OpenClaw Model Pool vs Persona Routing

> Read this before wiring personas to models. The common mistake is treating it
> as one-model-per-persona setup; it is not.

## Mental model

Think OS processes:

- **LLM models = a small shared resource pool (kernel processes).** Set up
  **once**, at the gateway/provider level: each provider has one auth profile.
  Today the pool is two entries — `openai/gpt-5.5` (Codex subscription OAuth) and
  `anthropic/claude-opus-4-8` (Claude CLI subscription). Adding a model to the
  pool = a one-time auth + runtime wiring step (see `governance.md §9`).
- **Personas = many lightweight identities (user processes).** A persona does
  **not** own or install a model. It just **references** a model ref from the
  pool via its OpenClaw agent config (`model.primary`, and an `agentRuntime` for
  CLI-backed models). Many personas → the same model. It is a **many-to-few**
  mapping, never one-pin-per-persona.

So "make a new persona that runs on Claude" is **not** a setup task — there is no
new auth, no new install, no new runtime. It is a single config field on the
persona pointing at an already-pooled model ref.

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
  "anthropic/claude-opus-4-8": { agentRuntime: { id: "claude-cli" } },
} } } }
```

`persona-bear` and `persona-judge` above both ride the **same** Claude
subscription and the **same** claude-cli runtime — they multiplex over one pool
entry.

## The real bottleneck: shared quota, not persona count

Because the pool is shared, every persona on a given model draws from that one
account's allowance:

- All `anthropic/*` personas share the Claude subscription's Agent SDK credit and
  rate limit (post-2026-06-15 billing; see `governance.md §9`).
- All `openai/*` personas share the ChatGPT/Codex subscription's quota.

A multi-persona debate with ten Claude personas is ten user-processes contending
for **one** kernel resource. The concurrency/throughput ceiling is the model
account's quota, **not** the number of personas.

**To scale concurrency, grow the pool (add accounts / provider profiles), not the
persona count.** Each added account is another `openclaw models auth login ...`
profile (a new kernel-process), after which personas can be routed across them.

## See also
- `integrations/openclaw/governance.md §9` — provider auth setup (Codex
  device-code OAuth, Claude CLI reuse) and the derived gateway image.
- `integrations/openclaw/integration.md` — pin and adapter boundary.

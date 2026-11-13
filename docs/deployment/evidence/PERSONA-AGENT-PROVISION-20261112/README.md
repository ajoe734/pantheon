# Persona agent provisioning (trading personas)

Task `PERSONA-AGENT-PROVISION-20261112` · owner Claude · reviewer Codex2.
Design: `docs/04/pantheon_persona_cycle_reconnect_2026-11-12/SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md` §6.2 / D3.

## What changed

- `integrations/openclaw/persona_agent_sync.py`: `build_persona_soul` is the single SOUL renderer.
  It renders registry fields plus the **active harness version read from the Persona owner record**
  (`metadata.persona_harness`, same schema/digest validation as the runtime profile). Absent or invalid
  harness is stated in the SOUL with its reason; nothing is synthesized. The old "update MEMORY.md"
  instruction is replaced by "stage a memory candidate" (MEMORY.md is a read-only cache).
  `PERSONA_AGENT_TOOL_PROFILE` / `persona_agent_runtime_policy()` = `read` + `persona_proposal_submit`,
  exec denied; a validator refuses any other tool. `sync_persona_agents(runtime_policy=...)` writes it
  to `agents.list` on create and update.
- `services/openclaw-gateway-adapter/main.py`: `POST /api/openclaw-adapter/agents/persona/ensure`
  (existing service-token middleware, no new credential) reconciles one trading persona through the
  existing reconciler and materializes its memory; `POST .../agents/persona/memory/materialize`
  refreshes the cache on request before a cycle turn. Ids of `main`, servant and opinion agents and a
  workspace other than the persona's own are refused.
- `persona_provisioning_coordinator.py`: after the schedule is admitted, `agent_provisioner(persona_id)`
  runs best-effort. Result is stored as `result.openclaw_agent` = `ready` or `degraded` + `reason`;
  it never changes state, error or compensation, so paper admission is unaffected.
- `integrations/openclaw/persona_agent_client.py` + `default_agent_provisioner` (coordinator): the real
  entry reads the Persona owner record and calls the adapter route through the existing ops client
  (subclass; same URL/timeout/service-token handling).
- `scripts/openclaw-sync-persona-agents.py`: second renderer and re-implemented reconcile removed;
  it delegates to the library (fails closed with `shared_reconciler_unavailable` otherwise) and has
  `--persona-id` for the backfill of the three current trading personas.

## Backfill (operator step, not performed here)

`python3 scripts/openclaw-sync-persona-agents.py --personas-file personas.json --persona-id <青禾 id> --persona-id <衡岳 id> --persona-id <澄川 id>`
with `PANTHEON_MEMORY_API_URL` set, from a checkout on `PYTHONPATH`. No hosted run is claimed by this task.

## Not claimed

Hosted deployment, a real approved harness patch/activation, the `persona_proposal_submit` tool
implementation (C1 owns proposal submission), and per-cycle invocation of the materialize route
(C1 controller) are outside this task. Tool name is the contract the controller must provide.

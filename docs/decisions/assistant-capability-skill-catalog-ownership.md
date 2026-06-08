# Assistant Capability = OpenClaw Skill (Catalog-Driven Surfaces)

Status: draft-canonical
Last updated: 2026-06-08
Source of truth inputs:
- `services/openclaw-gateway-adapter/tool_workflow_bridge.py` (SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE: deny-by-default tool/workflow policy)
- `services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md` (first-class `skill` entity lifecycle + confirm-token policy)
- `services/openclaw-gateway-adapter/assistant_codex_provider.py` (provider readiness / auth probe)
- `services/openclaw-gateway-adapter/assistant_credential_mounts.py` (service-user credential mount contract)
Tier: L1 Platform Architecture & Policy
Scope: how Management AI / assistant capabilities (SA/SD generation, control-mode, resync, provider re-auth, …) are declared, governed, discovered, and surfaced in the frontend
Conflict rule: this decision governs whether an assistant-invocable capability may exist as a hardcoded frontend affordance or a dedicated provider/BFF route. A narrower capability-specific decision may override an individual field (e.g. a bespoke confirm policy) but may not reintroduce a hardcoded, catalog-bypassing surface.
Executed by: EPIC `ASST-SKILL` (`scripts/dispatch_assistant_skill_catalog_2026-06-08.py`)

## Decision summary

- An assistant capability is a **governed OpenClaw skill**, not a hardcoded
  frontend button and not a one-off bespoke route.
- **OpenClaw owns the registry and the policy.** Which capabilities exist, who
  may invoke them, in which mode, and with what confirmation, is resolved by the
  OpenClaw tool/workflow policy layer — deny-by-default — and exposed through the
  effective-tools discovery endpoint.
- The **frontend is a generic renderer.** Surfaces (toolbar buttons, command
  palette entries, degraded-card actions) are produced by iterating the
  effective skill catalog for the current operator/agent/mode. The frontend must
  not enumerate capabilities in source.
- The **BFF is a governed proxy**, not a capability author. It maps operator
  identity/context into the OpenClaw bridge, enforces mode/role/confirm-token
  gates from the descriptor, and writes the audit trail. It must not host a
  per-capability bespoke endpoint that bypasses the catalog.
- This applies uniformly to existing capabilities (SA/SD doc generation,
  control-mode activate/deactivate, resync) and to new ones (provider re-auth via
  device flow). No capability is special-cased into the UI.

## Why

The intended platform model already exists in the backend:

- a first-class, governed `skill` entity with a full lifecycle
  (`validate → submit_for_approval → activate → publish → approve →
  deprecate → retire`) and confirm-token / role gating
  (`BFF_COMMAND_API_CONTRACT.md`, route `POST /bff/actions/skill/{skillId}/{actionId}`);
- an OpenClaw tool/workflow policy bridge that is **deny-by-default**, fails
  closed on unknown tools, maps operator identity into upstream calls, and audits
  every invocation (`tool_workflow_bridge.py`);
- a per-context discovery endpoint
  (`GET /api/openclaw-adapter/tools?agent_id=…`) plus a policy endpoint
  (`GET /api/openclaw-adapter/tools/policy`).

Despite this, the Management AI surface currently exposes capabilities as
**hardcoded toolbar buttons** (`SA/SD`, `Control`, `OpenClaw`, `Resync`) that
each call a **dedicated route** (e.g. SA/SD → `dev-docs/generate`), bypassing the
catalog and the policy layer. Consequences:

- adding a capability requires a frontend change **and** a new route, instead of
  registering one skill;
- gating (kernel mode / control-mode passphrase / role / confirm-token) is
  scattered per route instead of resolved once from policy;
- capabilities cannot be differentiated per persona/operator by policy, because
  the affordances are baked into the client.

This decision realigns the surfaces with the model the backend already implements.

## Canonical ownership matrix

| Concern | Owner | Description |
|---|---|---|
| capability definition | OpenClaw skill registry | the skill descriptor is the source of truth for a capability |
| capability availability (per context) | OpenClaw tool/workflow policy | deny-by-default; resolves effective skills for operator/agent/mode |
| invocation gating | skill descriptor + policy | mode gate, role, confirm-token requirement live on the descriptor |
| identity mapping + audit | BFF + adapter bridge | operator identity into upstream; one audit record per invocation |
| surface rendering | frontend (generic) | iterate effective catalog; render button/command/card from descriptor |
| capability handler | owning domain/provider | the actual work (doc generation, re-auth, resync) behind the skill |
| credential material | service-user mount only | never transits BFF/FE; see `assistant_credential_mounts.py` |

## Assistant-skill descriptor

A capability is declared once as a descriptor resolved through OpenClaw. Minimum
fields:

| Field | Meaning |
|---|---|
| `id` | stable skill id, e.g. `assistant.sa_sd.generate`, `assistant.provider.reauth` |
| `title` / `i18n` | human label for the surface |
| `surface` | `button` \| `command` \| `card_action` — how the FE renders it |
| `mode_gate` | required assistant mode, e.g. `kernel`, `control` |
| `role` | required operator role (reuses BFF role model) |
| `confirm_policy` | none \| confirm-token (high-risk catalog id) |
| `input_schema` | JSON schema for arguments (FE builds the form/modal) |
| `handler_ref` | which provider/BFF/workflow handler executes it |
| `result_surface` | how the result is shown (inline, transcript, card refresh) |

The frontend renders strictly from these fields. Whether a button is enabled,
whether a confirm step appears, and what inputs are collected are all derived
from the descriptor + policy, never from client-side conditionals per capability.

## Detailed rules

### OpenClaw (registry + policy)

Owns:

- the set of skills that exist and their descriptors;
- the **effective** skill resolution per operator/agent/mode (deny-by-default);
- deny-closed behavior for unknown/disallowed skills.

Must not: delegate capability existence to client code.

### BFF (governed proxy)

Owns:

- mapping operator identity/context into the OpenClaw bridge;
- enforcing the descriptor's mode/role/confirm-token gates;
- the per-invocation audit record.

Must not: introduce a bespoke per-capability endpoint that the catalog does not
know about. Existing bespoke routes are permitted only as **handlers wrapped
behind a catalog skill** during migration (see below), not as independent
surfaces.

### Frontend (generic renderer)

Owns:

- fetching the effective catalog for the current context;
- rendering surfaces (toolbar, command palette, degraded-card actions) by
  iterating descriptors;
- building input modals from `input_schema` and confirm steps from
  `confirm_policy`.

Must not: enumerate capabilities, hardcode a button per capability, or call a
capability route the catalog did not advertise.

### Credentials

No capability may cause provider credential material to transit the BFF or FE.
Capabilities that establish provider auth (e.g. `assistant.provider.reauth`) use
the device flow: the secret is exchanged between the operator's browser and the
identity provider and written by the provider CLI directly into the service-user
mount (`assistant_credential_mounts.py`). The capability surface only carries the
public verification URL, user code, and poll status.

## Worked examples

- **SA/SD generation** becomes `assistant.sa_sd.generate`
  (`surface: button`, `mode_gate: kernel`, `handler_ref:` the existing
  `dev-docs/generate` handler). The toolbar button disappears from source and is
  rendered from the catalog.
- **Provider re-auth** becomes `assistant.provider.reauth`
  (`surface: card_action` on the degraded card, `mode_gate: kernel`,
  `confirm_policy:` control-mode, device-flow handler). It is not a special
  button; it is the same kind of catalog entry as SA/SD.
- **Resync / Control** are likewise descriptors, not bespoke buttons.

## Migration path (non-disruptive)

1. **Wrap, don't rip.** Register each current capability as a skill whose
   `handler_ref` points at the **existing** route/provider call. No handler logic
   changes in step 1.
2. **Expose** the effective catalog through the assistant surface the FE already
   talks to (reuse `GET /api/openclaw-adapter/tools` + policy).
3. **Flip the FE** toolbar to render from the catalog; delete the hardcoded
   buttons once parity is confirmed.
4. **Tighten policy**: move per-route gate checks into the descriptor/policy so
   gating is resolved once.
5. **Add new capabilities as skills only** (e.g. `assistant.provider.reauth`),
   never as new hardcoded buttons.

Pilot scope: migrate **one** capability (SA/SD) end-to-end as the template, then
roll the pattern across the remaining toolbar entries and `assistant.provider.reauth`.

## Consequences

- New assistant capabilities ship by registering a governed skill, with no
  required frontend change.
- Gating and audit are uniform and policy-resolved, not per-route.
- Capabilities can be differentiated per persona/operator by policy.
- The frontend toolbar becomes data, not code.

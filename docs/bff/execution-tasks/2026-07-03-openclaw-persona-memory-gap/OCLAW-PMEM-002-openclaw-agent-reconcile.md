# OCLAW-PMEM-002 - OpenClaw Persona Agent Reconciliation

Owner: Codex2
Reviewer: Claude
Parent: `OCLAW-PMEM-000`
Depends on: `OCLAW-PMEM-001`

## Problem

General persona creation does not reliably create or update
`openclaw/{persona_id}`. The tested sync library and deploy script duplicate
SOUL rendering, and existing-agent reconciliation updates identity/SOUL but not
model drift.

## Scope

- Make general persona create/update call a shared OpenClaw persona reconciler,
  or emit a durable reconcile event consumed by the same path.
- Reconcile identity, workspace, model route, SOUL, and sync generation for new
  and existing agents.
- Detect existing-agent model drift and update the model or block with a precise
  reason and repair action.
- Remove deploy-script/library SOUL renderer drift by sharing a renderer or
  adding parity tests that fail on divergence.
- Include Memory section parity in live deploy script output.

## Acceptance

- Creating a persona in BFF results in a reachable `openclaw/{persona_id}` agent
  or a failed reconcile record with explicit reason.
- Updating the runtime profile model changes the OpenClaw agent model or fails
  closed with a repairable reason.
- Unit tests cover new agent, existing agent, model drift, SOUL parity, and
  failed CLI/OpenClaw behavior.
- Dev evidence shows at least one persona response through
  `model=openclaw/{persona_id}` after reconciliation.

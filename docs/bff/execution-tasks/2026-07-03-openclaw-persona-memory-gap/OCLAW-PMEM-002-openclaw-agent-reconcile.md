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

## 2026-07-11 owner verification

- Implementation commit `4ebd260a50466841965b3d8af0ea9c5b4377522a` merged through
  Pantheon PR #3003 as merge commit `875f770f0`.
- Focused verification passed:
  `python3 -m pytest integrations/openclaw/test_persona_agent_sync.py services/control-plane/bff/test_bff_strategy_persona_contract.py -q`
  (`37 passed`), followed by `python3 -m py_compile` for the reconciler,
  deploy driver, and BFF module, plus `git diff --check`.
- The live response acceptance remains open. The public dev host resolves, but
  this background worker cannot reach the gateway VM: the `pantheon-gcp` SSH
  alias still points to the retired address, no current VM private key is
  installed, and `gcloud compute` credential refresh requires an interactive
  login. No gateway token was exposed or copied as a workaround.
- Repair action: restore non-interactive operator access to the current dev VM
  (or refresh the worker's gcloud application credentials), then reconcile one
  registry persona and capture a real `/v1/responses` result using
  `model=openclaw/{persona_id}`. Until that evidence is attached, this task is
  not ready for review or `done`.

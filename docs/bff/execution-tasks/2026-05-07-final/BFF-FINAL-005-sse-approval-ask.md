# BFF-FINAL-005 - SSE Approval and Ask Channels

Priority: P1

Depends on: BFF-FINAL-001

Area: realtime feed

## Goal

Extend Pantheon BFF realtime/SSE contract with final `approval` and `ask` channels and publish replay/resync semantics.

## Contract Inputs

Final channel catalog:

```text
approval, ask, artifact, runtime, mcp, skill, channel, tool,
ranking, rebalance, evolution, research, signal, inbox,
journal, postmortem, loop, sentinel, intervention, audit, system
```

Approval resync:

- `/bff/approvals`
- `/bff/v5/interventions`

Ask resync:

- `/bff/agora/ask/sessions/{id}`

## Implementation Scope

Likely files:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/models.py`
- `services/control-plane/bff/test_pkt005_sse_substrate_contract.py`
- new SSE event tests
- `services/control-plane/bff/BFF_API_CONTRACT.md`

## Steps

1. Define `SseEventEnvelope`.
2. Add approval event payloads:
   - `approval.created`
   - `approval.stage.changed`
   - `approval.decided`
   - `approval.sla.escalated`
3. Add ask event payloads:
   - `ask.session.started`
   - `ask.message.delta`
   - `ask.tool.called`
   - `ask.message.completed`
   - `ask.session.completed`
   - `ask.session.failed`
4. Add channel validation for the final catalog.
5. Publish replay window metadata for channels where BFF can replay.
6. Return `SSE_REPLAY_UNAVAILABLE` when replay is requested beyond supported history.
7. Ensure BFF HA policy remains respected: do not implement multi-replica shared replay store in this pack.

## Acceptance Criteria

- `approval` and `ask` are valid BFF SSE channels.
- Approval decisions can emit approval channel events.
- Ask stream can emit session/message/tool events or contract fixtures.
- Replay failure uses final error envelope.
- Existing SSE substrate tests still pass.

## Verification

```bash
python -m pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py -q
python -m pytest services/control-plane/bff -k "sse or event" -q
```

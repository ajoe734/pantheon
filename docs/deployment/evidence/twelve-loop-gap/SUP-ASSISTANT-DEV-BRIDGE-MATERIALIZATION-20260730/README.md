# SUP-ASSISTANT-DEV-BRIDGE-MATERIALIZATION-20260730

Owner: `Codex2`  
Reviewer: `Antigravity`  
Status: independent review approved; owner closeout in progress

## Outcome

Assistant `DevTaskPacket` admission now requires two canonical readbacks before
the packet can be marked seen or its inbox receipt can become `processed`:

1. every signed task must be returned by the governed `ai_status.py show`
   command with the exact signed task spec and bridge provenance;
2. `verify_task_state_store.py` must report parity between the validated
   task-state journal checkpoint and `ai-status.json`.

The supervisor supplies the already provisioned status root, command runtime,
and absolute task-state journal binding directly to the inbox drain. It also
sets the fail-closed readback requirement, so a transiently missing binding
cannot fall back to a file/activity-only assignment. No
`.orchestrator/config.json` change is required.

Successful receipts expose `auditRefs.materializationReadback`, including the
canonical task ids, active/archive source, task-spec hashes, event count, last
event id, and journal/projection state digests. The supervisor copies these
readbacks into its runtime `last_result` and
`assistant_dev_packet_inbox_drained` audit event.

## Reproduced incident

The targeted live read at `2026-07-30T17:02Z` reproduced the false-positive
boundary using packet
`pkt-l12-actionable-gap-execution-20260730T163500Z`:

- its durable receipt said `processed`, `admissionStatus=admitted`, and
  `errors=[]`;
- activity rows `4227` through `4234` recorded eight `assign` events at
  `2026-07-30T16:34:53Z` through `16:35:14Z`;
- none of the eight task ids was present in the authoritative canonical board
  after the supervisor projection cycle.

The regression
`test_activity_log_and_projection_only_dispatch_cannot_create_admission`
constructs that same shape: the command exits zero, writes the exact task row
to `ai-status.json`, and appends an `assign` activity row, but does not append
the authoritative journal. The bridge now returns
`invalid_materialization`, persists no admission, and does not mark the packet
seen.

## Owned boundary

- `.orchestrator/supervisor.py`: passes the live runtime binding and publishes
  canonical readback evidence.
- `dev_bridge_inbox.py`: carries the supervisor-issued dispatch environment
  into the trusted dispatcher.
- `dev_bridge_dispatcher.py`: validates projection, governed task readback, and
  journal/checkpoint parity before admission.
- Reliability and supervisor contract tests cover positive authoritative
  materialization, the activity-only false positive, and missing binding.

This task does not change the task-state event schema, `ai_status.py` mutation
semantics, packet signing, replay/admission record formats, or orchestrator
configuration.

## Verification

```text
python3 -m py_compile \
  services/control-plane/bff/assistant/dev_bridge_dispatcher.py \
  services/control-plane/bff/assistant/dev_bridge_inbox.py \
  .orchestrator/supervisor.py \
  services/control-plane/bff/assistant/tests/test_dev_bridge_reliability.py \
  scripts/test_assistant_dev_packet_inbox_supervisor_contract.py
# PASS

.venv-pantheon/bin/python -m pytest -q \
  services/control-plane/bff/assistant/tests/test_dev_bridge.py \
  services/control-plane/bff/assistant/tests/test_dev_bridge_reliability.py \
  services/control-plane/bff/assistant/tests/test_dev_bridge_inbox.py \
  services/control-plane/bff/assistant/tests/test_dev_bridge_dispatch_cli.py \
  services/control-plane/bff/assistant/tests/test_dev_bridge_inbox_cli.py \
  scripts/test_assistant_dev_packet_inbox_supervisor_contract.py \
  scripts/test_verify_task_state_store.py \
  .orchestrator/rewrite/test_task_state_store.py
# 143 passed in 26.02s

.venv-pantheon/bin/python -m pytest -q \
  services/control-plane/bff/assistant/tests \
  scripts/test_assistant_dev_packet_inbox_supervisor_contract.py
# 159 passed in 24.69s
```

## Independent review

Antigravity approved exact PR head
`d8e51bbb744cb69c35e0b98bb2be3c78719880b8` at
`2026-07-30T17:18:44Z` after independently running the 159-test assistant
suite and the 143-test bridge/task-state suite. The review confirmed that the
activity-only negative control rejects a dispatch without a journal commit and
that the canonical readback gate prevents admission until strict
materialization succeeds.

The governed approval bound this manifest as `review_file`. This closeout
record only persists that already-issued decision; it does not change the
reviewed implementation or broaden the approved scope.

# OPS-ASSISTANT-DEV-BRIDGE-DRAIN-001 evidence

Task: Repair supervisor DevTaskPacket drain and bridge command binding  
Owner: Codex  
Reviewer: Codex2  
Evidence captured: 2026-07-27 UTC

## Root cause

The supervisor imports `assistant.dev_bridge_inbox` from its installed code
root before considering the central status root. That part was correct.
However, `dispatch_task_packet()` passed the central status root to
`_dispatch_task()`, and `_dispatch_task()` always executed:

```text
<status-root>/scripts/ai_status.py assign ...
```

The live supervisor configuration keeps its authoritative task-state journal
binding in an external config. The status-root script invocation did not
receive that binding, so it wrote only the `ai-status.json` projection. The
packet could receive a processed receipt and durable bridge admission record
even though the next authoritative projection removed the assignments.

The same subprocess used the signed packet actor as `AI_NAME`. A valid packet
from `codex-root` therefore hit the ordinary auto-worker lease gate:

```text
status command lease required for auto worker: codex-root
```

The bridge is a distinct trusted repo-local service path: signature
verification, constraint validation, replay protection, exact task
materialization validation, admission persistence, and replay marking all
happen before a packet is accepted. It must not borrow or synthesize an
auto-worker lease. The original signed actor must still remain in provenance.

## Live observations before the repair

### Lease rejection

Receipt:
`.orchestrator/assistant-dev-packets/receipts/pkt_pantheon_batch_architecture_20260727_platform_001.json`

- drained at `2026-07-27T14:15:02Z`
- status `failed`
- admission status `not_attempted`
- both task records failed in status-root
  `/home/lupin/pantheon/scripts/ai_status.py`
- error: `status command lease required for auto worker: codex-root`
- receipt SHA-256:
  `55f6f217289289fea77f9260030222abcce23289c07e85097cd2cd7c2126d439`

### File-only admission was absent from the journal

Receipt:
`.orchestrator/assistant-dev-packets/receipts/pkt_pantheon_batch_architecture_20260727_supervisor_autodrain_001.json`

- drained at `2026-07-27T14:23:26Z`
- status `processed`
- admission status `admitted`
- receipt SHA-256:
  `28451b3a2ef4dd81fc2eb493c8cbb2463941cefad798b4fe9a3f09f4bd209341`
- admission SHA-256:
  `e538ecc9ac7a949000e5ab9c8c917285024983033a8a46fbeece506abea20536`

The first authoritative journal event containing
`OPS-ASSISTANT-DEV-BRIDGE-DRAIN-001` is sequence `2357`, committed separately
by `Human/Ops` at `2026-07-27T14:27:33Z`. No bridge assignment event exists at
the receipt's `14:23:26Z` admission time. This is the observed gap between a
successful file-only receipt and canonical task-state durability.

### Pydantic incident is stale in the installed runtime

The active supervisor at capture time is PID `2493424`:

- executable: `/usr/bin/python3.12`
- cwd / installed code root:
  `/home/lupin/pantheon-ci-deploy/dev-root-1434effdc88f`
- `pydantic` version: `2.13.4`
- module:
  `/home/lupin/.local/lib/python3.12/site-packages/pydantic/__init__.py`
- `services/control-plane/bff/requirements.txt` declares `pydantic`
- the fresh supervisor auto-drain at `14:23:26Z` imported the bridge and
  processed one packet with `errorCount=0`

The earlier `ModuleNotFoundError: pydantic` cannot be reproduced in this exact
installed interpreter. No dependency, supervisor process, or live config
change is part of this task.

## Repair

`dev_bridge_dispatcher.py` now:

1. accepts a command runtime environment only when its
   `PANTHEON_STATUS_ROOT` exactly matches the dispatch status root;
2. otherwise reads the supervisor-owned
   `.orchestrator/state.json -> supervisor.task_state_shadow` record to recover
   the live absolute journal binding;
3. derives the command root from the module actually loaded by the installed
   supervisor, never from the central status checkout;
4. invokes that command root's `scripts/ai_status.py` with exact command SHA,
   remote, base ref, central status root, and task-state journal environment;
5. rejects relative or symlinked journal paths;
6. runs the verified bridge mutation as `Human/Ops` after clearing ambient
   auto-worker lease/workspace markers;
7. preserves the signed packet actor unchanged in `TASK_METADATA_JSON` and the
   durable admission record.

Legacy isolated test fixtures without a live task-state binding continue to
use their fixture-local `scripts/ai_status.py`.

No changes were made to `.orchestrator/supervisor.py`,
`scripts/ai_status.py`, the authoritative projection implementation, live
configuration, or the running supervisor process.

## Verification

Fail-first reproduction before production code:

```text
focused bridge tests: 28 passed, 4 failed
```

The failures were the expected `management-ai` versus `Human/Ops` actor
assertions and the missing
`<temporary-status-root>/scripts/ai_status.py` authoritative binding.

Post-repair focused suite:

```text
69 passed
```

Command:

```bash
PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
"$PANTHEON_PY" -m pytest -q \
  services/control-plane/bff/assistant/tests/test_dev_bridge_reliability.py \
  services/control-plane/bff/assistant/tests/test_dev_bridge_inbox.py \
  services/control-plane/bff/assistant/tests/test_dev_bridge_dispatch_cli.py \
  services/control-plane/bff/assistant/tests/test_dev_bridge_inbox_cli.py \
  services/control-plane/bff/assistant/tests/test_dev_bridge.py \
  scripts/test_assistant_dev_packet_inbox_supervisor_contract.py
```

The authoritative regression uses the real governed `scripts/ai_status.py`
against a temporary Git status root and external event log. It queues and
drains a signed packet, verifies a processed receipt and durable admission,
then rewrites the projection to its stale pre-dispatch state and proves the
next journal projection restores the exact task.

The lease regression separately proves:

- verified bridge dispatch runs with `AI_NAME=Human/Ops` and no ambient worker
  markers;
- the packet actor remains `management-ai` in exact bridge provenance;
- an untrusted direct `AI_NAME=Codex assign` without a run lease is still
  rejected by `validate_active_status_command_lease()`.

## Delivery and review

The implementation is on
`task/OPS-ASSISTANT-DEV-BRIDGE-DRAIN-001`. Independent Codex2 review, PR
checks, merge into `dev`, and the final governed `done` transition remain
pending at this evidence revision.

Installing or restarting the live supervisor command runtime is deliberately
not performed here. It belongs to the separately governed
`SUP-COMMAND-RUNTIME-REFRESH-001` sequencing and its active Human/Ops gate.

# ASK-005 Review - Codex R4

Task: ASK-005 - approval / ask SSE event publishing
Owner: Claude
Reviewer: Codex
Reviewed at: 2026-05-16T12:23:00Z
Disposition: changes requested

## Scope Reviewed

- Fix commit `632d72a8` (`ASK-005: extend approval SSE pre-check to durable command_store replay (Codex R3)`)
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_ask005_sse_event_publishing_contract.py`
- `support/evidence/ASK-005/README.md`

## Resolved From R3

- Durable command_store replay is now checked before approval SSE publication.
- The new regression test clears `_FINAL_CONTRACT_IDEMPOTENCY`, replays through durable `command_store`, receives `replayed=True`, and keeps exactly one approval SSE event.
- Focused ASK-005 contract tests pass.

## Blocking Finding

1. Idempotency conflict can still publish an approval SSE event before returning 409.

   `bff_approvals_decide` only treats matching idempotency hashes as replay before publishing. If the same `Idempotency-Key` is reused with a different approval payload, `_is_approval_replay` remains false and the event publish block runs before `_sem_command_response` raises `409 IDEMPOTENCY_CONFLICT`.

   Reviewer probe:

   ```text
   first status: 202
   second status: 409
   approval buffer length: 2
   event types: ['approval.decided', 'approval.decided']
   ```

   Required before approval: make the approval SSE pre-check mirror `_sem_command_response` for both replay and conflict. A reused idempotency key with a different request hash must raise the same 409 path without publishing any additional approval SSE event. Add direct ASK-005 coverage that first accepts one decision, then retries the same key with a different decision payload and asserts the response is 409 and the approval SSE buffer remains at exactly one event.

## Verification

Reviewer commands:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_ask005_sse_event_publishing_contract.py -q
# 11 passed in 10.63s

PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import os, sys, uuid
os.environ.setdefault('PANTHEON_BFF_AUTH_STUB', 'true')
os.environ.setdefault('PANTHEON_BFF_AUTH_MODE', 'stub')
sys.path.insert(0, 'services/control-plane/bff')
from fastapi.testclient import TestClient
import main as bff_main
bff_main._sse_buffers['approval'].clear()
bff_main._sse_subscribers['approval'].clear()
bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
client = TestClient(bff_main.app)
idem = 'ask005-conflict-' + uuid.uuid4().hex[:12]
headers = {'Authorization': 'Bearer ask005-approver:approver', 'Idempotency-Key': idem}
first = client.post('/bff/approvals/appr-dec-c5a9f11e/decide', json={'decision': 'approve'}, headers=headers)
second = client.post('/bff/approvals/appr-dec-c5a9f11e/decide', json={'decision': 'reject', 'rejection_reason': 'changed'}, headers=headers)
print({'first': first.status_code, 'second': second.status_code, 'events': len(bff_main._sse_buffers['approval']), 'types': [event['type'] for _, event in bff_main._sse_buffers['approval']]})
PY
# {'first': 202, 'second': 409, 'events': 2, 'types': ['approval.decided', 'approval.decided']}

git diff --check -- services/control-plane/bff/main.py \
  services/control-plane/bff/test_ask005_sse_event_publishing_contract.py \
  support/evidence/ASK-005/README.md
# passed
```

The R3 durable replay fix passes, but the idempotency conflict side-effect path still blocks approval.

## Decision

Changes requested. Return ASK-005 to Claude for a targeted idempotency-conflict pre-check and regression test.

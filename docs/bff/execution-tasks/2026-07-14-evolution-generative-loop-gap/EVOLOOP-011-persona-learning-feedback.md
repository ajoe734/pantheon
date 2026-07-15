# EVOLOOP-011 — Persona Learning Feedback

Status: ready for PR review  
Owner: Antigravity  
Reviewer: Claude  
Target branch: `task/EVOLOOP-011`

## Problem & Requirements
During active development, we identified that the "learning" (`學習`) stage on the persona side was a critical gap. Persona agents could not reference executed decision outcomes or historical incident postmortem summaries in subsequent OODA cycles because this feedback was not written back to the persona's memory plane.

The requirements for `EVOLOOP-011` are:
1. **Outcome Integration**: Map and flow executed evolution decisions and published postmortem summaries into persona memory.
2. **Fail-Closed**: Do not write a memory entry if there is no outcome (e.g. if the decision lacks an execution result or an outcome summary).
3. **Idempotence**: Multiple write attempts of the same decision or postmortem must not create duplicate memories.
4. **Agent materialization**: Retrieve and sync memory back into the OpenClaw agent's `MEMORY.md` file so the persona can cite real outcomes.

---

## Architecture & Implementation

We introduced a learning feedback bridge that acts as the synchronizing connector between the evolution/postmortems services and the canonical memory plane.

### 1. Learning Feedback Bridge (`services/persona/learning_feedback_bridge.py`)
- Resolves HTTP service endpoints or falls back to local data directories/files (ideal for offline tests).
- Filters for executed decisions with a valid `execution_result` carrying a non-empty `outcome_summary`.
- Filters for published postmortems with a valid `root_cause`.
- Normalizes and posts payloads to `/api/memory/writebacks/learn-feedback` on the memory service.
- Triggers the OpenClaw sync script (`openclaw-sync-persona-agents.py`) after successful writes to materialize entries into the agent workspaces.

### 2. Idempotency & Validation (Leveraged from memory service)
- `write_learn_feedback` generates a stable UUIDv5 hash based on `source_event_type` and `source_event_id`.
- This ensures that duplicate payloads result in `created: False` responses rather than double-inserting records.

---

## Verification Evidence

### Local Test Execution
We created a comprehensive E2E test suite under [test_evoloop_011_learning_feedback.py](file:///tmp/pantheon-worker-worktrees/pantheon/evoloop-011/tests/e2e/test_evoloop_011_learning_feedback.py) which verifies:
- Parser logic mapping `persona_capital_binding_id` to `persona_id`.
- Fail-closed behavior (decisions/postmortems without completed outcome do not create memory entries).
- Idempotency checks (resubmissions return `created: False`).
- OpenClaw materialization (`MEMORY.md` output contains correct citations and text fragments).

```bash
python3 -m pytest tests/e2e/test_evoloop_011_learning_feedback.py
```

**Output:**
```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0
rootdir: /tmp/pantheon-worker-worktrees/pantheon/evoloop-011
configfile: pytest.ini
plugins: asyncio-1.4.0, anyio-4.13.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 2 items                                                              

tests/e2e/test_evoloop_011_learning_feedback.py ..                       [100%]

============================== 2 passed in 0.50s ===============================
```

---

## File Lineage & Scope

- **New files**:
  - [services/persona/learning_feedback_bridge.py](file:///tmp/pantheon-worker-worktrees/pantheon/evoloop-011/services/persona/learning_feedback_bridge.py) (The bridge daemon and HTTP client connector).
  - [tests/e2e/test_evoloop_011_learning_feedback.py](file:///tmp/pantheon-worker-worktrees/pantheon/evoloop-011/tests/e2e/test_evoloop_011_learning_feedback.py) (The end-to-end integration and materialization verifier).
- **Target documentation**:
  - [docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-011-persona-learning-feedback.md](file:///tmp/pantheon-worker-worktrees/pantheon/evoloop-011/docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-011-persona-learning-feedback.md) (This file).

# EVOLOOP-004 — Research Plane Produces Evolutionary Artifact v2

Status: reviewer-approved implementation closeout

Owner: Antigravity  
Reviewer: Claude  
Target branch: `dev`  

## Outcome

`EVOLOOP-004` implements the generative half of the evolution loop in the research plane. When an approved evolution decision (e.g., `decision-evoloop-004`) of type `retrain` is executed, the evolution plane dispatches a research task (work item) to the `research-orchestrator`. 

The `research-orchestrator` schedules an offline job executed by the `training-session`/`optimizer` component, which performs a minimal retrain (parameter mutation) on the parent strategy artifact (v1). The resulting mutated strategy artifact (v2) is registered back into the `registry` with a new version (`1.1.0`), a new artifact ID (`artifact-tw-session-momentum-v2`), a distinct parameter delta (e.g., modified `lookback_bars` or `momentum_threshold`), and a complete lineage tracing back to the parent artifact, the triggering decision, the research work item, and the training session ID.

## Core Component Implementation

The changes are spanning across these four main files, which have been successfully merged into `dev`:

### 1. Evolution Plane Dispatch (`services/evolution/main.py`)
- Wire `/api/evolution/proposals/{decision_id}/execute` to trigger the actual dispatch worker flow.
- Instantiates a background task to POST the work item to the research plane's `/api/research/runs` endpoint.
- Marks the proposal state as `executed` with the dispatched `work_item_id` and `run_id` once the research plane accepts the request.

### 2. Research Plane Orchestration (`services/research/main.py`)
- Sets up endpoints to receive and enqueue runs mapped to evolution decisions.
- Dispatches tasks to the training session, tracking execution state (`queued` -> `running` -> `completed` / `failed`).
- Extracts mutation surface and parameters from the strategy registry to feed the training session.

### 3. Training & Optimization (`services/training-session/main.py`)
- Simulates or executes a minimal parameter mutation on the strategy's mutable controls (e.g., incrementing `lookback_bars` or shifting `momentum_threshold`).
- Computes the new parameter set and compiles the lineage metadata containing:
  - `parent_registry_ids`: `["artifact-tw-session-momentum-v1"]`
  - `source_run_ids`: `[decision_id, work_item_id, session_id]`
- Registers the new v2 artifact (`artifact-tw-session-momentum-v2`) in the registry.

### 4. Integration Test (`tests/e2e/test_evoloop_004_generative_loop.py`)
- Validates the entire end-to-end integration:
  - Parent artifact (v1) verification.
  - Proposal creation, review approval, and execution in the evolution service.
  - Background retrain execution in the research plane.
  - Traceability check: work item links to `decision_id`.
  - Mutated artifact (v2) verification: checks that parameters have a real difference from v1.
  - Lineage checks: verifies `parent_registry_ids` and `source_run_ids` contain the expected keys.

---

## Verification Evidence

### Local Test Execution
The integration test suite was run locally and passed successfully:

```bash
python3 -m pytest tests/e2e/test_evoloop_004_generative_loop.py
```

**Output Snippet:**
```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0
rootdir: /tmp/pantheon-worker-worktrees/pantheon/evoloop-004
configfile: pytest.ini
plugins: asyncio-1.4.0, anyio-4.13.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collecting 0 items                                                             collected 1 item                                                               

tests/e2e/test_evoloop_004_generative_loop.py .                          [100%]

======================== 1 passed, 4 warnings in 2.10s =========================
```

---

## Closeout Metadata

- **Pull Request**: [#3649](https://github.com/ajoe734/pantheon/pull/3649)
- **State**: `MERGED`
- **Merged At**: `2026-07-14T10:03:46Z`
- **CI Checks**:
  - **Commit trailers**: `SUCCESS`
  - **Runtime mirror guard**: `SUCCESS`
  - **Smoke acceptance**: `SUCCESS`
  - **Orchestrator Sync**: `SUCCESS`

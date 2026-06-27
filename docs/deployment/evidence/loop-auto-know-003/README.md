# LOOP-AUTO-KNOW-003 Evidence

Task: `LOOP-AUTO-KNOW-003` - Add persona teaching async preview and eval worker

## Delivered Surface

- `services/training-session/main.py`
  - Added durable async preview job API.
  - Emits `preview_requested` and `preview_result` teaching events.
  - Produces `evaluation_proof` records with governance gate state.
  - Blocks commit unless the replay candidate has completed eval proof.
  - Copies eval proof and gate state into commit artifacts and lineage audit.
- `services/training-session/preview_eval_worker.py`
  - Polls queued preview jobs and invokes the service run endpoint.
  - Duplicate ticks are idempotent because completed jobs replay without a new event.
- `docker-compose.yml`
  - Adds opt-in `training-session-preview-worker` profile with restart policy.
- `services/control-plane/persona/contract.md`
  - Documents that persona policy-affecting teaching commits require training-session eval proof.

## Acceptance Mapping

| Acceptance | Evidence |
|---|---|
| Teaching sessions run async preview and evaluation | `POST /preview-jobs`, `GET /preview-jobs`, `POST /preview-jobs/{job_id}/run`, and `preview_eval_worker.run_tick()` tests |
| Persona-affecting commit is blocked without eval proof | `test_commit_rejects_persona_patch_without_eval_proof` |
| Commit records lineage audit and governance gate state | Commit tests assert `evaluation_proof_ref`, `evaluation_governance_gate_state`, and `lineage_audit` artifacts |

## Verification

```bash
python3 -m pytest services/training-session/tests/test_http_service.py services/training-session/tests/test_compose_activation.py services/training-session/tests/test_preview_eval_worker.py services/training-session/test_rapid_eval_integration.py services/training-session/test_policy_lineage.py -q
```

Result: `15 passed in 4.99s`.

```bash
python3 -m pytest services/training-session -q
```

Result: `29 passed in 9.23s`.

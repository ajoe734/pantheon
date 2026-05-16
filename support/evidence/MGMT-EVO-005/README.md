# MGMT-EVO-005 Rollback / Freeze Follow-Through

Scope:

- Builds a local-only follow-through packet for approved high-risk freeze
  decisions.
- Traces the deployment-plane `freeze_stage` command and runtime-plane
  `pause_then_replace` rollback companion without collapsing them into one
  action.
- Replays `RuntimeManagerService.evolution_freeze()` and
  `RuntimeManagerService.rollback()` against in-memory stores only.
- Records safety assertions that no broker order, live execution, or capital
  binding mutation was performed.

Focused verification:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_evolution_followthrough_packet.py --json-out support/evidence/MGMT-EVO-005/rollback-freeze-followthrough.json
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest scripts/test_run_evolution_followthrough_packet.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/run_evolution_followthrough_packet.py scripts/test_run_evolution_followthrough_packet.py
```

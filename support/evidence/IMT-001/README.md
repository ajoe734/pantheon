# IMT-001 Evidence

## Scope

Implemented a schema-backed `TraderTrajectory` contract under
`services/research/imitation/`.

The contract records human trader / trainer traces as research-only imitation
source data. It preserves durable `storage_ref`, `schema_version`, lineage, and
no-order rationale, while explicitly preventing direct live execution influence.

## Deliverables

- `services/research/imitation/trader_trajectory.schema.json` - Draft-7
  `TraderTrajectory` schema.
- `services/research/imitation/trajectory_models.py` - frozen dataclasses,
  payload validation, and conversion into the existing dataset-builder session
  shape.
- `services/research/imitation/test_trajectory_models.py` - focused schema,
  governance, lineage, storage, no-order, and dataset-builder compatibility
  tests.
- `services/research/imitation/__init__.py` - package exports for the IMT-001
  contract.

## Governance Invariants

| Invariant | Enforcement |
|---|---|
| Human-only learning signal | `actor_role` is limited to `operator` / `approver`. |
| Training-state boundary | target `promotion_state` is limited to `candidate` / `paper`. |
| Durable source trace | `storage_ref` requires backend, path, and `sha256:<64 hex>` checksum. |
| Lineage required | `lineage.source_trace_refs` must contain at least one source ref. |
| No-order rationale | top-level `decision=no_order` and step-level `action=no_order` require `no_order_reason`. |
| No live authority | `governance.research_only=true` and `direct_live_influence=false` are required. |

## Verification

```bash
python3 -m py_compile services/research/imitation/trajectory_models.py services/research/imitation/test_trajectory_models.py
python3 -m json.tool services/research/imitation/trader_trajectory.schema.json
python3 - <<'PY'
import sys
sys.path.insert(0, 'services/research')
import imitation
assert imitation.TraderTrajectory.__name__ == 'TraderTrajectory'
assert imitation.load_trader_trajectory_schema()['title'] == 'TraderTrajectory'
PY
python3 -m pytest services/research/imitation/test_trajectory_models.py -q
python3 -m pytest services/research/imitation -q
```

Results:

- py_compile passed
- JSON schema parses cleanly
- package export smoke passed
- `test_trajectory_models.py`: 10 passed
- `services/research/imitation`: 51 passed

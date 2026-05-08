# BFF-LUV-GAP-001 - Execute-Plans Contract Registry

Priority: P0

Area: BFF route inventory, contract tests, supervisor handoff

## Goal

Create a durable Pantheon-side registry for the `execute-plans` BFF route surface so future work can prove which Lovable contract routes are implemented, intentionally deferred, or superseded.

## Contract Inputs

- `execute-plans/.lovable/spec/v2/...Part_06...BFF_API_Contract...`
- `execute-plans/.lovable/spec/v4/pack-d/*BFF*`, `*SSE*`, `*Session*`
- `execute-plans/.lovable/spec/v5/*`
- `execute-plans/.lovable/feedback/2026-05-07-final/*`
- `execute-plans/src/**` direct `/bff` references
- `execute-plans/README.md`

## Scope

1. Add a checked-in route matrix under `docs/bff/` or `services/control-plane/bff/contract_snapshots/`.
2. Normalize dynamic route params across `:id`, `{id}`, and FastAPI `{param}` forms.
3. Treat dynamic Pantheon routes such as `/bff/mcp-tools/{tool_id}/{action}` as covering concrete action paths only when tests prove `grant`, `revoke`, `disable`, and `test` work.
4. Add a focused route-surface test that reports missing routes by family without blocking unrelated existing BFF tests.
5. Mark every endpoint as one of:
   - implemented
   - implemented_by_alias
   - missing
   - superseded_with_reason
   - deferred_with_task

## Delivered Artifacts

- Route matrix: `services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json`
- Coverage/report helper: `services/control-plane/bff/contract_snapshots/execute_plans_bff_contract.py`
- Reviewer report command: `python3 services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py`
- Focused tests: `services/control-plane/bff/test_execute_plans_contract_registry.py`

## Registry Semantics

- `implemented` rows must exist in the current FastAPI route table.
- `implemented_by_alias` rows must name a live `covered_by` route and a focused proof test.
- `missing` and `deferred_with_task` rows must map to `BFF-LUV-GAP-002..012`.
- `superseded_with_reason` rows must carry an explicit reason and are not treated as open route gaps.

## Acceptance Criteria

- A reviewer can run one command and see the current `execute-plans` BFF coverage matrix.
- Existing final-contract routes stay green: `/health`, `/bff/actions`, `/bff/approvals`, `/bff/v5/interventions`, `/bff/v5/interventions/{id}/remediate`.
- Missing routes map to `BFF-LUV-GAP-002..012`.
- Full BFF test suite still passes.

## Verification

```bash
python3 services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py
python3 -m pytest services/control-plane/bff/test_execute_plans_contract_registry.py -q
python3 -m pytest services/control-plane/bff -q
```

## Revalidation - 2026-05-08T23:18:20Z

`BFF-LUV-GAP-001-UNBLOCK` reran the registry report and BFF verification after
`BFF-LUV-GAP-003` was archived done. The previous blocker that named in-progress
capital/ranking/rebalance failures is stale.

- `python3 services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py` - passed; 178 registry entries, no implemented rows missing from the live FastAPI route table.
- `python3 -m pytest services/control-plane/bff/test_execute_plans_contract_registry.py -q` - 5 passed.
- `python3 -m pytest services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py -q` - 24 passed.
- `python3 -m pytest services/control-plane/bff -q` - 552 passed, 48 warnings.

The full-suite rerun initially exposed service-backed read tests merging seeded
local snapshot records into explicit service stores. The narrow fix keeps local
overlay records reserved for datasets written through the current store, while
preserving task-created capital/ranking/rebalance read-through behavior.

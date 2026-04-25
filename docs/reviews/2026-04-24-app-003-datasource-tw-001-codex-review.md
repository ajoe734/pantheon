# APP-003-DATASOURCE-TW-001 Review

Date: 2026-04-24
Reviewer: Codex
Task: `APP-003-DATASOURCE-TW-001`
Owner: `Codex2`
Disposition: approved

## Scope Reviewed

- `services/execution/shioaji_adapter.py`
- `services/execution/lean_runtime/symbol_parser.py`
- `services/execution/test_shioaji_adapter.py`
- `services/execution/test_ibkr_adapter.py`
- `services/execution/lean_runtime/test_signal_consumer.py`
- `services/data-plane/taiwan_reference.py`
- `services/data-plane/tests/test_data_plane_schemas.py`
- `services/research/adapters/taiwan_market_client.py`
- `services/research/adapters/test_adapters.py`
- `DATA_SOURCE_SCOPE_MATRIX.md`

## Findings

No blocking reviewer findings in the Taiwan datasource slice.

## Verification

Executed locally:

```bash
python3 -m unittest services.execution.test_shioaji_adapter services.execution.test_ibkr_adapter services.execution.lean_runtime.test_signal_consumer
python3 -m unittest services.data-plane.tests.test_data_plane_schemas
python3 services/data-plane/smoke_test.py
cd services/research/adapters && python3 -m unittest discover -s . -p 'test_*.py' -v
```

Result:

- `services.execution.test_shioaji_adapter`, `services.execution.test_ibkr_adapter`, and `services.execution.lean_runtime.test_signal_consumer`: passed (`20` tests total; includes the Taiwan parser regression and Shioaji boundary coverage)
- `services.data-plane.tests.test_data_plane_schemas`: passed (`44` tests)
- `services/data-plane/smoke_test.py`: passed (`47 / 47` checks)
- `services/research/adapters` discover suite: passed (`20` tests)

## Notes

- Approval includes the review correction that removes the invalid Taiwan → `Market.HKFE` fallback from the LEAN symbol parser. Taiwan venue symbols now remain on the Shioaji adapter boundary and are rejected by the LEAN parser path.
- `TWSE OpenAPI`, `TPEx E-Data`, and `MOPS` remain official-reference truth, while `TEJ API` remains a governed `research_grade` vendor. That boundary is now aligned across execution notes, adapter metadata, and `DATA_SOURCE_SCOPE_MATRIX.md`.

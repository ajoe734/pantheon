# BG-001 Review Approval

Reviewer: Codex
Date: 2026-04-13
Task: `BG-001` - Formalize security master, contract master, market calendar, and dataset lineage objects

## Verdict

Approved after reviewer cleanup.

## Reviewer cleanup completed

1. Aligned `MarketCalendarSession` schema with the documented holiday-session rule.
   Holiday sessions now permit empty `session_open` / `session_close`, while non-holiday sessions still require valid `HH:MM:SS` values.

2. Aligned model validation with schema enums.
   `SecurityMaster.asset_type`, `SecurityMaster.listing_status`, `ContractMaster.contract_type`, `ContractMaster.option_right`, `ContractMaster.settlement_type`, `ContractMaster.margin_type`, `RawDataset.source_class`, and `NormalizedDataset.available_time_policy` are now checked explicitly instead of only checking presence.

3. Fixed timestamp defaults on `from_dict`.
   Missing `created_at` / `updated_at` values no longer collapse to `None`; models now emit timezone-aware UTC timestamps by default.

4. Tightened lineage-array schema coverage.
   `instrument_scope` and `DatasetVersion` lineage arrays now require at least one entry, matching the model contract.

5. Upgraded verification coverage.
   Unit tests and smoke tests now validate serialized payloads against JSON Schema with `jsonschema` format checking, so model/schema drift is caught automatically.

## Verification

- `python3 -m unittest discover -s services/data-plane/tests -p 'test_*.py' -v`
- `python3 services/data-plane/smoke_test.py`
- `python3 -m py_compile services/data-plane/models/*.py services/data-plane/tests/*.py services/data-plane/smoke_test.py`

## Result

`BG-001` now has a coherent contract across Python models, generated schemas, README guidance, and automated verification. It is ready to move from `review` to `review_approved`.

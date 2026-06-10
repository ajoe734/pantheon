# OPS-WAVE-005-V2: Evidence Retention Policy — Review Notes

Reviewer: Claude
Reviewed at: 2026-05-19

## Scope

- `scripts/evidence_retention_policy.py`
- `tests/orchestrator/test_evidence_retention_policy.py`

## Verification

```
pytest tests/orchestrator/test_evidence_retention_policy.py -q
3 passed in 0.37s

python3 -m py_compile scripts/evidence_retention_policy.py
OK
```

## Findings

### Part H5 Protected Signal Classification

`PROTECTED_SIGNAL_RULES` correctly defines regex patterns for all five protected
categories: `live`, `canary`, `human_gate`, `rollback`, and `postmortem`. Content
scanning reaches both path components and up to 64 KB / 32 files per evidence packet,
so signals embedded in JSON payloads or markdown bodies are detected.

### Never-Delete Invariant

Evidence packets are always classified with `action=keep` regardless of age. The
`execute()` function only acts on `ACTION_ARCHIVE` items and additionally enforces
that non-sidecar entries are not executable (returning `skipped` with a non-zero exit
code if such an item somehow reaches execution). This provides a double guard.

Sidecar archival uses `DISABLED_DELETE_AFTER_DAYS = 1_000_000_000` when constructing
the underlying `sidecar_cleanup.RetentionPolicy`, ensuring the wrapped `sidecar_cleanup`
module can never issue a delete through this policy layer.

### Sidecar Archival

Eligible sidecars (by task archive age) are correctly identified and moved to
`support/sidecars/archived/`. Collision-safe naming via `_unique_destination` prevents
overwrites when the same sidecar task id appears multiple times.

### CLI Interface

`--execute` flag gates live archival; default is dry-run. JSON output includes counts
for all retention classes, enabling downstream audit tooling.

### Test Coverage

All three tests pass:
1. `test_classifies_part_h5_protected_evidence_signals` — live/canary/postmortem detection
2. `test_execute_archives_eligible_sidecars_but_never_modifies_evidence` — execute moves sidecars, leaves evidence in place
3. `test_cli_dry_run_reports_protected_evidence_and_sidecar_archive` — CLI dry-run output shape

## Verdict

Implementation is correct and complete. Approved for owner closeout.

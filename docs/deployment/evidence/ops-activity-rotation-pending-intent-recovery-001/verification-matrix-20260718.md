# Exact-head owner verification matrix — 2026-07-18

Task: `OPS-ACTIVITY-ROTATION-PENDING-INTENT-RECOVERY-001`

Validated code base: `86029affc0400f57367abeee2fdc6356c3af9131`

All test status roots were newly created under `/tmp/oparpir-*`, outside the
central status root. The inherited runner, workspace, heartbeat, and run-id
variables were removed. These commands did not execute the live recovery or
write any central activity artifact.

## Commands and aggregate results

| ID | Command | Result |
| --- | --- | --- |
| C1 | `env -u ORCH_RUN_ID -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH PYTHONDONTWRITEBYTECODE=1 PANTHEON_STATUS_ROOT=/tmp/oparpir-final-pending.<random>/status python3 .orchestrator/test_activity_pending_intent_recovery.py -q` | PASS, 37 tests |
| C2 | same isolated environment, `python3 .orchestrator/test_common.py -q` | PASS, 90 tests |
| C3 | same isolated environment, `python3 -m unittest -q scripts.test_activity_audit_logical_inventory` | PASS, 25 tests; 1 explicit opt-in central integration skip |
| C4 | same isolated environment, `python3 .orchestrator/test_activity_pending_intent_recovery.py TamperMatrixTests CrashRetryTests ResolutionReaderContractTests -v` | PASS, 13 test methods; all subtest rows below passed |
| C5 | same isolated environment, `python3 .orchestrator/test_common.py LogicalActivityReaderTests.test_lineage_tamper_and_rollback_fail_closed LogicalActivityReaderTests.test_active_lineage_head_control_tamper_failures LogicalActivityReaderTests.test_content_lineage_archive_and_row_tamper_failures LogicalActivityReaderTests.test_active_lineage_head_stale_tail_and_newest_row_rollback_fail LogicalActivityReaderTests.test_newest_row_and_archive_rollback_fails_for_both_keep_lines LogicalActivityReaderTests.test_active_control_field_level_tamper_matrix_fails_closed LogicalActivityReaderTests.test_active_control_retained_tail_truncation_fails_closed -v` | PASS, 7 test methods; all subtest rows below passed |
| C6 | `git diff --check origin/dev...HEAD` | PASS |

Passing `unittest` methods fail if any listed `subTest` row fails. The tables
therefore expand every fault/tamper row that was executed by C1/C4/C5 rather
than treating one aggregate suite count as the row-level result.

## Incident relationship and execution rows

| Test method | Row | Command | Result |
| --- | --- | --- | --- |
| `test_execute_resolves_exact_incident` | exact schema-v1 incident; idempotent second run; stale fresh pin rejected | C1 | PASS |
| `test_execute_supports_zero_one_many_appends_and_zero_overlap` | post-rotation append count 0, overlap 10 | C1 | PASS |
| same | append count 1, overlap 10 | C1 | PASS |
| same | append count 12, overlap 10 | C1 | PASS |
| same | append count 4, zero overlap | C1 | PASS |
| `test_exact_duplicate_superseding_archive_is_safe` | exact duplicate relation | C1 | PASS |
| `test_superseding_archive_relationship_variants_fail_closed` | `prefix_of_source` | C1 | PASS |
| same | `one_byte_differs` | C1 | PASS |
| same | `overlap_without_newline` | C1 | PASS |
| same | `independent` | C1 | PASS |
| same | `two_candidates` | C1 | PASS |
| `test_dry_run_allows_append_since_pin` | append during dry-run | C1 | PASS |
| `test_execute_stops_on_append_since_pin` | append between pin and execute | C1 | PASS |
| `test_execute_stops_on_changed_active_inode` | active inode replacement | C1 | PASS |
| `test_partial_final_active_line_fails_closed` | partial active final line | C1 | PASS |
| `test_post_rotation_suffix_repeating_superseded_events_fails` | repeated superseded event | C1 | PASS |
| `test_competing_recovery_is_serialized_by_exclusive_lock` | competing exclusive recovery | C1 | PASS |

## Incident artifact tamper rows

| Test method | Row | Command | Result |
| --- | --- | --- | --- |
| `test_incident_artifact_tampers_fail_closed` | `intent_field` | C4 | PASS |
| same | `intent_transaction_id` | C4 | PASS |
| same | `stage_archive_payload` | C4 | PASS |
| same | `stage_tail_bytes` | C4 | PASS |
| same | `stage_tail_truncated` | C4 | PASS |
| same | `installed_gzip_conflict` | C4 | PASS |
| same | `installed_missing` | C4 | PASS |
| same | `stage_archive_missing` | C4 | PASS |
| same | `stage_tail_missing` | C4 | PASS |
| same | `stage_archive_partial_gzip` | C4 | PASS |
| same | `active_overlap_tamper` | C4 | PASS |
| same | `extra_content_archive` | C4 | PASS |
| same | `unknown_archive_name` | C4 | PASS |
| same | `lineage_present` | C4 | PASS |
| `test_symlinked_incident_leaves_fail_closed` | pending intent symlink | C4 | PASS |
| same | staged tail symlink | C4 | PASS |
| same | installed archive symlink | C4 | PASS |
| same | superseding archive symlink | C4 | PASS |
| same | active log symlink | C4 | PASS |
| `test_stale_pin_against_mutated_manifest_fails` | manifest changed after digest pin | C4 | PASS |

## Crash/retry and resolution-contract rows

| Test method | Row | Command | Result |
| --- | --- | --- | --- |
| `test_sigkill_at_each_publish_step_converges_on_retry` | `pin-recheck` | C4 | PASS |
| same | `preserve` | C4 | PASS |
| same | `resolution` | C4 | PASS |
| same | `resolution-readback` | C4 | PASS |
| same | `unlink-intent` | C4 | PASS |
| same | `unlink-stage` | C4 | PASS |
| `test_resolution_row_field_tampers_fail_closed` | `sequence` | C4 | PASS |
| same | `resolved_transaction_id` | C4 | PASS |
| same | `archive_gzip_sha256` | C4 | PASS |
| same | `archive_payload_sha256` | C4 | PASS |
| same | `superseding_gzip_sha256` | C4 | PASS |
| same | `superseding_payload_sha256` | C4 | PASS |
| same | `source_byte_count` | C4 | PASS |
| same | `active_line_count` | C4 | PASS |
| same | `previous_resolutions_sha256` | C4 | PASS |
| same | `writer_guard_attestation` | C4 | PASS |
| same | `resolution_id` | C4 | PASS |
| same | `inventory_sha256` | C4 | PASS |
| `test_resolutions_file_truncation_and_blank_rows_fail_closed` | truncated row | C4 | PASS |
| same | blank row | C4 | PASS |
| same | empty file | C4 | PASS |
| `test_exact_superseded_archive_backup_is_accepted` | byte-exact backup accepted; changed backup rejected | C4 | PASS |
| `test_missing_superseded_archive_and_backup_fails_closed` | archive and backup absent | C4 | PASS |
| `test_symlinked_superseded_archive_backup_fails_closed` | backup symlink | C4 | PASS |
| `test_superseding_archive_tamper_fails_closed` | superseding gzip tamper | C4 | PASS |
| `test_symlinked_resolutions_file_fails_closed` | resolutions file symlink | C4 | PASS |
| `test_rotation_rejects_publishing_onto_superseded_archive_path` | archive-path reuse | C4 | PASS |
| `test_superseded_archive_registered_in_lineage_fails_closed` | lineage/resolution conflict | C4 | PASS |

## Schema-v2 lineage and active-control rows

| Test method | Row | Command | Result |
| --- | --- | --- | --- |
| `test_lineage_tamper_and_rollback_fail_closed` | lineage/archive removed, keep-lines 0 | C5 | PASS |
| same | lineage/archive removed, keep-lines 2 | C5 | PASS |
| `test_active_lineage_head_control_tamper_failures` | missing active control | C5 | PASS |
| `test_content_lineage_archive_and_row_tamper_failures` | missing newest archive | C5 | PASS |
| same | modified gzip | C5 | PASS |
| same | sequence gap | C5 | PASS |
| same | predecessor fork | C5 | PASS |
| same | duplicate transaction | C5 | PASS |
| same | duplicate archive | C5 | PASS |
| `test_active_lineage_head_stale_tail_and_newest_row_rollback_fail` | stale control | C5 | PASS |
| same | retained-tail digest change | C5 | PASS |
| same | newest row plus archive rollback | C5 | PASS |
| `test_newest_row_and_archive_rollback_fails_for_both_keep_lines` | multi-row rollback, keep-lines 0 | C5 | PASS |
| same | multi-row rollback, keep-lines 1000 | C5 | PASS |
| `test_active_control_field_level_tamper_matrix_fails_closed` | `archive_payload_sha256` | C5 | PASS |
| same | `archive_gzip_sha256` | C5 | PASS |
| same | `lineage_sha256` | C5 | PASS |
| same | `lineage_row_sha256` | C5 | PASS |
| same | `tail_sha256` | C5 | PASS |
| same | `sequence` | C5 | PASS |
| same | `tail_byte_count` | C5 | PASS |
| same | `tail_line_count` | C5 | PASS |
| same | `transaction_id` | C5 | PASS |
| same | `log_name` | C5 | PASS |
| same | `schema_version` | C5 | PASS |
| `test_active_control_retained_tail_truncation_fails_closed` | retained-tail truncation | C5 | PASS |

## Isolation and claim boundary

The original exact-head evidence includes per-suite `fuser` sampling and a
full `strace -f` pass with zero candidate test PIDs opening either central
lock. The current reruns remained repo-external and also passed
`test_isolated_lock_paths_and_no_central_references` in C1.

This matrix proves the implementation fault/tamper contract. It does not turn
the later unproved `0404Z -> 1754Z` legacy boundary into a current-history
zero-missing claim; that stop condition is recorded separately.

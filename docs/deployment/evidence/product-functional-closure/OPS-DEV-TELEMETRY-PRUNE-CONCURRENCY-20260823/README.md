# OPS-DEV-TELEMETRY-PRUNE-CONCURRENCY-20260823 Evidence

## Defect

During `prune_dev_management_ai_telemetry_for_disk`, the previous PFG-DATA-TELEMETRY-PRUNE-20260822 sentinel compared global `canonical_count_before != canonical_count_after` and `canonical_checksum_before != canonical_checksum_after` directly across the entire `public.telemetry_events` table.
When legitimate concurrent appends (INSERTs) into `public.telemetry_events` occurred while the prune transaction was executing, the new rows altered the count and table checksum, triggering a false `canonical telemetry drift detected` failure and aborting valid dev deployments.

## Fix

1. **Concurrency-Safe Historical Preservation with Full-Row Digest**:
   - Captures the exact pre-state set of `public.telemetry_events` with per-row full-row digests (`md5(to_jsonb(r)::text)`) in a session-scoped temp table `_pantheon_canonical_telemetry_pre` before derived table truncation.
   - Enforces that all pre-existing canonical rows are matched post-prune with identical row digests (`canonical_matched_count == canonical_count_before` and `canonical_matched_checksum == canonical_checksum_before`).
   - Allows concurrent appends (`canonical_count_after >= canonical_count_before`) while ensuring pre-existing historical telemetry is strictly preserved from DELETE, UPDATE (including payload, event_type, created_at), or TRUNCATE.

2. **Strict Canonical Mutation Guard**:
   - If any historical row is deleted, modified (e.g. payload JSON, event_type, or timestamp), or if `public.telemetry_events` is truncated (even if masked by concurrent appends), the drift check immediately fails closed with `canonical telemetry drift detected`.

3. **Deterministic Sentinel Emission**:
   - Emits `TELEMETRY_PRUNE_SENTINEL` containing `canonical_matched_count`, `canonical_matched_checksum`, pre/post counts and timestamps, and list of pruned derived tables (`result: preserved`).

4. **Single Prune Authority**:
   - Extended the existing `prune_dev_management_ai_telemetry_for_disk` function in `scripts/deploy_nonprod_vm.sh` with zero new cleanup authorities or duplicate cleanup paths.

## Verification

- `bash -n scripts/deploy_nonprod_vm.sh` (passed)
- `.venv-pantheon/bin/python3 -m pytest -v scripts/test_deploy_nonprod_telemetry_prune.py scripts/test_management_ai_postgres_bootstrap_contract.py` (34 passed)
- `./scripts/deploy_nonprod_vm.sh --environment dev --sha 95a1455e3dc1a275b8d541fd2c432c3971013308 --project-id pantheon-lupin-dev-20260719 --dry-run` (passed)

# Review: SVC-SOURCE-CRAWL-FRONTIER-SCHEDULER

**Reviewer:** Claude  
**Date:** 2026-04-30  
**Status:** APPROVED

## Acceptance Criteria Assessment

### 1. persistent frontier stores queued running done failed and retry state ✅
`CrawlFrontierItem.VALID_STATUSES = {"queued", "running", "done", "failed", "retry"}` is enforced in `__post_init__`. All state transitions (`claim_frontier`, `complete_frontier`, `fail_frontier`, `replay_frontier`) append to the JSONL store. `reload()` replays the full log on startup.

### 2. watermarks backoff and DLQ replay are durable ✅
- `SourceWatermark` is appended to the schedule JSONL store via `update_watermark()`.
- `fail_frontier()` writes `available_at = _utc_after(backoff_seconds)` to the JSONL store — backoff survives restart.
- DLQ replay path (`_replay_source_event`) routes through `store.replay_frontier()` → `claim_frontier()` → `_run_frontier_item()` and updates frontier state durably.
- `test_run_scheduled_frontier_retry_backoff_and_dlq_replay_are_durable` verifies durability via `importlib.reload`.

### 3. robots allowlist max bytes max records and timeout guards are enforced ✅
- `_validate_feed_url()` enforces `http/https/file` scheme and no inline credentials or secret query params.
- `allowed_url_prefixes` is required for `external_feed` and checked at config time and at fetch time (including after HTTP redirects).
- `_assert_robots_allowed()` fetches `robots.txt`, parses per-agent rules, and raises on disallow.
- `max_bytes` ceiling is 10 MB; response is read as `max_bytes + 1` and compared.
- `max_records` ceiling is 1000; external feed record count is checked after fetch.
- `timeout_seconds` must be `> 0` and `<= 30`.
- All guard tests are in `test_fetch_config_validation_rejects_unsafe_urls_secrets_and_overlarge_payloads`.

### 4. scheduler can run as service or bounded smoke worker ✅
- `scheduler_worker.py` supports continuous service (`SOURCE_INGEST_SCHEDULER_MAX_TICKS=0`) and bounded runs (`MAX_TICKS > 0`).
- `scripts/source_ingest_scheduler_once.py` provides a one-shot CLI wrapper.
- `docker-compose.yml` adds `source-ingest-scheduler` service under the `source-ingest-scheduler` profile.
- `/api/source-ingest/run-scheduled` provides the bounded HTTP trigger used by both paths.

### 5. tests cover retry replay and unsafe source rejection ✅
- `test_run_scheduled_frontier_retry_backoff_and_dlq_replay_are_durable`: retry backoff, DLQ replay, frontier durability after reload.
- `test_run_scheduled_honors_bounded_concurrency`: bounded concurrency claim limit.
- `test_fetch_config_validation_rejects_unsafe_urls_secrets_and_overlarge_payloads`: URL allowlist, inline credentials, max_bytes, timeout.
- `test_run_scheduled_skips_not_due_connector`: watermark-based interval enforcement.
- `test_run_scheduled_skips_disabled_connector`: disabled schedule skipped.

## Verification Commands Run
```
python3 -m compileall -q services/source_ingestion scripts/source_ingest_scheduler_once.py  # 0 errors
python3 -m pytest services/source_ingestion -q  # 43 passed
docker compose config --quiet  # valid
```

## Notes
- Implementation is clean, self-contained, and does not widen canonical architecture docs beyond the task scope.
- JSONL append-log pattern is consistent with the rest of the source ingestion service.
- Robots.txt parser handles `*` and specific user-agent blocks correctly.
- No issues found. Returning to Codex for closeout.

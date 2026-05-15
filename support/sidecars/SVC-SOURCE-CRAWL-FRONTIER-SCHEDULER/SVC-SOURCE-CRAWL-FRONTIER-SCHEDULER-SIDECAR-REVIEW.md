# Review Packet: SVC-SOURCE-CRAWL-FRONTIER-SCHEDULER

**Sidecar Kind:** review_packet
**Parent Task:** SVC-SOURCE-CRAWL-FRONTIER-SCHEDULER
**Sidecar ID:** SVC-SOURCE-CRAWL-FRONTIER-SCHEDULER-SIDECAR-REVIEW
**Prepared By:** Codex2
**Sidecar Reviewer:** Codex
**Parent Owner:** Codex
**Parent Reviewer:** Claude
**Prepared At:** 2026-04-30
**Mutates Canonical:** no

This packet is a support artifact only. It summarizes the parent task's current
review evidence for reviewer intake and does not modify L1 canonical policy,
runtime ownership truth, registry/governance behavior, or parent task delivery
state.

## 1. Current Snapshot

| Item | Current truth | Review implication |
|---|---|---|
| Parent task lifecycle | `ai-status.json` records parent status `review`, owner `Codex`, reviewer `Claude`, with a pending Codex -> Claude handoff. | Parent implementation is ready for Claude review; this sidecar should not approve or close the parent. |
| Sidecar lifecycle | `ai-status.json` records this sidecar as `review`, owner `Codex2`, reviewer `Codex`, helper kind `review_packet`. | Sidecar output is limited to this support packet and reviewer handoff. |
| Parent acceptance scope | Persistent crawl frontier; durable watermarks/backoff/DLQ replay; robots/allowlist/max-byte/max-record/timeout guards; service or bounded smoke worker; retry/replay/unsafe source tests. | Reviewer should evaluate whether the evidence below maps cleanly to those criteria. |
| Canonical scope | No L1 canonical truth changes are required by this sidecar. | Do not absorb this packet as architecture authority; parent owner decides what, if anything, belongs in mainline docs later. |

## 2. Acceptance Evidence Matrix

| # | Parent acceptance criterion | Evidence | Sidecar read |
|---|---|---|---|
| 1 | Persistent frontier stores queued, running, done, failed, and retry state | `CrawlFrontierItem` validates those statuses and serializes frontier metadata; `JsonlIngestScheduleStore.reload()` replays `crawl_frontier_item`; `enqueue_frontier()`, `claim_frontier()`, `complete_frontier()`, `fail_frontier()`, and `replay_frontier()` append state transitions. | PASS |
| 2 | Watermarks, backoff, and DLQ replay are durable | `SourceWatermark` remains append/replay persisted; failed frontier items get retry/failed state with `available_at`; DLQ replay calls `_replay_source_event()`, replays the frontier, claims it, and reruns the configured fetch. Tests reload the app and confirm replayed frontier state remains present. | PASS |
| 3 | Robots, allowlist, max bytes, max records, and timeout guards are enforced | `ConfiguredConnectorFetcher` validates URL/prefixes, rejects inline secrets, applies `timeout_seconds`, enforces `max_bytes` and `max_records`, and checks `robots.txt` by default for HTTP(S) feeds. Existing tests cover unallowlisted URL, robots disallow, oversize payload, and too-many-record failures without watermark advance. | PASS |
| 4 | Scheduler can run as service or bounded smoke worker | `POST /api/source-ingest/run-scheduled` accepts `max_concurrency`; `scheduler_worker.py` loops bounded ticks over HTTP; `scripts/source_ingest_scheduler_once.py` runs one tick; `docker-compose.yml` adds a `source-ingest-scheduler` profile that depends on healthy `source-ingest`. | PASS |
| 5 | Tests cover retry, replay, and unsafe source rejection | `test_run_scheduled_frontier_retry_backoff_and_dlq_replay_are_durable()` covers retry, backoff, DLQ replay, and durability; `test_external_http_feed_respects_robots_disallow_and_routes_to_dlq()`, size, record-count, and allowlist tests cover unsafe feed rejection. | PASS |

Sidecar conclusion: the parent implementation appears to satisfy the stated
acceptance criteria based on the current workspace and focused reruns below.

## 3. Implementation Evidence

### 3.1 Persistent Frontier And Watermarks

Primary surfaces:

- `services/source_ingestion/scheduler.py`
- `services/source_ingestion/main.py`

Important implementation points:

- `CrawlFrontierItem` carries `frontier_id`, `connector_id`, status, attempts,
  max attempts, `available_at`, `last_error`, `ingest_run_id`, and timestamps.
- `JsonlIngestScheduleStore` now replays `ingest_run`, `source_watermark`, and
  `crawl_frontier_item` records from the same append-only schedule store.
- Claiming moves queued/retry items to running and increments attempts.
- Completion moves the item to done and attaches the completed ingest run id.
- Failure moves the item to retry with future `available_at`, or failed after
  max attempts, preserving `last_error`.
- Replay moves failed/retry items back to retry under `dlq_replay` trigger type.

### 3.2 Scheduled Execution API

Primary surfaces:

- `PUT /api/source-ingest/connectors/{connector_id}/schedule`
- `GET /api/source-ingest/connectors/{connector_id}/schedule`
- `GET /api/source-ingest/frontier`
- `POST /api/source-ingest/frontier/{frontier_id}/replay`
- `POST /api/source-ingest/run-scheduled`

Execution flow:

1. Schedule config is persisted with `JsonlConnectorScheduleStore`.
2. `run-scheduled` skips disabled/not-due schedules and enqueues due connector
   frontier items.
3. The service claims up to `max_concurrency` due frontier items.
4. Each claimed item runs through configured fetch, scheduler, evidence
   persistence, DLQ/audit persistence, and frontier completion/failure update.
5. Response returns `enqueued`, `claimed`, `ran`, `skipped`, `failed`, and
   summary counters for reviewer/debug visibility.

### 3.3 Fetch Guards

Primary surface:

- `services/source_ingestion/configured.py`

Guard summary:

- URL scheme restricted to `http`, `https`, or `file`.
- HTTP(S) URLs must include a host.
- Inline credentials and known secret query keys are rejected.
- Feed URL and redirects must match configured `allowed_url_prefixes`.
- HTTP(S) feeds respect `robots.txt` by default, with `respect_robots_txt`
  available in connector fetch config.
- Fetch uses configured timeout.
- Response body is bounded by `max_bytes + 1` and rejects oversize payloads.
- External feed records are bounded by `max_records`.

### 3.4 Worker And Compose Wiring

Primary surfaces:

- `services/source_ingestion/scheduler_worker.py`
- `scripts/source_ingest_scheduler_once.py`
- `docker-compose.yml`
- `services/source_ingestion/test_compose_activation.py`

The compose service `source-ingest-scheduler` is behind profile
`source-ingest-scheduler`, calls `python -m services.source_ingestion.scheduler_worker`,
points at `http://source-ingest:8097`, and waits for the source ingest health
check. The one-shot script gives operators and smoke jobs a bounded single-tick
path against an already running service.

## 4. Test And Verification Evidence

Parent handoff from Codex reports:

```bash
python3 -m compileall -q services/source_ingestion scripts/source_ingest_scheduler_once.py
python3 -m pytest services/source_ingestion -q
# 43 passed
docker compose config --quiet
git diff --check
```

This sidecar reran focused checks against the current workspace:

| Command | Result |
|---|---|
| `python3 -m compileall -q services/source_ingestion scripts/source_ingest_scheduler_once.py` | pass |
| `python3 -m pytest services/source_ingestion/tests/test_scheduled_connector.py services/source_ingestion/test_compose_activation.py -q` | `11 passed in 3.61s` |
| `git diff --check -- services/source_ingestion scripts/source_ingest_scheduler_once.py docker-compose.yml` | pass |

Focused test coverage highlights:

- Schedule PUT/GET round trip and schedule replay after reload.
- Due scheduled connector run persists evidence and source records.
- `max_concurrency=1` leaves one frontier item queued, then processes it on the
  next tick.
- Disabled and not-due schedules are skipped.
- Failed scheduled fetch creates retry frontier state, blocks immediate retry
  during backoff, DLQ replay completes the frontier under `dlq_replay`, and
  state survives app reload.
- Compose test verifies scheduler profile, command, env, dependency, and smoke
  script route coverage.

## 5. Reviewer Notes

No blocking findings were identified for this sidecar's scoped purpose.

Items for the parent reviewer to inspect deliberately:

| Area | Why to check | Suggested file |
|---|---|---|
| Append-only JSONL frontier replay | Confirms current-state reduction is correct when multiple transitions for a frontier id exist. | `services/source_ingestion/scheduler.py` |
| Retry semantics | Confirms internal `IngestionScheduler.max_attempts` and frontier-level attempts interact as intended for configured fetch failures. | `services/source_ingestion/scheduler.py`, `services/source_ingestion/main.py` |
| Robots behavior | Confirms default allow/deny handling matches the intended policy for missing/erroring `robots.txt`. | `services/source_ingestion/configured.py` |
| Compose profile | Confirms scheduler service is opt-in and does not change default dev topology unless the profile is enabled. | `docker-compose.yml` |

## 6. Scope Boundary

This sidecar did not edit and should not be used to override:

- L1 canonical architecture or policy files.
- Parent task lifecycle or parent reviewer decision.
- Core registry/governance semantics.
- Runtime code, contracts, or compose configuration.

Out of scope for this sidecar:

- Making source ingestion production-grade beyond the parent slice.
- Promoting JSONL storage to the staging/production backend.
- Changing search indexing behavior.
- Closing or approving the parent task.

## 7. Reviewer Handoff

Suggested disposition for Codex reviewing this sidecar:

1. Verify this file is support-only and does not alter canonical/runtime truth.
2. Spot-check the evidence matrix against the parent implementation and focused
   test output.
3. If accurate, approve this sidecar so Codex2 can finalize the support packet;
   leave parent review/absorption decisions with the parent owner/reviewer flow.

## 8. Owner Closeout

Codex approved this sidecar on 2026-04-30 with the review note that the
support-only packet is scoped correctly, evidence spot-checks match the parent
implementation, and focused verification passed. Codex2 finalized the support
artifact at 2026-04-30T06:30:29Z without changing canonical truth, runtime
code, registry behavior, governance behavior, or parent task state.

Closeout verification rerun:

| Command | Result |
|---|---|
| `python3 -m compileall -q services/source_ingestion scripts/source_ingest_scheduler_once.py` | pass |
| `python3 -m pytest services/source_ingestion/tests/test_scheduled_connector.py services/source_ingestion/test_compose_activation.py -q` | `11 passed in 3.60s` |
| `git diff --check -- support/sidecars/SVC-SOURCE-CRAWL-FRONTIER-SCHEDULER/SVC-SOURCE-CRAWL-FRONTIER-SCHEDULER-SIDECAR-REVIEW.md services/source_ingestion scripts/source_ingest_scheduler_once.py docker-compose.yml` | pass |

## 9. Source Boundary

This packet used the instructed task-scoped context and direct parent evidence:

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/svc_source_crawl_frontier_scheduler_sidecar_review.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `ai-status.json`
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/planning-session.json` direct `rg` check for this task/scheduler terms
- `services/source_ingestion/scheduler.py`
- `services/source_ingestion/main.py`
- `services/source_ingestion/configured.py`
- `services/source_ingestion/scheduler_worker.py`
- `scripts/source_ingest_scheduler_once.py`
- `docker-compose.yml`
- `services/source_ingestion/tests/test_scheduled_connector.py`
- `services/source_ingestion/test_service.py`
- `services/source_ingestion/test_compose_activation.py`

Intentionally not reviewed:

- `current-work.md`
- full `ai-activity-log.jsonl`

Reason: wake-up instructions explicitly said not to scan those global summaries
or historical logs unless the task brief required them.

---

Prepared by Codex2 for the `SVC-SOURCE-CRAWL-FRONTIER-SCHEDULER-SIDECAR-REVIEW`
support slice. This file is support-only and does not modify canonical truth.

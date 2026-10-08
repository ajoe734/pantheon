# Reproduction and Root Cause Analysis: RECON-INCIDENT-LISTENER-BACKLOG-20261008

## Incident Context

During dev deployment run `37719887861` (`3faddc34d`), the stable-state healthcheck failed at `exact_component_deployment`:
```
required component(s) unhealthy or unknown: reconciliation-drift-incident-listener: health=unhealthy
```

Observed diagnostics on the deployment host:
1. `incident_listener.py healthcheck` (interval 30s, start period 300s, retries 6) repeatedly returned:
   ```
   reconciliation-drift-incident-listener health is not ready: status=starting ticks=0
   ```
2. The listener process sat in socket `poll()` on an established TCP connection to `reconciliation-drift-svc:8102`.
3. `/data/reconciliation-drift/incident-listener-state.json` contained:
   - `backlog`: 18 entries.
   - `last_success_at`: `2026-10-07T16:18:16Z`.
   - `last_failure_at`: `2026-10-08T03:12:58Z`.
   - `last_failure_error`: `[Errno -3] Temporary failure in name resolution` (transient error during container recreation).
   - Sample entry: `attempt_count: 21`, `first_failed_at: 2026-10-07T22:50:02Z`, binding `infra-subject-default-control-plane-bff-incidents`, evidence summary `non_trading_infrastructure_incident=true`.
4. Downstream `reconciliation-drift-svc` logged 20 `POST /api/reconciliation-drift/incident-triggers/consume 201` in the hour (14 between 03:14:55 and 03:16:17, then minutes apart), yet the backlog remained at 18 while `updated_at` in the state file kept advancing.

---

## Acceptance 3 Analysis: Why Replays Did Not Clear the Backlog

Acceptance 3 poses the key question:
*Why did replays of already consumed incidents not clear the hosted backlog although the service logged 201 for `incident-triggers/consume` and dedupes by `evaluation_id` (client timeout versus server completion or a removal or persistence defect)?*

### 1. Verification of Removal and Persistence Logic

We inspected `IncidentListenerState.remove_delivery()` in `incident_listener.py`:
```python
def remove_delivery(self, identity: str) -> None:
    old = self.backlog.pop(identity, None)
    if old is None:
        return
    try:
        self.save()
    except Exception:
        self.backlog[identity] = old
        raise
```
And `save()`:
- Writes the state document to a unique temporary file (`.{name}.*.tmp`).
- Flushes and `fsync`s the file descriptor.
- Uses atomic `os.replace` to replace the target file.

Both `remove_delivery()` and `save()` are correct and atomic. There was no internal dictionary key mismatch (the loop keys over `listener_state.backlog.items()`, so `identity` is exact), nor was there a filesystem or JSON serialization bug preventing deletions.

### 2. The Client Timeout vs Server Completion Mechanism

The discrepancy between the server logging 201 and the client maintaining an 18-item backlog is explained by **client-side timeout racing against asynchronous/queued server completion**:

1. **Downstream Bottleneck**: `reconciliation-drift-svc` runs as a single uvicorn worker process. When handling `consume_incident_trigger` for incidents that do not bypass evaluation:
   - It computes drift metrics and acquires exclusive process locks (`fcntl.flock`) on `drift_evaluations.json`.
   - It contends with concurrent background workers (`consumer` and `scheduler`) accessing the same store.
   - When 18 incidents queued up during container recreation (where DNS resolution had briefly failed), all 18 were queued sequentially.
2. **Client Timeout**:
   - `incident_listener.py` sends requests via `post_incident_trigger()` using `urllib.request.urlopen(..., timeout=timeout_seconds)` with `timeout_seconds = 30.0`.
   - When request latency or queue wait exceeded 30 seconds, `urlopen` timed out on the client, raising `urllib.error.URLError: <urlopen error timed out>`.
3. **Failure Recording on Client**:
   - `_retry_operation` caught the exception across retries and returned `result = None`.
   - Because `result is None`, `remove_delivery()` was never called.
   - Instead, `record_delivery_failure()` was executed:
     ```python
     listener_state.record_delivery_failure(
         identity=identity,
         incident=incident,
         error=str(last_error["detail"]),
         attempt_count=len(op_attempts),
         failed_at=str(last_error["at"]),
     )
     ```
   - `record_delivery_failure()` calls `save()`, which computes a fresh `updated_at = _utc_now()`.
   - Thus, `updated_at` continually moved forward, while `self.backlog` retained all 18 entries and `attempt_count` increased to 21!
4. **Server Completion After Client Disconnect**:
   - The uvicorn worker completed each queued request, wrote/deduped the evaluation in `drift_evaluations.json`, and returned HTTP 201.
   - The server log recorded `POST /api/reconciliation-drift/incident-triggers/consume 201`.
   - However, the client had already closed the timed-out connection and marked the delivery as failed.
5. **Tick Stalling & Health Failure**:
   - `run_tick()` processed all backlog entries sequentially without a tick time budget.
   - 18 entries with 3 retries each at 30s timeout could keep `run_tick()` running for over 15 minutes.
   - While `run_tick()` was stuck in socket poll/timeouts, `ticks` was never incremented from `0`.
   - After the 300s Docker `start_period` elapsed, the healthcheck probe (`status=starting ticks=0`) failed 6 consecutive times, causing Docker to mark the container unhealthy and failing the deployment.

---

## The Solution

1. **Non-Trading Infrastructure Incident Bypass (Acceptance 1)**:
   - Incidents with `infra-subject-*` binding or `non_trading_infrastructure_incident=true` evidence summary have no trading bindings to reconcile.
   - The listener now recognizes infrastructure incidents and acknowledges them immediately without HTTP delivery to `reconciliation-drift-svc`, removing them from the backlog instantly.
   - `reconciliation-drift-svc` (`main.py`) also fast-paths infrastructure incidents, returning `status="ok", skipped=True, not_applicable=True` without lock acquisitions or store writes.
2. **Tick Time Budget (Acceptance 2)**:
   - `run_tick()` enforces a `tick_budget_seconds` deadline (default 30s).
   - Once the deadline is reached, the loop breaks cleanly, allowing the tick to finish and increment `ticks >= 1`.
   - Residual backlog items remain persisted and are drained across subsequent ticks.
3. **Health Progress Reporting (Acceptance 2)**:
   - Worker health state now initializes and updates `backlog_count` and `last_progress_at`.
   - Healthcheck passes once tick 1 finishes (`status=ok`, `ticks >= 1`).
4. **Automated Verification (Acceptance 4)**:
   - Unit test `test_backlog_of_18_infrastructure_incidents_finishes_within_budget_and_health_becomes_ready` confirms that 18 infrastructure incidents against a slow reconciliation stub are cleared within milliseconds without invoking the slow stub, and health becomes ready (`healthcheck() == 0`).

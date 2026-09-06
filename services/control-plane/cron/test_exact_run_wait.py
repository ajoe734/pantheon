"""Cron exact-run correlation tests for `_CliGatewayTransport`.

SIMPLIFY-OPENCLAW-001 part 3: `_wait_for_terminal_run` must only ever return
a run whose `runId` exactly equals the dispatched `run_id` — never an
`entries[0]` "most recent run" fallback, which could silently report an
unrelated run's outcome as this dispatch's result. A missing `run_id` from
`cron.run` must fail fast (no polling, no auto-resubmit of `cron.run`).

This module intentionally imports only `openclaw_client` (not `service`, which
has an unrelated pre-existing relative-import limitation under this repo's
test harness when collected as a bare top-level module) so it collects and
runs independently.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_CRON_DIR = str(Path(__file__).resolve().parent)
if _CRON_DIR not in sys.path:
    sys.path.insert(0, _CRON_DIR)

from openclaw_client import _CliGatewayTransport  # noqa: E402


def _make_transport(*, run_func, poll_timeout=0.3, poll_interval=0.02) -> _CliGatewayTransport:
    return _CliGatewayTransport(
        gateway_url="ws://openclaw-gateway:18789",
        token="tok",
        poll_timeout_seconds=poll_timeout,
        poll_interval_seconds=poll_interval,
        _run_func=run_func,
        _which_func=lambda _name: "/usr/local/bin/openclaw",
    )


def _run_result(stdout: str):
    class _R:
        returncode = 0

        def __init__(self, out):
            self.stdout = out
            self.stderr = ""

    return _R(stdout)


def _cron_runs_stdout(entries: list) -> str:
    import json

    return json.dumps({"entries": entries})


class TestExactRunCorrelation:
    @pytest.mark.parametrize("entry", [
        {"jobId": "other-job", "runId": "same-run", "status": "ok"},
        {"runId": "same-run", "status": "ok"},
    ])
    def test_same_run_id_without_matching_job_never_completes(self, entry):
        transport = _make_transport(
            run_func=lambda *a, **k: _run_result(_cron_runs_stdout([entry])),
            poll_timeout=0.03,
        )
        with pytest.raises(RuntimeError, match="Timed out"):
            transport._wait_for_terminal_run("job-1", "same-run")

    def test_interleaved_runs_of_same_job_picks_the_exact_one(self):
        """Multiple runIds present for the same job; only ours must be picked."""
        target_run_id = "run-target-002"

        def fake_run(cmd, **_kw):
            entries = [
                {"jobId": "job-1", "runId": "run-other-001", "status": "ok", "summary": "unrelated earlier run"},
                {"jobId": "job-1", "runId": target_run_id, "status": "ok", "summary": "our run"},
                {"jobId": "job-1", "runId": "run-other-003", "status": "failed", "summary": "unrelated later run"},
            ]
            return _run_result(_cron_runs_stdout(entries))

        transport = _make_transport(run_func=fake_run)
        result = transport._wait_for_terminal_run("job-1", target_run_id)
        assert result["runId"] == target_run_id
        assert result["status"] == "ok"

    def test_target_run_outside_most_recent_five_entries(self):
        """The widened `limit: 20` poll window must still find a target run
        that would have been excluded from the old `limit: 5` window."""
        target_run_id = "run-target-old"

        def fake_run(cmd, **_kw):
            # 10 more-recent unrelated runs, then our target run further back.
            recent = [
                {"jobId": "job-1", "runId": f"run-recent-{i}", "status": "ok"} for i in range(10)
            ]
            entries = recent + [{"jobId": "job-1", "runId": target_run_id, "status": "ok"}]
            return _run_result(_cron_runs_stdout(entries))

        transport = _make_transport(run_func=fake_run)
        result = transport._wait_for_terminal_run("job-1", target_run_id)
        assert result["runId"] == target_run_id

    def test_missing_run_id_fails_fast_without_resubmitting(self):
        """`cron.run` returning no runId must raise immediately at the call
        site, never poll blindly, and never resubmit `cron.run`."""
        calls: list[str] = []

        def fake_run(cmd, **_kw):
            method = cmd[3] if len(cmd) > 3 else None
            calls.append(method)
            if method == "cron.add":
                return _run_result('{"id": "job-1"}')
            if method == "cron.run":
                # No runId in the response.
                return _run_result('{"ok": true, "enqueued": true}')
            raise AssertionError(f"unexpected method invoked: {method}")

        transport = _make_transport(run_func=fake_run)
        request = _build_dispatch_request()
        with pytest.raises(RuntimeError, match="did not return a run id"):
            transport(request)

        assert calls.count("cron.run") == 1, "cron.run must not be resubmitted after a missing run id"
        assert "cron.runs" not in calls, "must not poll cron.runs for an unknown run id"

    def test_late_arriving_target_run_eventually_succeeds(self):
        """The target run appears only after a couple of poll iterations —
        the loop must keep polling and eventually succeed, not time out."""
        target_run_id = "run-late"
        call_count = {"n": 0}

        def fake_run(cmd, **_kw):
            call_count["n"] += 1
            if call_count["n"] < 3:
                # Target run not yet indexed by the gateway.
                entries = [{"jobId": "job-1", "runId": "run-unrelated", "status": "ok"}]
            else:
                entries = [
                    {"jobId": "job-1", "runId": "run-unrelated", "status": "ok"},
                    {"jobId": "job-1", "runId": target_run_id, "status": "ok"},
                ]
            return _run_result(_cron_runs_stdout(entries))

        transport = _make_transport(run_func=fake_run, poll_timeout=2.0, poll_interval=0.01)
        result = transport._wait_for_terminal_run("job-1", target_run_id)
        assert result["runId"] == target_run_id
        assert call_count["n"] >= 3

    def test_target_run_failure_status_surfaces_as_real_failure(self):
        """A non-ok terminal status for OUR run must be returned as-is (the
        caller's `__call__` then raises) — not silently swallowed, and not
        confused with an unrelated run's `ok` status."""
        target_run_id = "run-failed-1"

        def fake_run(cmd, **_kw):
            entries = [
                {"jobId": "job-1", "runId": "run-unrelated", "status": "ok"},
                {"jobId": "job-1", "runId": target_run_id, "status": "failed", "error": "boom"},
            ]
            return _run_result(_cron_runs_stdout(entries))

        transport = _make_transport(run_func=fake_run)
        result = transport._wait_for_terminal_run("job-1", target_run_id)
        assert result["runId"] == target_run_id
        assert result["status"] == "failed"

    def test_end_to_end_call_raises_on_target_run_failure_not_unrelated_ok(self):
        """Full `__call__` dispatch path: our run fails while an unrelated
        run of the same job is `ok` — the unrelated `ok` must never surface
        as our result."""
        calls: list[str] = []

        def fake_run(cmd, **_kw):
            method = cmd[3] if len(cmd) > 3 else None
            calls.append(method)
            if method == "cron.add":
                return _run_result('{"id": "job-1"}')
            if method == "cron.run":
                return _run_result('{"jobId": "job-1", "runId": "run-ours", "ok": true}')
            if method == "cron.runs":
                entries = [
                    {"jobId": "job-1", "runId": "run-unrelated", "status": "ok"},
                    {"jobId": "job-1", "runId": "run-ours", "status": "failed"},
                ]
                return _run_result(_cron_runs_stdout(entries))
            raise AssertionError(f"unexpected method: {method}")

        transport = _make_transport(run_func=fake_run)
        request = _build_dispatch_request()
        with pytest.raises(RuntimeError, match="run-ours"):
            transport(request)

    def test_rpc_that_never_returns_terminal_status_hits_timeout_not_wrong_run(self):
        """The target run never reaches a terminal status within the
        deadline, and an unrelated run of the same job DOES reach `ok` — the
        timeout must fire; the unrelated `ok` must never be returned."""
        target_run_id = "run-hangs"

        def fake_run(cmd, **_kw):
            entries = [
                {"jobId": "job-1", "runId": "run-unrelated", "status": "ok"},
                {"jobId": "job-1", "runId": target_run_id, "status": "running"},
            ]
            return _run_result(_cron_runs_stdout(entries))

        transport = _make_transport(run_func=fake_run, poll_timeout=0.15, poll_interval=0.02)
        with pytest.raises(RuntimeError, match="Timed out"):
            transport._wait_for_terminal_run("job-1", target_run_id)

    def test_target_run_not_found_before_timeout_is_unknown_not_success_or_failure(self):
        """If the target run never appears in the polled window at all, the
        timeout must fire (unknown outcome) — it must not be treated as
        success (no fabricated result) nor mapped to a fabricated failure
        status; it is a plain timeout RuntimeError."""

        def fake_run(cmd, **_kw):
            entries = [{"jobId": "job-1", "runId": "run-other", "status": "ok"}]
            return _run_result(_cron_runs_stdout(entries))

        transport = _make_transport(run_func=fake_run, poll_timeout=0.1, poll_interval=0.02)
        with pytest.raises(RuntimeError, match="Timed out"):
            transport._wait_for_terminal_run("job-1", "run-never-appears")

    def test_racing_unrelated_run_reaching_ok_first_never_returned_for_our_run_id(self):
        """Same-run poll races another unrelated run of the same job reaching
        `ok` first; the unrelated run's `ok` must never be returned for our
        run_id — only our own run_id's terminal status counts."""
        target_run_id = "run-ours-2"
        call_count = {"n": 0}

        def fake_run(cmd, **_kw):
            call_count["n"] += 1
            # The unrelated run is "ok" from the very first poll; ours only
            # becomes terminal on the third poll.
            if call_count["n"] < 3:
                entries = [
                    {"jobId": "job-1", "runId": "run-racing-unrelated", "status": "ok"},
                    {"jobId": "job-1", "runId": target_run_id, "status": "running"},
                ]
            else:
                entries = [
                    {"jobId": "job-1", "runId": "run-racing-unrelated", "status": "ok"},
                    {"jobId": "job-1", "runId": target_run_id, "status": "ok"},
                ]
            return _run_result(_cron_runs_stdout(entries))

        transport = _make_transport(run_func=fake_run, poll_timeout=2.0, poll_interval=0.01)
        result = transport._wait_for_terminal_run("job-1", target_run_id)
        assert result["runId"] == target_run_id
        assert call_count["n"] >= 3


class TestExactRunLookupAndSharedDeadline:
    def test_cron_runs_call_requests_exact_runid_lookup(self):
        """The pinned Gateway supports an exact `{id, runId}` lookup ahead of
        pagination — a fixed `limit` alone can falsely time out once enough
        newer runs exist ahead of the target. The request must always ask
        for the exact run."""
        captured_params: list[dict] = []

        def fake_run(cmd, **_kw):
            params_idx = cmd.index("--params") + 1
            import json as _json

            captured_params.append(_json.loads(cmd[params_idx]))
            entries = [{"jobId": "job-1", "runId": "run-target", "status": "ok"}]
            return _run_result(_cron_runs_stdout(entries))

        transport = _make_transport(run_func=fake_run)
        result = transport._wait_for_terminal_run("job-1", "run-target")
        assert result["runId"] == "run-target"
        assert captured_params[0] == {"id": "job-1", "runId": "run-target"}

    def test_target_run_behind_more_than_old_fixed_limit_is_still_found(self):
        """25 newer unrelated runs ahead of the target would have exceeded
        the old fixed `limit: 20` window; an exact `runId` lookup must still
        find it regardless of how many other runs exist."""
        target_run_id = "run-far-behind"

        def fake_run(cmd, **_kw):
            recent = [{"jobId": "job-1", "runId": f"run-recent-{i}", "status": "ok"} for i in range(25)]
            entries = recent + [{"jobId": "job-1", "runId": target_run_id, "status": "ok"}]
            return _run_result(_cron_runs_stdout(entries))

        transport = _make_transport(run_func=fake_run)
        result = transport._wait_for_terminal_run("job-1", target_run_id)
        assert result["runId"] == target_run_id

    def test_rpc_and_sleep_share_one_total_deadline_not_a_fixed_subprocess_timeout(self):
        """The RPC call and the inter-poll sleep must share ONE bounded
        total deadline: `_call` is given only the time remaining, never the
        old fixed 30s regardless of how little budget is actually left."""
        captured_timeouts: list[float] = []

        def fake_run(cmd, **kwargs):
            captured_timeouts.append(kwargs.get("timeout"))
            entries = [{"jobId": "job-1", "runId": "run-never", "status": "running"}]
            return _run_result(_cron_runs_stdout(entries))

        transport = _make_transport(run_func=fake_run, poll_timeout=0.2, poll_interval=0.02)
        with pytest.raises(RuntimeError, match="Timed out"):
            transport._wait_for_terminal_run("job-1", "run-never")
        assert captured_timeouts, "no RPC calls were made"
        assert all(t is not None and t <= 0.2 + 1e-6 for t in captured_timeouts), captured_timeouts
        assert all(t != 30 for t in captured_timeouts), "must not fall back to the fixed 30s timeout"

    def test_rpc_result_arriving_after_deadline_is_rejected_not_accepted_as_late_success(self):
        """A slow/hung RPC that only returns a terminal result after the
        caller's total deadline has already elapsed must be treated as
        unknown (timeout), not trusted as a late success."""
        import time as _time

        deadline_poll_timeout = 0.05

        def fake_run(cmd, **_kw):
            # Simulate an RPC that takes longer than the remaining budget to
            # return, then finally reports our run as terminal.
            _time.sleep(deadline_poll_timeout + 0.05)
            entries = [{"jobId": "job-1", "runId": "run-late", "status": "ok"}]
            return _run_result(_cron_runs_stdout(entries))

        transport = _make_transport(
            run_func=fake_run, poll_timeout=deadline_poll_timeout, poll_interval=0.01
        )
        with pytest.raises(RuntimeError, match="Timed out"):
            transport._wait_for_terminal_run("job-1", "run-late")


def _build_dispatch_request() -> dict:
    return {
        "workflow": {
            "workflow_id": "pantheon.ingest",
            "upstream_entrypoint": "research.ingest",
        },
        "request_id": "pantheon.ingest-abc123",
        "prepared_at": "2026-04-16T19:00:00Z",
        "governance": {"policy_id": "oc002.cron.ingest"},
        "runtime": {"release_tag": "v2026.7.1"},
    }

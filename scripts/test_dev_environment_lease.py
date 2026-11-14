from __future__ import annotations

import copy
import io
import json
import os
import signal
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import dev_environment_lease as lease

REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 13, 16, 30, tzinfo=timezone.utc)


def state_for(
    *,
    owner: str = "execute-plans:run-1",
    mode: str = "qualification",
    lease_id: str = "11111111-1111-4111-8111-111111111111",
    heartbeat: datetime = NOW,
    expires: datetime | None = None,
) -> dict:
    expires = expires or heartbeat + timedelta(minutes=5)
    return {
        "schemaVersion": 1,
        "resource": lease.DEFAULT_RESOURCE,
        "mode": mode,
        "owner": owner,
        "leaseId": lease_id,
        "acquiredAt": lease.utc_iso(heartbeat),
        "heartbeatAt": lease.utc_iso(heartbeat),
        "expiresAt": lease.utc_iso(expires),
        "repository": lease.DEFAULT_REPOSITORY,
        "branch": lease.DEFAULT_BRANCH,
        "path": lease.DEFAULT_PATH,
        "expectedBackendSha": "a" * 40,
        "runUrl": "https://github.com/ajoe734/execute-plans/actions/runs/1",
    }


class FakeClient:
    def __init__(self) -> None:
        self.now = NOW
        self.refs = {"main": "d" * 40, lease.DEFAULT_BRANCH: "c" * 40}
        self.state: dict | None = None
        self.content_sha: str | None = None
        self.put_conflicts = 0
        self.delete_conflicts = 0
        self.put_expected_shas: list[str | None] = []
        self.deleted = False
        self.scripted_content: list[lease.RemoteContent | None] = []
        self.get_content_calls = 0

    def get_ref(self, repository: str, branch: str):
        self._assert_repo(repository)
        return self.refs.get(branch), self.now

    def get_default_branch(self, repository: str):
        self._assert_repo(repository)
        return "main", self.now

    def create_ref(self, repository: str, branch: str, sha: str):
        self._assert_repo(repository)
        if branch in self.refs:
            raise lease.GitHubApiError(422, "Reference already exists")
        self.refs[branch] = sha
        return self.now

    def get_content(self, repository: str, branch: str, path: str):
        self._assert_location(repository, branch, path)
        self.get_content_calls += 1
        if self.scripted_content:
            remote = self.scripted_content[0]
            if len(self.scripted_content) > 1:
                self.scripted_content.pop(0)
            return copy.deepcopy(remote)
        if self.state is None:
            return None
        return lease.RemoteContent(
            state=copy.deepcopy(self.state),
            content_sha=str(self.content_sha),
            server_now=self.now,
        )

    def put_content(
        self,
        repository: str,
        branch: str,
        path: str,
        state,
        *,
        expected_sha: str | None,
        message: str,
    ):
        self._assert_location(repository, branch, path)
        self.put_expected_shas.append(expected_sha)
        if self.put_conflicts:
            self.put_conflicts -= 1
            raise lease.LeaseConflict("simulated CAS conflict")
        if self.state is None:
            if expected_sha is not None:
                raise lease.LeaseConflict("unexpected create precondition")
        elif expected_sha != self.content_sha:
            raise lease.LeaseConflict("stale update precondition")
        self.state = copy.deepcopy(dict(state))
        self.content_sha = f"blob-{len(self.put_expected_shas)}"
        return self.content_sha, self.now

    def delete_content(
        self,
        repository: str,
        branch: str,
        path: str,
        *,
        expected_sha: str,
        message: str,
    ):
        self._assert_location(repository, branch, path)
        if self.delete_conflicts:
            self.delete_conflicts -= 1
            raise lease.LeaseConflict("simulated delete conflict")
        if expected_sha != self.content_sha:
            raise lease.LeaseConflict("stale delete precondition")
        self.state = None
        self.content_sha = None
        self.deleted = True
        return self.now

    @staticmethod
    def _assert_repo(repository: str) -> None:
        assert repository == lease.DEFAULT_REPOSITORY

    @staticmethod
    def _assert_location(repository: str, branch: str, path: str) -> None:
        assert repository == lease.DEFAULT_REPOSITORY
        assert branch == lease.DEFAULT_BRANCH
        assert path == lease.DEFAULT_PATH


def manager(client: FakeClient) -> lease.LeaseManager:
    return lease.LeaseManager(
        client,  # type: ignore[arg-type]
        repository=lease.DEFAULT_REPOSITORY,
        branch=lease.DEFAULT_BRANCH,
        path=lease.DEFAULT_PATH,
        resource=lease.DEFAULT_RESOURCE,
    )


class GitHubRetryTests(unittest.TestCase):
    def test_verify_survives_transient_read_errors(self) -> None:
        for first_error in (500, 502, 503, 504, "timeout", "connection"):
            with self.subTest(first_error=first_error):
                state = state_for()
                payload = {
                    "encoding": "base64",
                    "content": lease.base64.b64encode(json.dumps(state).encode()).decode(),
                    "sha": "blob-current",
                }
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.status = 200
                response.headers = {"Date": "Mon, 13 Jul 2026 16:30:01 GMT"}
                response.read.return_value = json.dumps(payload).encode()
                if first_error == "timeout":
                    error = TimeoutError("timed out")
                elif first_error == "connection":
                    error = urllib.error.URLError("connection reset")
                else:
                    error = urllib.error.HTTPError(
                        "https://api.github.com/test", first_error, "transient", {},
                        io.BytesIO(b'{"message":"temporary failure"}'),
                    )
                client = lease.GitHubClient("secret-sentinel")
                with mock.patch.object(lease.urllib.request, "urlopen", side_effect=[error, response]) as request, mock.patch.object(lease.time, "sleep") as sleep:
                    result = manager(client).verify(
                        lease.public_state(state, content_sha="blob-current"),
                        max_heartbeat_age_seconds=120,
                    )
                self.assertEqual(result["status"], "verified")
                self.assertEqual(request.call_count, 2)
                sleep.assert_called_once_with(1)
                self.assertEqual(request.call_args.kwargs["timeout"], 5)

    def test_read_retry_is_bounded_and_does_not_hide_persistent_outage(self) -> None:
        with mock.patch.object(lease.GitHubClient, "_request_once", side_effect=lease.GitHubApiError(500, "unavailable")) as request, mock.patch.object(lease.time, "sleep") as sleep:
            with self.assertRaises(lease.GitHubApiError):
                lease.GitHubClient("sentinel").get_content(
                    lease.DEFAULT_REPOSITORY, lease.DEFAULT_BRANCH, lease.DEFAULT_PATH
                )
        self.assertEqual(request.call_count, 3)
        self.assertEqual(sleep.call_args_list, [mock.call(1), mock.call(2)])

    def test_auth_conflict_and_malformed_responses_are_not_retried(self) -> None:
        for error in (
            lease.GitHubApiError(401, "unauthorized"),
            lease.GitHubApiError(403, "forbidden"),
            lease.GitHubApiError(409, "conflict"),
            lease.LeaseError("malformed lease"),
        ):
            with self.subTest(error=error), mock.patch.object(lease.GitHubClient, "_request_once", side_effect=error) as request, mock.patch.object(lease.time, "sleep") as sleep:
                with self.assertRaises(lease.LeaseError):
                    lease.GitHubClient("sentinel").get_content(
                        lease.DEFAULT_REPOSITORY, lease.DEFAULT_BRANCH, lease.DEFAULT_PATH
                    )
                self.assertEqual(request.call_count, 1)
                sleep.assert_not_called()

    def test_ambiguous_cas_write_is_not_blindly_replayed(self) -> None:
        with mock.patch.object(lease.GitHubClient, "_request_once", side_effect=lease.GitHubApiError(500, "ambiguous write")) as request, mock.patch.object(lease.time, "sleep") as sleep:
            with self.assertRaises(lease.GitHubApiError):
                lease.GitHubClient("sentinel").put_content(
                    lease.DEFAULT_REPOSITORY, lease.DEFAULT_BRANCH, lease.DEFAULT_PATH,
                    state_for(), expected_sha="old-blob", message="heartbeat",
                )
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()


class HeartbeatRecoveryTests(unittest.TestCase):
    def run_loop(self, root: Path, mgr: lease.LeaseManager) -> tuple[int, list[float]]:
        args = lease.build_parser().parse_args([
            "heartbeat-loop", "--state-file", str(root / "state.json"),
            "--interval-seconds", "60", "--failure-json-out", str(root / "failure.json"),
        ])
        handlers = {}
        self.stop_loop = lambda: handlers[signal.SIGTERM](signal.SIGTERM, None)
        with mock.patch.object(lease.signal, "signal", side_effect=lambda sig, handler: handlers.update({sig: handler})), mock.patch.object(lease.time, "sleep") as sleep:
            result = lease.heartbeat_loop(args, mgr)
        return result, [call.args[0] for call in sleep.call_args_list]

    def test_ambiguous_committed_heartbeat_recovers_with_fresh_cas_and_server_clock(self) -> None:
        client = FakeClient()
        # Server time is deliberately years behind the runner clock.
        client.state = state_for()
        client.content_sha = "blob-current"
        mgr = manager(client)
        original_put = client.put_content
        calls = 0

        def flaky_put(*args, **kwargs):
            nonlocal calls
            calls += 1
            result = original_put(*args, **kwargs)
            if calls == 1:
                raise lease.GitHubApiError(500, "committed but response lost")
            self.stop_loop()
            return result

        client.put_content = flaky_put
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lease.atomic_write_json(root / "state.json", lease.public_state(client.state, content_sha="blob-current"), 0o600)
            result, sleeps = self.run_loop(root, mgr)
            self.assertEqual(result, 0)
            self.assertFalse((root / "failure.json").exists())
            self.assertEqual(lease.read_json_file(root / "state.json", "test")["contentSha"], "blob-2")
        self.assertEqual(client.put_expected_shas, ["blob-current", "blob-1"])
        self.assertEqual(client.get_content_calls, 2)
        # Retry after five seconds, without another normal 60s sleep.
        self.assertEqual(sum(sleeps), 65)

    def test_incident_41812400967_two_transient_put_failures_recover_with_fresh_authoritative_gets(self) -> None:
        incident_hb = datetime(2026, 11, 4, 10, 34, 37, tzinfo=timezone.utc)
        incident_exp = datetime(2026, 11, 4, 10, 39, 37, tzinfo=timezone.utc)
        client = FakeClient()
        client.now = datetime(2026, 11, 4, 10, 35, 39, tzinfo=timezone.utc)
        client.state = state_for(
            owner="pantheon:deploy-41812400967",
            mode="deployment",
            heartbeat=incident_hb,
            expires=incident_exp,
        )
        client.content_sha = "blob-current"
        mgr = manager(client)
        original_put = client.put_content
        put_calls = 0

        def incident_put(*args, **kwargs):
            nonlocal put_calls
            put_calls += 1
            if put_calls == 1:
                client.put_expected_shas.append(kwargs.get("expected_sha"))
                client.now = datetime(2026, 11, 4, 10, 35, 57, tzinfo=timezone.utc)
                raise lease.GitHubTransportError("GitHub API request failed: PUT timed out at 10:35:47Z")
            if put_calls == 2:
                client.put_expected_shas.append(kwargs.get("expected_sha"))
                client.now = datetime(2026, 11, 4, 10, 36, 2, tzinfo=timezone.utc)
                raise lease.GitHubTransportError("GitHub API request failed: PUT timed out at 10:35:57Z")
            result = original_put(*args, **kwargs)
            self.stop_loop()
            return result

        client.put_content = incident_put
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lease.atomic_write_json(
                root / "state.json",
                lease.public_state(client.state, content_sha="blob-current"),
                0o600,
            )
            args = lease.build_parser().parse_args([
                "heartbeat-loop",
                "--state-file", str(root / "state.json"),
                "--interval-seconds", "60",
                "--parent-pid", "99999",
                "--failure-json-out", str(root / "failure.json"),
            ])
            handlers = {}
            self.stop_loop = lambda: handlers[signal.SIGTERM](signal.SIGTERM, None)
            with mock.patch.object(
                lease.signal,
                "signal",
                side_effect=lambda sig, handler: handlers.update({sig: handler}),
            ), mock.patch.object(lease.time, "sleep") as sleep, mock.patch.object(
                lease.os, "kill"
            ) as kill_mock:
                result = lease.heartbeat_loop(args, mgr)
            self.assertEqual(result, 0)
            self.assertFalse((root / "failure.json").exists())
            kill_mock.assert_not_called()
            saved = lease.read_json_file(root / "state.json", "test")
            self.assertEqual(saved["contentSha"], "blob-3")
            self.assertEqual(saved["heartbeatAt"], "2026-11-04T10:36:02Z")
            self.assertEqual(saved["expiresAt"], "2026-11-04T10:41:02Z")
        self.assertEqual(client.get_content_calls, 3)
        self.assertEqual(client.put_expected_shas, ["blob-current", "blob-current", "blob-current"])
        self.assertEqual(sum(call.args[0] for call in sleep.call_args_list), 70)

    def test_renewal_outage_is_bounded_but_auth_and_ownership_fail_immediately(self) -> None:
        # Persistent transient PUT failures after verified fresh GETs retry across more than 2 attempts
        # until the conservative min(expiresAt, heartbeatAt + 120s) budget is exhausted.
        for transient_put_error in (
            lease.GitHubApiError(500, "outage"),
            lease.GitHubTransportError("timeout"),
        ):
            with self.subTest(transient_put_error=transient_put_error), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                client = FakeClient()
                # 18s of freshness remaining (despite 219s of TTL remaining).
                client.state = state_for(
                    heartbeat=NOW - timedelta(seconds=102),
                    expires=NOW + timedelta(seconds=219),
                )
                client.content_sha = "blob-current"
                client.put_content = mock.Mock(side_effect=transient_put_error)
                mgr = manager(client)
                lease.atomic_write_json(
                    root / "state.json",
                    lease.public_state(client.state, content_sha="blob-current"),
                    0o600,
                )
                result, sleeps = self.run_loop(root, mgr)
                self.assertEqual(result, 75)
                self.assertGreaterEqual(client.put_content.call_count, 3)
                self.assertEqual(client.get_content_calls, client.put_content.call_count)
                # Initial 60s interval sleep + retry sleeps bounded by the 18s freshness window (never 219s TTL).
                self.assertLessEqual(sum(sleeps) - 60.0, 18.0)
                self.assertEqual(lease.read_json_file(root / "failure.json", "test")["status"], "lost")

        # Exhausted GETs and non-transient/auth/ownership/CAS/malformed errors fail on attempt 1.
        for immediate_error in (
            lease.GitHubApiError(500, "exhausted get outage"),
            lease.GitHubTransportError("exhausted get timeout"),
            lease.GitHubApiError(401, "unauthorized"),
            lease.GitHubApiError(403, "forbidden"),
            lease.LeaseLost("owner changed or expired"),
            lease.LeaseConflict("CAS changed"),
            lease.LeaseError("malformed lease"),
        ):
            with self.subTest(immediate_error=immediate_error), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                client = FakeClient()
                client.state = state_for()
                mgr = manager(client)
                mgr.heartbeat = mock.Mock(side_effect=immediate_error)
                lease.atomic_write_json(
                    root / "state.json",
                    lease.public_state(client.state, content_sha="blob-current"),
                    0o600,
                )
                result, _ = self.run_loop(root, mgr)
                self.assertEqual(result, 75)
                self.assertEqual(mgr.heartbeat.call_count, 1)
                self.assertEqual(lease.read_json_file(root / "failure.json", "test")["status"], "lost")

    def test_ttl_still_live_but_heartbeat_stale_stops_at_120s_freshness_boundary(self) -> None:
        # Incident 41812400967 geometry: 219s TTL remaining (expiresAt=10:39:37Z),
        # but only 39s freshness remaining at 10:35:58Z before 10:36:37Z (heartbeatAt=10:34:37Z + 120s).
        incident_hb = datetime(2026, 11, 4, 10, 34, 37, tzinfo=timezone.utc)
        incident_exp = datetime(2026, 11, 4, 10, 39, 37, tzinfo=timezone.utc)
        client = FakeClient()
        client.now = datetime(2026, 11, 4, 10, 35, 58, tzinfo=timezone.utc)
        client.state = state_for(heartbeat=incident_hb, expires=incident_exp)
        client.content_sha = "blob-current"
        client.put_content = mock.Mock(side_effect=lease.GitHubTransportError("persistent PUT timeout"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lease.atomic_write_json(
                root / "state.json",
                lease.public_state(client.state, content_sha="blob-current"),
                0o600,
            )
            result, sleeps = self.run_loop(root, manager(client))
            self.assertEqual(result, 75)
            retry_elapsed = sum(sleeps) - 60.0
            self.assertLessEqual(retry_elapsed, 39.0)
            self.assertGreater(client.put_content.call_count, 2)
            self.assertEqual(lease.read_json_file(root / "failure.json", "test")["status"], "lost")

        # Direct heartbeat call when TTL has 300s left but heartbeat age == 120s fails closed before PUT.
        stale_client = FakeClient()
        stale_client.now = NOW
        stale_client.state = state_for(
            heartbeat=NOW - timedelta(seconds=120),
            expires=NOW + timedelta(seconds=300),
        )
        stale_client.content_sha = "blob-stale-hb"
        stale_client.put_content = mock.Mock()
        with self.assertRaisesRegex(lease.LeaseLost, "heartbeat is stale"):
            manager(stale_client).heartbeat(
                lease.public_state(stale_client.state, content_sha="blob-stale-hb"),
                ttl_seconds=300,
            )
        stale_client.put_content.assert_not_called()

    def test_earlier_expires_at_stops_before_freshness_boundary(self) -> None:
        client = FakeClient()
        # Heartbeat is fresh (10s old -> 110s freshness left), but expiresAt is only 12s away.
        client.state = state_for(
            heartbeat=NOW - timedelta(seconds=10),
            expires=NOW + timedelta(seconds=12),
        )
        client.content_sha = "blob-current"
        client.put_content = mock.Mock(side_effect=lease.GitHubApiError(503, "write unavailable"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lease.atomic_write_json(
                root / "state.json",
                lease.public_state(client.state, content_sha="blob-current"),
                0o600,
            )
            result, sleeps = self.run_loop(root, manager(client))
            self.assertEqual(result, 75)
            retry_elapsed = sum(sleeps) - 60.0
            self.assertLessEqual(retry_elapsed, 12.0)
            self.assertEqual(client.put_content.call_count, 4)
            self.assertEqual(lease.read_json_file(root / "failure.json", "test")["status"], "lost")

    def test_failed_get_after_transient_put_never_extends_or_reuses_budget(self) -> None:
        client = FakeClient()
        client.state = state_for()
        client.content_sha = "blob-current"
        original_get = client.get_content

        def get_fails_on_second_renewal(repository: str, branch: str, path: str):
            if client.get_content_calls == 1:
                client.get_content_calls += 1
                raise lease.GitHubTransportError("exhausted GET on retry")
            return original_get(repository, branch, path)

        client.get_content = get_fails_on_second_renewal
        client.put_content = mock.Mock(side_effect=lease.GitHubTransportError("first PUT failed"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lease.atomic_write_json(
                root / "state.json",
                lease.public_state(client.state, content_sha="blob-current"),
                0o600,
            )
            result, _ = self.run_loop(root, manager(client))
            self.assertEqual(result, 75)
            self.assertIn("exhausted GET on retry", lease.read_json_file(root / "failure.json", "test")["error"])
        self.assertEqual(client.get_content_calls, 2)
        self.assertEqual(client.put_content.call_count, 1)

    def test_missing_malformed_auth_or_cas_conflict_after_transient_put_fails_closed(self) -> None:
        for second_failure in ("missing", "malformed", "auth401", "auth403", "cas409"):
            with self.subTest(second_failure=second_failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                client = FakeClient()
                client.state = state_for()
                client.content_sha = "blob-current"
                lease.atomic_write_json(
                    root / "state.json",
                    lease.public_state(client.state, content_sha="blob-current"),
                    0o600,
                )
                calls = 0

                def put_side_effect(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        if second_failure == "missing":
                            client.state = None
                        elif second_failure == "malformed":
                            client.state["expiresAt"] = "not-a-timestamp"
                        raise lease.GitHubTransportError("transient PUT before terminal condition")
                    if second_failure == "auth401":
                        raise lease.GitHubApiError(401, "revoked token")
                    if second_failure == "auth403":
                        raise lease.GitHubApiError(403, "forbidden")
                    if second_failure == "cas409":
                        raise lease.LeaseConflict("concurrent CAS conflict")
                    raise AssertionError("unreachable")

                client.put_content = mock.Mock(side_effect=put_side_effect)
                result, _ = self.run_loop(root, manager(client))
                self.assertEqual(result, 75)
                self.assertEqual(lease.read_json_file(root / "failure.json", "test")["status"], "lost")
                expected_puts = 1 if second_failure in ("missing", "malformed") else 2
                self.assertEqual(client.put_content.call_count, expected_puts)
                self.assertEqual(client.get_content_calls, 2)

    def test_request_timeout_and_retry_delays_are_bounded_by_validity_deadline_without_wall_clock_fallback(self) -> None:
        state = state_for(
            heartbeat=NOW - timedelta(seconds=117),
            expires=NOW + timedelta(seconds=200),
        )
        get_payload = {
            "encoding": "base64",
            "content": lease.base64.b64encode(json.dumps(state).encode()).decode(),
            "sha": "blob-current",
        }
        get_response = mock.MagicMock()
        get_response.__enter__.return_value = get_response
        get_response.status = 200
        get_response.headers = {"Date": "Mon, 13 Jul 2026 16:30:00 GMT"}
        get_response.read.return_value = json.dumps(get_payload).encode()

        put_timeout = TimeoutError("PUT timed out")
        client = lease.GitHubClient("secret-sentinel")
        mgr = manager(client)
        local = lease.public_state(state, content_sha="blob-current")
        # Only 3 seconds of freshness remain at server_now (117s age vs 120s limit),
        # so the PUT request timeout must be capped to 3.0s instead of the default 5s.
        with mock.patch.object(
            lease.urllib.request, "urlopen", side_effect=[get_response, put_timeout]
        ) as urlopen_mock:
            with self.assertRaises(lease.GitHubTransportError) as ctx:
                mgr.heartbeat(local, ttl_seconds=300)
        self.assertEqual(urlopen_mock.call_count, 2)
        self.assertAlmostEqual(urlopen_mock.call_args_list[1].kwargs["timeout"], 3.0, places=2)
        budget = getattr(ctx.exception, "_verified_renewal_budget", None)
        self.assertIsNotNone(budget)
        self.assertEqual(
            budget.validity_deadline_utc,
            NOW + timedelta(seconds=3),
        )

    def test_delayed_get_body_deducts_elapsed_and_blocks_put_past_fence(self) -> None:
        server_now = datetime(2026, 11, 4, 10, 35, 58, tzinfo=timezone.utc)
        server_date = "Wed, 04 Nov 2026 10:35:58 GMT"
        # 119s heartbeat age at Date -> 1.0s validity remaining anchored at GET start (100.0 -> 101.0).
        state = state_for(
            owner="synthetic-audit-owner",
            mode="deployment",
            heartbeat=server_now - timedelta(seconds=119),
            expires=server_now + timedelta(seconds=300),
        )
        get_payload = {
            "encoding": "base64",
            "content": lease.base64.b64encode(json.dumps(state).encode()).decode(),
            "sha": "synthetic-current",
        }
        clock = [100.0]
        calls: list[dict[str, object]] = []

        class DelayedGetResponse:
            status = 200

            def __init__(self, method: str, get_elapsed: float) -> None:
                self.method = method
                self.get_elapsed = get_elapsed
                self.headers = {"Date": server_date}

            def __enter__(self) -> DelayedGetResponse:
                return self

            def __exit__(self, *_args: object) -> bool:
                return False

            def read(self) -> bytes:
                if self.method == "GET":
                    clock[0] += self.get_elapsed
                    return json.dumps(get_payload).encode()
                raise AssertionError("PUT read should not be reached when GET exhausted fence")

        def urlopen_exhausted(request: object, timeout: float) -> DelayedGetResponse:
            method = getattr(request, "method")
            calls.append({"method": method, "monotonic": clock[0], "timeout": timeout})
            if method == "PUT":
                raise AssertionError("PUT must not dispatch after delayed GET exhausted fence")
            return DelayedGetResponse(method, 4.0)

        client = lease.GitHubClient("synthetic-key-not-a-real-secret")
        mgr = manager(client)
        with mock.patch.object(
            lease.time, "monotonic", side_effect=lambda: clock[0]
        ), mock.patch.object(
            lease.urllib.request, "urlopen", side_effect=urlopen_exhausted
        ):
            with self.assertRaisesRegex(lease.LeaseLost, "validity budget exhausted before renewal write") as ctx:
                mgr.heartbeat(
                    lease.public_state(state, content_sha="synthetic-current"),
                    ttl_seconds=300,
                )
        self.assertIsNone(getattr(ctx.exception, "_verified_renewal_budget", None))
        self.assertEqual(calls, [{"method": "GET", "monotonic": 100.0, "timeout": 5}])
        self.assertEqual(mgr._active_deadline_monotonic, 101.0)

        # Partial GET elapsed: 5s remaining at Date, 3s GET body -> 2s remaining for PUT at 103.0 (fence 105.0).
        state_5s = state_for(
            owner="synthetic-audit-owner",
            mode="deployment",
            heartbeat=server_now - timedelta(seconds=115),
            expires=server_now + timedelta(seconds=300),
        )
        get_payload_5s = {
            "encoding": "base64",
            "content": lease.base64.b64encode(json.dumps(state_5s).encode()).decode(),
            "sha": "synthetic-current",
        }
        clock_partial = [100.0]
        calls_partial: list[dict[str, object]] = []

        def urlopen_partial(request: object, timeout: float) -> DelayedGetResponse:
            method = getattr(request, "method")
            calls_partial.append({"method": method, "monotonic": clock_partial[0], "timeout": timeout})
            if method == "PUT":
                raise TimeoutError("PUT failed after capped timeout")
            resp = DelayedGetResponse(method, 3.0)
            resp.read = lambda: (  # type: ignore[method-assign]
                clock_partial.__setitem__(0, clock_partial[0] + 3.0)
                or json.dumps(get_payload_5s).encode()
            )
            return resp

        client_partial = lease.GitHubClient("synthetic-key-not-a-real-secret")
        mgr_partial = manager(client_partial)
        with mock.patch.object(
            lease.time, "monotonic", side_effect=lambda: clock_partial[0]
        ), mock.patch.object(
            lease.urllib.request, "urlopen", side_effect=urlopen_partial
        ):
            with self.assertRaises(lease.GitHubTransportError) as partial_ctx:
                mgr_partial.heartbeat(
                    lease.public_state(state_5s, content_sha="synthetic-current"),
                    ttl_seconds=300,
                )
        self.assertEqual(
            calls_partial,
            [
                {"method": "GET", "monotonic": 100.0, "timeout": 5},
                {"method": "PUT", "monotonic": 103.0, "timeout": 2.0},
            ],
        )
        partial_budget = getattr(partial_ctx.exception, "_verified_renewal_budget", None)
        self.assertIsNotNone(partial_budget)
        self.assertEqual(partial_budget.deadline_monotonic, 105.0)

    def test_late_successful_put_body_past_validity_fence_is_rejected(self) -> None:
        server_now = datetime(2026, 11, 4, 10, 35, 58, tzinfo=timezone.utc)
        server_date = "Wed, 04 Nov 2026 10:35:58 GMT"
        # 117s heartbeat age at Date -> 3.0s validity remaining anchored at 100.0 (fence 103.0).
        state = state_for(
            owner="synthetic-audit-owner",
            mode="deployment",
            heartbeat=server_now - timedelta(seconds=117),
            expires=server_now + timedelta(seconds=300),
        )
        get_payload = {
            "encoding": "base64",
            "content": lease.base64.b64encode(json.dumps(state).encode()).decode(),
            "sha": "synthetic-current",
        }
        clock = [100.0]
        calls: list[dict[str, object]] = []

        class SlowPutResponse:
            status = 200

            def __init__(self, method: str) -> None:
                self.method = method
                self.headers = {"Date": server_date}

            def __enter__(self) -> SlowPutResponse:
                return self

            def __exit__(self, *_args: object) -> bool:
                return False

            def read(self) -> bytes:
                if self.method == "GET":
                    return json.dumps(get_payload).encode()
                clock[0] += 4.0
                return json.dumps({"content": {"sha": "synthetic-new"}}).encode()

        def synthetic_urlopen(request: object, timeout: float) -> SlowPutResponse:
            method = getattr(request, "method")
            calls.append({"method": method, "monotonic": clock[0], "timeout": timeout})
            return SlowPutResponse(method)

        client = lease.GitHubClient("synthetic-key-not-a-real-secret")
        mgr = manager(client)
        with mock.patch.object(
            lease.time, "monotonic", side_effect=lambda: clock[0]
        ), mock.patch.object(
            lease.urllib.request, "urlopen", side_effect=synthetic_urlopen
        ):
            with self.assertRaisesRegex(lease.LeaseLost, "validity budget exhausted") as ctx:
                mgr.heartbeat(
                    lease.public_state(state, content_sha="synthetic-current"),
                    ttl_seconds=300,
                )
        self.assertIsNone(getattr(ctx.exception, "_verified_renewal_budget", None))
        self.assertIsNotNone(mgr._recovery_anchor)
        self.assertEqual(mgr._active_deadline_monotonic, 103.0)
        self.assertEqual(
            calls,
            [
                {"method": "GET", "monotonic": 100.0, "timeout": 5},
                {"method": "PUT", "monotonic": 100.0, "timeout": 3.0},
            ],
        )

    def test_owner_replacement_after_transient_error_is_never_renewed(self) -> None:
        client = FakeClient()
        client.state = state_for()
        client.content_sha = "blob-current"

        def replaced_put(*args, **kwargs):
            client.state["leaseId"] = "22222222-2222-4222-8222-222222222222"
            client.content_sha = "replacement"
            raise lease.GitHubApiError(503, "response unavailable")

        client.put_content = mock.Mock(side_effect=replaced_put)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lease.atomic_write_json(root / "state.json", lease.public_state(client.state, content_sha="blob-current"), 0o600)
            result, _ = self.run_loop(root, manager(client))
            self.assertEqual(result, 75)
            self.assertIn("leaseId", lease.read_json_file(root / "failure.json", "test")["error"])
        self.assertEqual(client.put_content.call_count, 1)


class LeaseStateTests(unittest.TestCase):
    def test_validate_state_rejects_wrong_location_and_bad_expiry(self) -> None:
        wrong = state_for()
        wrong["branch"] = "dev"
        with self.assertRaisesRegex(lease.LeaseError, "branch mismatch"):
            lease.validate_state(
                wrong,
                repository=lease.DEFAULT_REPOSITORY,
                branch=lease.DEFAULT_BRANCH,
                path=lease.DEFAULT_PATH,
                resource=lease.DEFAULT_RESOURCE,
            )

        invalid = state_for(expires=NOW)
        with self.assertRaisesRegex(lease.LeaseError, "expiresAt must be after"):
            lease.validate_state(
                invalid,
                repository=lease.DEFAULT_REPOSITORY,
                branch=lease.DEFAULT_BRANCH,
                path=lease.DEFAULT_PATH,
                resource=lease.DEFAULT_RESOURCE,
            )

    def test_public_state_is_sanitized_and_state_file_is_private(self) -> None:
        payload = lease.public_state(state_for(), content_sha="blob-1")
        self.assertNotIn("token", json.dumps(payload).lower())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lease.json"
            lease.atomic_write_json(path, payload, 0o600)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), payload)


class LeaseManagerTests(unittest.TestCase):
    def test_bootstrap_creates_dedicated_branch_from_repository_default(self) -> None:
        client = FakeClient()
        del client.refs[lease.DEFAULT_BRANCH]
        result = manager(client).bootstrap("")
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["baseBranch"], "main")
        self.assertEqual(client.refs[lease.DEFAULT_BRANCH], "d" * 40)

    def test_acquire_missing_file_uses_atomic_create(self) -> None:
        client = FakeClient()
        state, content_sha, _ = manager(client).acquire(
            mode="qualification",
            owner="execute-plans:run-2",
            ttl_seconds=300,
            wait_seconds=0,
            poll_seconds=0.01,
            expected_backend_sha="b" * 40,
            run_url="https://github.com/ajoe734/execute-plans/actions/runs/2",
        )
        self.assertEqual(client.put_expected_shas, [None])
        self.assertEqual(content_sha, "blob-1")
        self.assertEqual(state["expectedBackendSha"], "b" * 40)
        self.assertEqual(state["owner"], "execute-plans:run-2")

    def test_active_lease_is_busy_and_never_overwritten(self) -> None:
        client = FakeClient()
        client.state = state_for(owner="other-run")
        client.content_sha = "blob-existing"
        with self.assertRaisesRegex(lease.LeaseBusy, "other-run"):
            manager(client).acquire(
                mode="deployment",
                owner="pantheon:deploy-1",
                ttl_seconds=300,
                wait_seconds=0,
                poll_seconds=0.01,
            )
        self.assertEqual(client.put_expected_shas, [])

    def test_stale_takeover_uses_old_blob_sha(self) -> None:
        client = FakeClient()
        client.state = state_for(owner="stale", expires=NOW - timedelta(seconds=1))
        # Keep the document structurally valid while expired at server time.
        client.state["heartbeatAt"] = lease.utc_iso(NOW - timedelta(minutes=6))
        client.state["acquiredAt"] = lease.utc_iso(NOW - timedelta(minutes=7))
        client.content_sha = "blob-stale"
        state, _, _ = manager(client).acquire(
            mode="deployment",
            owner="pantheon:deploy-2",
            ttl_seconds=300,
            wait_seconds=0,
            poll_seconds=0.01,
        )
        self.assertEqual(client.put_expected_shas, ["blob-stale"])
        self.assertEqual(state["owner"], "pantheon:deploy-2")

    def test_acquire_retries_one_cas_conflict(self) -> None:
        client = FakeClient()
        client.put_conflicts = 1
        state, _, _ = manager(client).acquire(
            mode="qualification",
            owner="execute-plans:retry",
            ttl_seconds=300,
            wait_seconds=2,
            poll_seconds=0.01,
        )
        self.assertEqual(state["owner"], "execute-plans:retry")
        self.assertEqual(len(client.put_expected_shas), 2)

    def test_verify_emits_exact_active_identity(self) -> None:
        client = FakeClient()
        client.state = state_for()
        client.content_sha = "blob-current"
        result = manager(client).verify(
            lease.public_state(client.state, content_sha="blob-old"),
            max_heartbeat_age_seconds=120,
        )
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["verifiedAt"], lease.utc_iso(NOW))
        self.assertEqual(result["contentSha"], "blob-current")
        self.assertEqual(result["heartbeatAgeSeconds"], 0)

    def test_verify_rejects_every_acquisition_immutable_field_mutation(self) -> None:
        mutations = {
            "schemaVersion": 2,
            "repository": "ajoe734/other",
            "branch": "other-coordination",
            "path": ".pantheon/environment-leases/other.json",
            "leaseId": "22222222-2222-4222-8222-222222222222",
            "owner": "other-owner",
            "mode": "deployment",
            "resource": "other-resource",
            "acquiredAt": lease.utc_iso(NOW - timedelta(seconds=1)),
            "expectedBackendSha": "b" * 40,
            "runUrl": "https://github.com/ajoe734/execute-plans/actions/runs/2",
        }
        self.assertEqual(
            set(mutations), set(lease.ACQUISITION_IMMUTABLE_FIELDS)
        )
        original = state_for()
        local = lease.public_state(original, content_sha="blob-original")
        for key, changed in mutations.items():
            with self.subTest(key=key):
                client = FakeClient()
                client.state = copy.deepcopy(original)
                client.state[key] = changed
                client.content_sha = "blob-mutated"
                with self.assertRaisesRegex(
                    lease.LeaseLost, rf"immutable field {key} changed"
                ):
                    manager(client).verify(
                        local,
                        max_heartbeat_age_seconds=120,
                    )

    def test_verify_rejects_removed_optional_acquisition_identity(self) -> None:
        original = state_for()
        local = lease.public_state(original, content_sha="blob-original")
        for key in ("expectedBackendSha", "runUrl"):
            with self.subTest(key=key):
                client = FakeClient()
                client.state = copy.deepcopy(original)
                client.state.pop(key)
                client.content_sha = "blob-mutated"
                with self.assertRaisesRegex(
                    lease.LeaseLost, rf"immutable field {key} changed"
                ):
                    manager(client).verify(
                        local,
                        max_heartbeat_age_seconds=120,
                    )

    def test_verify_fails_closed_on_stale_heartbeat(self) -> None:
        client = FakeClient()
        client.state = state_for(heartbeat=NOW - timedelta(minutes=3), expires=NOW + timedelta(minutes=2))
        client.content_sha = "blob-current"
        with self.assertRaisesRegex(lease.LeaseLost, "heartbeat is stale"):
            manager(client).verify(
                lease.public_state(client.state, content_sha="blob-current"),
                max_heartbeat_age_seconds=120,
            )

    def test_initial_verify_retries_only_the_exact_expired_predecessor(self) -> None:
        client = FakeClient()
        predecessor = state_for(
            owner="execute-plans:previous",
            lease_id="22222222-2222-4222-8222-222222222222",
            heartbeat=NOW - timedelta(minutes=6),
            expires=NOW - timedelta(seconds=1),
        )
        predecessor_sha = "1" * 40
        client.state = copy.deepcopy(predecessor)
        client.content_sha = predecessor_sha
        acquired, content_sha, _ = manager(client).acquire(
            mode="deployment",
            owner="execute-plans:current",
            ttl_seconds=300,
            wait_seconds=0,
            poll_seconds=0.01,
            expected_backend_sha="b" * 40,
            run_url="https://github.com/ajoe734/pantheon/actions/runs/2",
        )
        local = lease.public_state(acquired, content_sha=content_sha)
        current = lease.RemoteContent(
            state=copy.deepcopy(client.state),
            content_sha=str(client.content_sha),
            server_now=client.now,
        )
        client.scripted_content = [
            lease.RemoteContent(
                state=predecessor,
                content_sha=predecessor_sha,
                server_now=client.now,
            ),
            current,
        ]

        result = manager(client).verify(
            local,
            max_heartbeat_age_seconds=120,
            initial_visibility_wait_seconds=0.1,
            initial_visibility_poll_seconds=0.001,
        )

        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["leaseId"], acquired["leaseId"])
        self.assertEqual(client.get_content_calls, 3)  # acquire + two verifies

    def test_initial_verify_fails_immediately_for_foreign_active_replacement(self) -> None:
        client = FakeClient()
        local_state = state_for(
            owner="execute-plans:current",
            mode="deployment",
            lease_id="33333333-3333-4333-8333-333333333333",
        )
        local_state["previousContentSha"] = "1" * 40
        local = lease.public_state(local_state, content_sha="2" * 40)
        client.state = state_for(
            owner="execute-plans:foreign",
            mode="deployment",
            lease_id="44444444-4444-4444-8444-444444444444",
            heartbeat=NOW + timedelta(seconds=1),
        )
        client.content_sha = "3" * 40

        with self.assertRaisesRegex(lease.LeaseLost, "leaseId changed"):
            manager(client).verify(
                local,
                max_heartbeat_age_seconds=120,
                initial_visibility_wait_seconds=10,
                initial_visibility_poll_seconds=1,
            )
        self.assertEqual(client.get_content_calls, 1)

    def test_initial_verify_times_out_if_exact_predecessor_remains_visible(self) -> None:
        client = FakeClient()
        local_state = state_for(
            owner="execute-plans:current",
            mode="deployment",
            lease_id="33333333-3333-4333-8333-333333333333",
            heartbeat=NOW,
        )
        local_state["previousContentSha"] = "1" * 40
        local = lease.public_state(local_state, content_sha="2" * 40)
        predecessor = state_for(
            owner="execute-plans:previous",
            mode="deployment",
            lease_id="22222222-2222-4222-8222-222222222222",
            heartbeat=NOW - timedelta(minutes=6),
            expires=NOW - timedelta(seconds=1),
        )
        client.scripted_content = [
            lease.RemoteContent(
                state=predecessor,
                content_sha="1" * 40,
                server_now=NOW,
            )
        ]

        with self.assertRaisesRegex(
            lease.LeaseLost, "bounded timeout"
        ):
            manager(client).verify(
                local,
                max_heartbeat_age_seconds=120,
                initial_visibility_wait_seconds=0.01,
                initial_visibility_poll_seconds=0.001,
            )
        self.assertGreater(client.get_content_calls, 1)

    def test_initial_verify_fails_immediately_for_wrong_predecessor_sha(self) -> None:
        client = FakeClient()
        local_state = state_for(
            owner="execute-plans:current",
            mode="deployment",
            lease_id="33333333-3333-4333-8333-333333333333",
        )
        local_state["previousContentSha"] = "1" * 40
        local = lease.public_state(local_state, content_sha="2" * 40)
        client.state = state_for(
            owner="execute-plans:previous",
            mode="deployment",
            lease_id="22222222-2222-4222-8222-222222222222",
            heartbeat=NOW - timedelta(minutes=6),
            expires=NOW - timedelta(seconds=1),
        )
        client.content_sha = "9" * 40

        with self.assertRaisesRegex(lease.LeaseLost, "leaseId changed"):
            manager(client).verify(
                local,
                max_heartbeat_age_seconds=120,
                initial_visibility_wait_seconds=10,
                initial_visibility_poll_seconds=1,
            )
        self.assertEqual(client.get_content_calls, 1)

    def test_initial_verify_fails_immediately_without_valid_predecessor_sha(self) -> None:
        for previous_content_sha in (None, "not-a-sha"):
            with self.subTest(previousContentSha=previous_content_sha):
                client = FakeClient()
                local_state = state_for(
                    owner="execute-plans:current",
                    mode="deployment",
                    lease_id="33333333-3333-4333-8333-333333333333",
                )
                local = lease.public_state(local_state, content_sha="2" * 40)
                if previous_content_sha is not None:
                    local["previousContentSha"] = previous_content_sha
                client.state = state_for(
                    owner="execute-plans:previous",
                    mode="deployment",
                    lease_id="22222222-2222-4222-8222-222222222222",
                    heartbeat=NOW - timedelta(minutes=6),
                    expires=NOW - timedelta(seconds=1),
                )
                client.content_sha = "1" * 40

                with self.assertRaisesRegex(lease.LeaseLost, "leaseId changed"):
                    manager(client).verify(
                        local,
                        max_heartbeat_age_seconds=120,
                        initial_visibility_wait_seconds=10,
                        initial_visibility_poll_seconds=1,
                    )
                self.assertEqual(client.get_content_calls, 1)

    def test_initial_verify_does_not_retry_predecessor_without_opt_in(self) -> None:
        client = FakeClient()
        local_state = state_for(
            owner="execute-plans:current",
            mode="deployment",
            lease_id="33333333-3333-4333-8333-333333333333",
        )
        local_state["previousContentSha"] = "1" * 40
        local = lease.public_state(local_state, content_sha="2" * 40)
        client.state = state_for(
            owner="execute-plans:previous",
            mode="deployment",
            lease_id="22222222-2222-4222-8222-222222222222",
            heartbeat=NOW - timedelta(minutes=6),
            expires=NOW - timedelta(seconds=1),
        )
        client.content_sha = "1" * 40

        with self.assertRaisesRegex(lease.LeaseLost, "leaseId changed"):
            manager(client).verify(local, max_heartbeat_age_seconds=120)
        self.assertEqual(client.get_content_calls, 1)

    def test_initial_visibility_bounds_fail_closed(self) -> None:
        client = FakeClient()
        local = lease.public_state(state_for(), content_sha="blob-current")

        invalid_args = (
            {
                "initial_visibility_wait_seconds": -0.1,
                "initial_visibility_poll_seconds": 1,
                "message": "wait-seconds must be between 0 and 30",
            },
            {
                "initial_visibility_wait_seconds": 31,
                "initial_visibility_poll_seconds": 1,
                "message": "wait-seconds must be between 0 and 30",
            },
            {
                "initial_visibility_wait_seconds": 1,
                "initial_visibility_poll_seconds": 0,
                "message": "poll-seconds must be greater than 0 and at most 5",
            },
            {
                "initial_visibility_wait_seconds": 1,
                "initial_visibility_poll_seconds": 6,
                "message": "poll-seconds must be greater than 0 and at most 5",
            },
        )
        for case in invalid_args:
            with self.subTest(case=case):
                with self.assertRaisesRegex(lease.LeaseError, case["message"]):
                    manager(client).verify(
                        local,
                        max_heartbeat_age_seconds=120,
                        initial_visibility_wait_seconds=case[
                            "initial_visibility_wait_seconds"
                        ],
                        initial_visibility_poll_seconds=case[
                            "initial_visibility_poll_seconds"
                        ],
                    )
                self.assertEqual(client.get_content_calls, 0)

    def test_heartbeat_renews_with_current_blob_sha(self) -> None:
        client = FakeClient()
        client.state = state_for()
        client.content_sha = "blob-current"
        client.now = NOW + timedelta(minutes=1)
        local = lease.public_state(client.state, content_sha="blob-current")
        renewed, content_sha, _ = manager(client).heartbeat(local, ttl_seconds=300)
        self.assertEqual(client.put_expected_shas, ["blob-current"])
        self.assertEqual(content_sha, "blob-1")
        self.assertEqual(renewed["heartbeatAt"], lease.utc_iso(client.now))
        self.assertEqual(
            renewed["expiresAt"], lease.utc_iso(client.now + timedelta(minutes=5))
        )

    def test_heartbeat_and_release_refuse_replacement_owner(self) -> None:
        client = FakeClient()
        original = state_for()
        local = lease.public_state(original, content_sha="blob-original")
        client.state = state_for(
            owner="replacement",
            mode="deployment",
            lease_id="22222222-2222-4222-8222-222222222222",
        )
        client.content_sha = "blob-replacement"
        with self.assertRaisesRegex(lease.LeaseLost, "leaseId changed"):
            manager(client).heartbeat(local, ttl_seconds=300)
        with self.assertRaisesRegex(lease.LeaseLost, "leaseId changed"):
            manager(client).release(local)
        self.assertFalse(client.deleted)

    def test_release_retries_cas_without_deleting_a_different_lease(self) -> None:
        client = FakeClient()
        client.state = state_for()
        client.content_sha = "blob-current"
        client.delete_conflicts = 1
        local = lease.public_state(client.state, content_sha="blob-current")
        result = manager(client).release(local)
        self.assertEqual(result["status"], "released")
        self.assertTrue(client.deleted)

    def test_heartbeat_loop_retries_transient_errors_while_lease_unexpired(self) -> None:
        import subprocess
        import sys
        import time

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = root / "state.json"
            failure_file = root / "failure.json"
            shutdown_file = root / "shutdown.json"
            identity_file = root / "identity.json"
            flaky_marker = root / "flaky-count.txt"
            flaky_marker.write_text("0\n", encoding="utf-8")

            now = datetime.now(timezone.utc)
            local_payload = lease.public_state(
                state_for(heartbeat=now, expires=now + timedelta(minutes=5)), content_sha="blob-1"
            )
            lease.atomic_write_json(state_file, local_payload, 0o600)

            # Test script that runs heartbeat-loop with a manager that fails on attempt 1, then succeeds
            script = f"""
import sys
from pathlib import Path
sys.path.insert(0, {str(REPO_ROOT / "scripts")!r})
sys.path.insert(0, {str(REPO_ROOT)!r})
import dev_environment_lease as lease
from scripts.test_dev_environment_lease import FakeClient, state_for, manager
from datetime import datetime, timedelta, timezone
import argparse

now = datetime.fromisoformat({now.isoformat()!r})
client = FakeClient()
client.now = now
client.state = state_for(heartbeat=now, expires=now + timedelta(minutes=5))
client.content_sha = "blob-1"
mgr = manager(client)

marker = Path({str(flaky_marker)!r})
original_put = client.put_content

def flaky_put(*args, **kwargs):
    count = int(marker.read_text().strip() or "0") + 1
    marker.write_text(f"{{count}}\\n")
    if count == 1:
        raise lease.GitHubApiError(500, "Simulated 500")
    if count == 2:
        raise lease.GitHubTransportError("Simulated PUT transport timeout")
    return original_put(*args, **kwargs)

client.put_content = flaky_put

args = argparse.Namespace(
    command="heartbeat-loop",
    interval_seconds=1,
    ttl_seconds=300,
    parent_pid=0,
    state_file={str(state_file)!r},
    failure_json_out={str(failure_file)!r},
    shutdown_json_out={str(shutdown_file)!r},
    identity_json_out={str(identity_file)!r},
    token_stdin=False,
)

raise SystemExit(lease.heartbeat_loop(args, mgr))
"""
            sub = subprocess.Popen(
                [sys.executable, "-c", script],
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    if int(flaky_marker.read_text().strip() or "0") >= 3:
                        break
                    time.sleep(0.1)
                sub.send_signal(signal.SIGTERM)
                _stdout, stderr = sub.communicate(timeout=5)
            finally:
                if sub.poll() is None:
                    sub.kill()
                    sub.communicate(timeout=3)

            self.assertEqual(sub.returncode, 0, stderr)
            self.assertFalse(failure_file.exists())
            self.assertTrue(shutdown_file.exists())
            self.assertGreaterEqual(int(flaky_marker.read_text().strip()), 3)

    def test_heartbeat_loop_fails_immediately_on_lease_lost(self) -> None:
        import argparse

        client = FakeClient()
        client.state = state_for()
        client.content_sha = "blob-1"
        mgr = manager(client)

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            failure_file = Path(tmp) / "failure.json"
            shutdown_file = Path(tmp) / "shutdown.json"
            identity_file = Path(tmp) / "identity.json"

            local_payload = lease.public_state(client.state, content_sha="blob-1")
            lease.atomic_write_json(state_file, local_payload, 0o600)

            def lost_heartbeat(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], str, datetime]:
                raise lease.LeaseLost("lease expired or stolen")

            mgr.heartbeat = lost_heartbeat  # type: ignore[assignment]

            args = argparse.Namespace(
                command="heartbeat-loop",
                interval_seconds=1,
                ttl_seconds=300,
                parent_pid=0,
                state_file=str(state_file),
                failure_json_out=str(failure_file),
                shutdown_json_out=str(shutdown_file),
                identity_json_out=str(identity_file),
                token_stdin=False,
            )

            ret = lease.heartbeat_loop(args, mgr)
            self.assertEqual(ret, 75)
            self.assertTrue(failure_file.exists())
            failure = json.loads(failure_file.read_text(encoding="utf-8"))
            self.assertEqual(failure["status"], "lost")


class RateLimitHandlingTests(unittest.TestCase):
    def setUp(self):
        jitter = mock.patch.object(lease.random, "uniform", return_value=2.0)
        jitter.start()
        self.addCleanup(jitter.stop)

    def test_secondary_limit_on_acquisition_put_rereads_remote_cas_before_restore(self):
        client = FakeClient()
        client.state = state_for(heartbeat=NOW - timedelta(seconds=400))
        client.content_sha = "a" * 40
        original_put = client.put_content
        attempted_shas = []
        def limited_once(*args, **kwargs):
            attempted_shas.append(kwargs["expected_sha"])
            if len(attempted_shas) == 1:
                raise lease.GitHubRateLimitError(429, "secondary rate limit",
                                               retry_after_seconds=42, server_now=client.now)
            return original_put(*args, **kwargs)
        client.put_content = limited_once
        def reset(seconds):
            client.now += timedelta(seconds=seconds)
            client.content_sha = "b" * 40
        client.sleep = reset
        mgr = manager(client)
        state, sha, _ = mgr.acquire(mode="deployment", owner="deploy:rollback",
                                   ttl_seconds=300, wait_seconds=3900, poll_seconds=5)
        verified = mgr.verify(lease.public_state(state, content_sha=sha), max_heartbeat_age_seconds=120)
        restore = mock.Mock()
        # Restore may only be called with the verified, freshly acquired ID.
        if verified["status"] == "verified":
            restore(verified["leaseId"])
        self.assertEqual(attempted_shas, ["a" * 40, "b" * 40])
        restore.assert_called_once_with(state["leaseId"])
        self.assertEqual(state["previousContentSha"], "b" * 40)

    def test_permission_denials_never_wait_or_authorize_quota_acquire_or_verify(self):
        for status in (401, 403):
            for phase in ("quota", "acquire", "verify"):
                with self.subTest(status=status, phase=phase):
                    client = FakeClient()
                    client.state = state_for(owner="deploy:rollback")
                    client.content_sha = "a" * 40
                    denied = lease.classify_github_http_error(status, "Permission denied")
                    mgr = manager(client)
                    if phase == "quota":
                        client.get_rate_limit = mock.Mock(side_effect=denied)
                        call = lambda: mgr.wait_for_core_quota()
                    elif phase == "acquire":
                        client.get_ref = mock.Mock(side_effect=denied)
                        call = lambda: mgr.acquire(mode="deployment", owner="deploy:rollback",
                                                  ttl_seconds=300, wait_seconds=3900, poll_seconds=5)
                    else:
                        client.get_content = mock.Mock(side_effect=denied)
                        call = lambda: mgr.verify(client.state, max_heartbeat_age_seconds=120,
                                                 rate_limit_wait_seconds=3900)
                    with mock.patch.object(mgr, "_sleep") as sleep:
                        with self.assertRaises(lease.GitHubApiError):
                            call()
                    sleep.assert_not_called()
                    self.assertEqual(client.put_expected_shas, [])

    def test_guard_verification_does_not_wait_or_reacquire_for_rollback_owner(self):
        client = FakeClient()
        local = state_for(owner="deploy:rollback")
        client.get_content = mock.Mock(side_effect=lease.GitHubRateLimitError(
            429, "secondary rate limit", retry_after_seconds=60, server_now=NOW))
        mgr = manager(client)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(mgr, "_sleep") as sleep:
            with self.assertRaises(lease.GitHubRateLimitError):
                mgr.verify(local, max_heartbeat_age_seconds=120)
        sleep.assert_not_called()
        self.assertEqual(client.put_expected_shas, [])

    def test_verification_after_reset_does_not_reacquire_a_foreign_lease(self):
        client = FakeClient()
        local = state_for(owner="deploy:rollback")
        foreign = state_for(owner="foreign", lease_id="22222222-2222-4222-8222-222222222222")
        client.get_content = mock.Mock(side_effect=[
            lease.GitHubRateLimitError(429, "secondary rate limit", retry_after_seconds=10, server_now=NOW),
            lease.RemoteContent(state=foreign, content_sha="f" * 40, server_now=NOW),
        ])
        mgr = manager(client)
        with mock.patch.object(mgr, "_sleep") as sleep:
            with self.assertRaises(lease.LeaseLost):
                mgr.verify(local, max_heartbeat_age_seconds=120, rate_limit_wait_seconds=3900)
        sleep.assert_called_once()
        self.assertEqual(client.put_expected_shas, [])

    def test_http_transport_preserves_rate_limit_headers(self):
        for status, headers in ((403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(NOW.timestamp()) + 120)}),
                                (429, {"Retry-After": "42"})):
            with self.subTest(status=status):
                headers["Date"] = "Mon, 13 Jul 2026 16:30:00 GMT"
                error = urllib.error.HTTPError("https://api.github.com/test", status, "rate limit", headers,
                                              io.BytesIO(b'{"message":"API rate limit exceeded"}'))
                client = lease.GitHubClient("fixture-token")
                with mock.patch.object(lease.urllib.request, "urlopen", side_effect=error):
                    with self.assertRaises(lease.GitHubRateLimitError) as caught:
                        client._request("GET", "/test", allowed=(200,))
                self.assertEqual(caught.exception.status, status)
                self.assertEqual(caught.exception.server_now, NOW)

    def test_classify_primary_403_secondary_429_and_plain_auth_denial(self) -> None:
        reset_time = datetime(2026, 11, 14, 7, 58, 56, tzinfo=timezone.utc)
        server_now = datetime(2026, 11, 14, 7, 50, 54, tzinfo=timezone.utc)

        primary_err = lease.classify_github_http_error(
            403,
            "API rate limit exceeded for user ID 169176954.",
            '{"message":"API rate limit exceeded for user ID 169176954."}',
            {
                "Date": "Sat, 14 Nov 2026 07:50:54 GMT",
                "X-RateLimit-Limit": "5000",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(int(reset_time.timestamp())),
            },
        )
        self.assertIsInstance(primary_err, lease.GitHubRateLimitError)
        assert isinstance(primary_err, lease.GitHubRateLimitError)
        self.assertTrue(lease.is_rate_limit_error(primary_err))
        self.assertFalse(lease.is_transient_error(primary_err))
        self.assertEqual(primary_err.reason, "rate_limited")
        self.assertEqual(primary_err.rate_limit_kind, "primary")
        self.assertEqual(primary_err.remaining, 0)
        self.assertEqual(primary_err.reset_at, reset_time)
        self.assertEqual(primary_err.server_now, server_now)

        secondary_429 = lease.classify_github_http_error(
            429,
            "You have exceeded a secondary rate limit. Please wait a few minutes before you try again.",
            '{"message":"You have exceeded a secondary rate limit."}',
            {
                "Date": "Sat, 14 Nov 2026 07:50:54 GMT",
                "Retry-After": "42",
            },
        )
        self.assertIsInstance(secondary_429, lease.GitHubRateLimitError)
        assert isinstance(secondary_429, lease.GitHubRateLimitError)
        self.assertTrue(lease.is_rate_limit_error(secondary_429))
        self.assertFalse(lease.is_transient_error(secondary_429))
        self.assertEqual(secondary_429.reason, "rate_limited")
        self.assertEqual(secondary_429.rate_limit_kind, "secondary")
        self.assertEqual(secondary_429.retry_after_seconds, 42.0)

        secondary_403_retry_after = lease.classify_github_http_error(
            403,
            "Forbidden",
            '{"message":"Forbidden"}',
            {
                "Date": "Sat, 14 Nov 2026 07:50:54 GMT",
                "X-RateLimit-Remaining": "4200",
                "Retry-After": "30",
            },
        )
        self.assertIsInstance(secondary_403_retry_after, lease.GitHubRateLimitError)
        assert isinstance(secondary_403_retry_after, lease.GitHubRateLimitError)
        self.assertEqual(secondary_403_retry_after.rate_limit_kind, "secondary")
        self.assertEqual(secondary_403_retry_after.retry_after_seconds, 30.0)

        for status, msg, headers in (
            (
                401,
                "Bad credentials",
                {"Date": "Sat, 14 Nov 2026 07:50:54 GMT"},
            ),
            (
                403,
                "Resource not accessible by integration",
                {
                    "Date": "Sat, 14 Nov 2026 07:50:54 GMT",
                    "X-RateLimit-Remaining": "4912",
                    "X-RateLimit-Reset": str(int(reset_time.timestamp())),
                },
            ),
            (
                404,
                "Not Found",
                {"Date": "Sat, 14 Nov 2026 07:50:54 GMT"},
            ),
            (
                409,
                "Conflict",
                {"Date": "Sat, 14 Nov 2026 07:50:54 GMT"},
            ),
            (
                422,
                "Unprocessable Entity",
                {"Date": "Sat, 14 Nov 2026 07:50:54 GMT"},
            ),
        ):
            with self.subTest(status=status, msg=msg):
                plain_err = lease.classify_github_http_error(
                    status,
                    msg,
                    json.dumps({"message": msg}),
                    headers,
                )
                self.assertIsInstance(plain_err, lease.GitHubApiError)
                self.assertNotIsInstance(plain_err, lease.GitHubRateLimitError)
                self.assertFalse(lease.is_rate_limit_error(plain_err))
                self.assertFalse(lease.is_transient_error(plain_err))

    def test_root_43569948128_heartbeat_rate_limit_loss_then_compensation_waits_and_restores_with_fresh_cas(
        self,
    ) -> None:
        # Root 43569948128 timeline on 2026-11-14:
        # - 07:49:54Z: last verified heartbeat (expiresAt 07:54:54Z, freshness deadline 07:51:54Z)
        # - 07:50:54Z: heartbeat renewal hits 403 API rate limit exceeded (reset at 07:58:56Z, 482s away > 60s remaining freshness)
        # - 07:54:04Z: compensation rollback lease acquisition hits the same 403 rate limit until 07:58:56Z
        hb_at = datetime(2026, 11, 14, 7, 49, 54, tzinfo=timezone.utc)
        exp_at = datetime(2026, 11, 14, 7, 54, 54, tzinfo=timezone.utc)
        fail_at = datetime(2026, 11, 14, 7, 50, 54, tzinfo=timezone.utc)
        comp_start = datetime(2026, 11, 14, 7, 54, 4, tzinfo=timezone.utc)
        reset_at = datetime(2026, 11, 14, 7, 58, 56, tzinfo=timezone.utc)

        deploy_active_sha = "1" * 40
        client = FakeClient()
        client.now = fail_at
        client.state = state_for(
            owner="pantheon:deploy-43569948128",
            mode="deployment",
            heartbeat=hb_at,
            expires=exp_at,
        )
        client.content_sha = deploy_active_sha
        mgr = manager(client)

        rate_limit_403 = lease.GitHubRateLimitError(
            403,
            "API rate limit exceeded for user ID 169176954.",
            '{"message":"API rate limit exceeded for user ID 169176954."}',
            rate_limit_kind="primary",
            remaining=0,
            reset_at=reset_at,
            server_now=fail_at,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = root / "state.json"
            failure_file = root / "failure.json"
            rollback_state_file = root / "rollback-state.json"
            lease.atomic_write_json(
                state_file,
                lease.public_state(client.state, content_sha=deploy_active_sha),
                0o600,
            )

            # Phase 1: At 07:50:54Z, heartbeat renewal hits 403 primary rate limit.
            # Remaining verified freshness is 60s (until 07:51:54Z), while reset is 482s away (07:58:56Z).
            # Heartbeat must NOT wait past verified freshness; it immediately reports lost with reason="rate_limited".
            original_get_content = client.get_content
            client.get_content = mock.MagicMock(side_effect=rate_limit_403)  # type: ignore[method-assign]

            hb_args = lease.build_parser().parse_args(
                [
                    "heartbeat-loop",
                    "--state-file",
                    str(state_file),
                    "--interval-seconds",
                    "60",
                    "--failure-json-out",
                    str(failure_file),
                ]
            )
            with mock.patch.object(lease.signal, "signal"), mock.patch.object(
                lease.time, "sleep"
            ) as hb_sleep:
                hb_exit = lease.heartbeat_loop(hb_args, mgr)

            self.assertEqual(hb_exit, 75)
            # Only the normal 60s interval sleep occurred before the single rate-limited renewal attempt.
            self.assertEqual(sum(call.args[0] for call in hb_sleep.call_args_list), 60.0)
            failure_payload = json.loads(failure_file.read_text(encoding="utf-8"))
            self.assertEqual(failure_payload["status"], "lost")
            self.assertEqual(failure_payload["reason"], "rate_limited")
            self.assertEqual(failure_payload["rateLimitKind"], "primary")
            self.assertEqual(failure_payload["rateLimitResetAt"], "2026-11-14T07:58:56Z")

            # Phase 2: At 07:54:04Z, compensation runs rollback lease acquire + verify.
            # While client.now < reset_at (07:58:56Z), GitHub returns 403 rate limit.
            # When compensation sleeps, clock advances past 07:58:56Z (and past predecessor expiresAt 07:54:54Z).
            client.now = comp_start
            slept_intervals: list[float] = []

            def advance_clock(seconds: float) -> None:
                slept_intervals.append(seconds)
                client.now = client.now + timedelta(seconds=seconds)

            client.sleep = advance_clock  # type: ignore[attr-defined]

            def rate_limited_get_ref(repository: str, branch: str):
                if client.now < reset_at:
                    raise lease.GitHubRateLimitError(
                        403,
                        "API rate limit exceeded for user ID 169176954.",
                        rate_limit_kind="primary",
                        remaining=0,
                        reset_at=reset_at,
                        server_now=client.now,
                    )
                return client.refs.get(branch), client.now

            def rate_limited_get_content(repository: str, branch: str, path: str):
                if client.now < reset_at:
                    raise lease.GitHubRateLimitError(
                        403,
                        "API rate limit exceeded for user ID 169176954.",
                        rate_limit_kind="primary",
                        remaining=0,
                        reset_at=reset_at,
                        server_now=client.now,
                    )
                return original_get_content(repository, branch, path)

            client.get_ref = rate_limited_get_ref  # type: ignore[method-assign]
            client.get_content = rate_limited_get_content  # type: ignore[method-assign]

            comp_mgr = manager(client)
            acquired_state, acquired_sha, written_at = comp_mgr.acquire(
                mode="deployment",
                owner="pantheon:deploy-43569948128:rollback",
                ttl_seconds=300,
                wait_seconds=3900,
                poll_seconds=5.0,
                expected_backend_sha="b" * 40,
                run_url="https://github.com/ajoe734/pantheon/actions/runs/43569948128",
            )
            # Wait was 292s (07:54:04Z -> 07:58:56Z) + 2s bounded jitter = 294s.
            self.assertEqual(slept_intervals, [294.0])
            self.assertEqual(client.now, datetime(2026, 11, 14, 7, 58, 58, tzinfo=timezone.utc))
            self.assertEqual(written_at, datetime(2026, 11, 14, 7, 58, 58, tzinfo=timezone.utc))
            # Fresh CAS replaced the expired predecessor deploy_active_sha with a fresh leaseId.
            self.assertEqual(client.put_expected_shas, [deploy_active_sha])
            self.assertEqual(acquired_state["previousContentSha"], deploy_active_sha)
            self.assertNotEqual(acquired_state["leaseId"], "11111111-1111-4111-8111-111111111111")
            self.assertEqual(acquired_state["owner"], "pantheon:deploy-43569948128:rollback")

            local_rollback = lease.public_state(acquired_state, content_sha=acquired_sha)
            lease.atomic_write_json(rollback_state_file, local_rollback, 0o600)

            verified = comp_mgr.verify(
                local_rollback,
                max_heartbeat_age_seconds=120,
                initial_visibility_wait_seconds=15.0,
                initial_visibility_poll_seconds=1.0,
                state_file=str(rollback_state_file),
            )
            self.assertEqual(verified["status"], "verified")
            self.assertEqual(verified["owner"], "pantheon:deploy-43569948128:rollback")
            self.assertEqual(verified["leaseId"], acquired_state["leaseId"])

    def test_heartbeat_retries_short_secondary_rate_limit_only_within_verified_freshness(
        self,
    ) -> None:
        client = FakeClient()
        # 50s of verified freshness remaining (heartbeat 70s ago, max freshness 120s).
        hb_at = NOW - timedelta(seconds=70)
        exp_at = NOW + timedelta(seconds=230)
        client.state = state_for(heartbeat=hb_at, expires=exp_at)
        client.content_sha = "blob-initial"
        mgr = manager(client)

        original_put = client.put_content
        put_attempts = 0

        def rate_limited_then_ok_put(*args, **kwargs):
            nonlocal put_attempts
            put_attempts += 1
            if put_attempts == 1:
                raise lease.GitHubRateLimitError(
                    429,
                    "You have exceeded a secondary rate limit.",
                    rate_limit_kind="secondary",
                    retry_after_seconds=10.0,
                    server_now=client.now,
                )
            res = original_put(*args, **kwargs)
            self.stop_loop()
            return res

        client.put_content = rate_limited_then_ok_put  # type: ignore[method-assign]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = root / "state.json"
            failure_file = root / "failure.json"
            lease.atomic_write_json(
                state_file,
                lease.public_state(client.state, content_sha="blob-initial"),
                0o600,
            )
            args = lease.build_parser().parse_args(
                [
                    "heartbeat-loop",
                    "--state-file",
                    str(state_file),
                    "--interval-seconds",
                    "60",
                    "--failure-json-out",
                    str(failure_file),
                ]
            )
            handlers = {}
            self.stop_loop = lambda: handlers[signal.SIGTERM](signal.SIGTERM, None)
            with mock.patch.object(
                lease.signal,
                "signal",
                side_effect=lambda sig, handler: handlers.update({sig: handler}),
            ), mock.patch.object(lease.time, "sleep") as sleep_mock:
                rc = lease.heartbeat_loop(args, mgr)

            self.assertEqual(rc, 0)
            self.assertFalse(failure_file.exists())
            # 60s initial interval + (10s retry-after + 2s jitter) = 72s
            self.assertAlmostEqual(
                sum(call.args[0] for call in sleep_mock.call_args_list), 72.0
            )
            saved = lease.read_json_file(state_file, "state")
            self.assertEqual(saved["contentSha"], "blob-1")

    def test_compensation_verify_waits_for_rate_limit_and_reacquires_fresh_lease_when_ttl_expired(
        self,
    ) -> None:
        client = FakeClient()
        client.now = datetime(2026, 11, 14, 8, 0, 0, tzinfo=timezone.utc)
        mgr = manager(client)

        initial_state, _, _ = mgr.acquire(
            mode="deployment",
            owner="pantheon:deploy-99:rollback",
            ttl_seconds=300,
            wait_seconds=60,
            poll_seconds=5.0,
            expected_backend_sha="a" * 40,
        )
        initial_sha = "2" * 40
        client.content_sha = initial_sha
        # During initial verify, GitHub rate-limits for 400s (longer than the 300s TTL).
        reset_at = client.now + timedelta(seconds=400)
        original_get_content = client.get_content
        verify_get_calls = 0

        def get_content_rate_limited_once(repository: str, branch: str, path: str):
            nonlocal verify_get_calls
            verify_get_calls += 1
            if verify_get_calls == 1:
                raise lease.GitHubRateLimitError(
                    403,
                    "API rate limit exceeded",
                    rate_limit_kind="primary",
                    remaining=0,
                    reset_at=reset_at,
                    server_now=client.now,
                )
            return original_get_content(repository, branch, path)

        client.get_content = get_content_rate_limited_once  # type: ignore[method-assign]
        slept: list[float] = []

        def advance_clock(seconds: float) -> None:
            slept.append(seconds)
            client.now = client.now + timedelta(seconds=seconds)

        client.sleep = advance_clock  # type: ignore[attr-defined]

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "rollback-state.json"
            local_state = lease.public_state(initial_state, content_sha=initial_sha)
            lease.atomic_write_json(state_file, local_state, 0o600)

            verified = mgr.verify(
                local_state,
                max_heartbeat_age_seconds=120,
                initial_visibility_wait_seconds=15.0,
                initial_visibility_poll_seconds=1.0,
                rate_limit_wait_seconds=3900.0,
                ttl_seconds=300,
                state_file=str(state_file),
            )
            self.assertEqual(slept, [402.0])
            self.assertEqual(verified["status"], "verified")
            # Re-acquired a fresh lease with a new leaseId and fresh CAS after waiting out the 400s reset.
            self.assertNotEqual(verified["leaseId"], initial_state["leaseId"])
            self.assertEqual(client.put_expected_shas, [None, initial_sha])
            persisted = lease.read_json_file(state_file, "rollback state")
            self.assertEqual(persisted["leaseId"], verified["leaseId"])
            self.assertEqual(local_state["leaseId"], verified["leaseId"])

    def test_reset_beyond_budget_fails_with_rate_limited_rollback_pending_exit_79(
        self,
    ) -> None:
        client = FakeClient()
        client.now = datetime(2026, 11, 14, 7, 54, 4, tzinfo=timezone.utc)
        # Reset is 4000s in the future, exceeding the 3900s compensation budget.
        reset_beyond = client.now + timedelta(seconds=4000)

        def always_rate_limited(*args, **kwargs):
            raise lease.GitHubRateLimitError(
                403,
                "API rate limit exceeded for user ID 169176954.",
                rate_limit_kind="primary",
                remaining=0,
                reset_at=reset_beyond,
                server_now=client.now,
            )

        client.get_ref = always_rate_limited  # type: ignore[method-assign]
        mgr = manager(client)

        with self.assertRaises(lease.RateLimitedRollbackPending) as ctx:
            mgr.acquire(
                mode="deployment",
                owner="pantheon:deploy-43569948128:rollback",
                ttl_seconds=300,
                wait_seconds=3900,
                poll_seconds=5.0,
            )
        self.assertIn("rate_limited_rollback_pending", str(ctx.exception))

        # Verify main() translates RateLimitedRollbackPending to exit code 79 and explicit reason on stderr.
        stderr_buf = io.StringIO()
        with mock.patch.object(
            lease, "run", side_effect=ctx.exception
        ), mock.patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as exit_ctx:
                lease.main()
        self.assertEqual(
            exit_ctx.exception.code, lease.RATE_LIMITED_ROLLBACK_PENDING_EXIT_CODE
        )
        self.assertEqual(exit_ctx.exception.code, 79)
        self.assertIn(
            "environment lease rate_limited_rollback_pending:",
            stderr_buf.getvalue(),
        )

    def test_wait_for_core_quota_waits_when_below_default_1500_and_never_fails(
        self,
    ) -> None:
        client = FakeClient()
        client.now = datetime(2026, 11, 14, 7, 45, 0, tzinfo=timezone.utc)
        reset_at = datetime(2026, 11, 14, 7, 58, 56, tzinfo=timezone.utc)
        slept: list[float] = []

        def advance_clock(seconds: float) -> None:
            slept.append(seconds)
            client.now = client.now + timedelta(seconds=seconds)

        client.sleep = advance_clock  # type: ignore[attr-defined]
        calls = 0

        def fake_get_rate_limit() -> dict:
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "limit": 5000,
                    "remaining": 842,
                    "resetEpoch": int(reset_at.timestamp()),
                    "resetAt": reset_at,
                    "serverNow": client.now,
                }
            return {
                "limit": 5000,
                "remaining": 5000,
                "resetEpoch": int((client.now + timedelta(hours=1)).timestamp()),
                "resetAt": client.now + timedelta(hours=1),
                "serverNow": client.now,
            }

        client.get_rate_limit = fake_get_rate_limit  # type: ignore[attr-defined]
        mgr = manager(client)

        stderr_buf = io.StringIO()
        with mock.patch("sys.stderr", stderr_buf):
            result = mgr.wait_for_core_quota()

        self.assertEqual(result["status"], "quota_ready")
        self.assertEqual(result["minRemaining"], 1500)
        self.assertEqual(result["remaining"], 5000)
        self.assertEqual(result["attempts"], 2)
        # 836s until 07:58:56Z + 2s jitter = 838s
        self.assertEqual(slept, [838.0])
        logs = [
            json.loads(line)
            for line in stderr_buf.getvalue().strip().splitlines()
            if line.strip()
        ]
        statuses = [entry["status"] for entry in logs]
        self.assertEqual(
            statuses,
            ["quota_observed", "quota_waiting", "quota_observed"],
        )
        self.assertEqual(logs[0]["remaining"], 842)
        self.assertEqual(logs[1]["waitSeconds"], 838.0)
        self.assertEqual(logs[2]["remaining"], 5000)


if __name__ == "__main__":
    unittest.main()

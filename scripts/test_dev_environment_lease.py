from __future__ import annotations

import copy
import json
import os
import signal
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

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

now = datetime.now(timezone.utc)
client = FakeClient()
client.now = now
client.state = state_for(heartbeat=now, expires=now + timedelta(minutes=5))
client.content_sha = "blob-1"
mgr = manager(client)

marker = Path({str(flaky_marker)!r})
original_heartbeat = mgr.heartbeat

def flaky_heartbeat(*args, **kwargs):
    count = int(marker.read_text().strip() or "0") + 1
    marker.write_text(f"{{count}}\\n")
    if count == 1:
        raise lease.GitHubApiError(500, "Simulated 500")
    return original_heartbeat(*args, **kwargs)

mgr.heartbeat = flaky_heartbeat

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
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    if int(flaky_marker.read_text().strip() or "0") >= 2:
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
            self.assertGreaterEqual(int(flaky_marker.read_text().strip()), 2)

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


if __name__ == "__main__":
    unittest.main()

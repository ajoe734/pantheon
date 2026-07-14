from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import dev_environment_lease as lease


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


if __name__ == "__main__":
    unittest.main()

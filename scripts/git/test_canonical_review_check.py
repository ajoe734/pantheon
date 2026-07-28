#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import canonical_review_check as check


PRIVATE_KEY = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc4"
    "4449c5697b326919703bac031cae7f60"
)
PUBLIC_KEY = bytes.fromhex(
    "d75a980182b10ab7d54bfed3c964073a"
    "0ee172f3daa62325af021a68f707511a"
)
OTHER_PRIVATE_KEY = bytes.fromhex(
    "4ccd089b28ff96da9db6c346ec114e0f"
    "5b8a319f35aba624da8cf6ed4fb8a6fb"
)
HEAD = "1" * 40
OTHER_HEAD = "2" * 40
NOW = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
APPROVED_AT = NOW - timedelta(minutes=10)


def pr_payload(
    *,
    number: int = 88,
    head_sha: str = HEAD,
    head_branch: str = "task/ABC-001",
    base: str = "dev",
    draft: bool = False,
    state: str = "open",
) -> dict[str, Any]:
    return {
        "number": number,
        "state": state,
        "draft": draft,
        "user": {"login": "shared-owner"},
        "head": {"sha": head_sha, "ref": head_branch},
        "base": {"ref": base},
    }


def gh_pr_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "number": 88,
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": HEAD,
        "headRefName": "task/ABC-001",
        "baseRefName": "dev",
        "commits": [
            {
                "authoredDate": "2026-07-28T14:40:00Z",
                "committedDate": "2026-07-28T14:40:00Z",
            }
        ],
        "autoMergeRequest": None,
    }
    payload.update(overrides)
    return payload


def trusted_keys(
    *,
    reviewer: str = "Claude",
    public_key: bytes = PUBLIC_KEY,
) -> dict[str, check.TrustedKey]:
    return check.load_trusted_keys(
        {
            "keys": {
                "claude-2026-07": {
                    "reviewer": reviewer,
                    "public_key_base64": base64.b64encode(public_key).decode(),
                }
            }
        }
    )


def payload(
    *,
    decision: str = check.APPROVE,
    repository: str = "ajoe734/pantheon",
    task_id: str = "ABC-001",
    pr: int = 88,
    head_sha: str = HEAD,
    head_branch: str = "task/ABC-001",
    base: str = "dev",
    owner: str = "Codex",
    reviewer: str = "Claude",
    issued_at: datetime = NOW - timedelta(minutes=5),
    expires_at: datetime = NOW + timedelta(minutes=55),
    canonical_status: str | None = None,
) -> dict[str, Any]:
    return {
        "approval_event_at": check._format_time(APPROVED_AT),
        "base": base,
        "canonical_record_sha256": "a" * 64,
        "canonical_source": "active",
        "canonical_status": canonical_status
        or ("review_approved" if decision == check.APPROVE else "in_progress"),
        "decision": decision,
        "expires_at": check._format_time(expires_at),
        "head_branch": head_branch,
        "head_sha": head_sha,
        "issued_at": check._format_time(issued_at),
        "nonce": "review-00000000-0000-4000-8000-000000000001",
        "owner": owner,
        "pr": pr,
        "repository": repository,
        "review_message_sha256": "b" * 64,
        "reviewer": reviewer,
        "schema": check.SCHEMA,
        "task_id": task_id,
    }


def signed_comment(
    raw_payload: dict[str, Any],
    *,
    private_key: bytes = PRIVATE_KEY,
    key_id: str = "claude-2026-07",
    comment_id: int = 10,
) -> dict[str, Any]:
    encoded = check.encode_envelope(
        payload=raw_payload,
        key_id=key_id,
        private_key=private_key,
    )
    return {
        "id": comment_id,
        "body": check.format_comment(encoded, payload=raw_payload),
    }


class ExactHeadAttestationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pr = check.PullRequestIdentity.from_mapping(
            pr_payload(),
            repository="ajoe734/pantheon",
        )
        self.keys = trusted_keys()

    def evaluate(self, comments: list[dict[str, Any]]) -> check.CheckResult:
        return check.evaluate_comments(
            pr=self.pr,
            comments=comments,
            trusted_keys=self.keys,
            now=NOW,
        )

    def test_exact_head_independent_approval_passes(self) -> None:
        result = self.evaluate([signed_comment(payload())])
        self.assertTrue(result.passed)
        self.assertEqual(result.reason, "exact_head_independently_approved")
        self.assertEqual(result.reviewer, "Claude")
        self.assertEqual(result.comment_id, 10)

    def test_missing_attestation_fails_closed(self) -> None:
        result = self.evaluate([])
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "missing_attestation")

    def test_newer_signed_rejection_overrides_approval(self) -> None:
        approved = payload(issued_at=NOW - timedelta(minutes=8))
        rejected = payload(
            decision=check.REJECT,
            issued_at=NOW - timedelta(minutes=2),
            expires_at=NOW + timedelta(minutes=30),
        )
        result = self.evaluate(
            [
                signed_comment(approved, comment_id=10),
                signed_comment(rejected, comment_id=11),
            ]
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "attestation_rejected")
        self.assertEqual(result.comment_id, 11)

    def test_stale_head_attestation_is_rejected(self) -> None:
        result = self.evaluate(
            [signed_comment(payload(head_sha=OTHER_HEAD))]
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "stale_head_attestation")

    def test_attestation_cannot_replay_on_another_pr(self) -> None:
        result = self.evaluate([signed_comment(payload(pr=89))])
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "stale_head_attestation")

    def test_attestation_cannot_replay_on_another_repository(self) -> None:
        result = self.evaluate(
            [signed_comment(payload(repository="ajoe734/execute-plans"))]
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "stale_head_attestation")

    def test_self_owned_attestation_is_rejected_even_when_signed(self) -> None:
        result = self.evaluate(
            [signed_comment(payload(owner="Claude", reviewer="Claude"))]
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "self_owned_attestation")

    def test_key_cannot_impersonate_another_reviewer(self) -> None:
        result = self.evaluate(
            [signed_comment(payload(reviewer="Codex2"))]
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "reviewer_key_mismatch")

    def test_forged_payload_fails_signature_verification(self) -> None:
        original = payload()
        encoded = check.encode_envelope(
            payload=original,
            key_id="claude-2026-07",
            private_key=PRIVATE_KEY,
        )
        envelope = check.decode_envelope(encoded)
        envelope["payload"]["head_sha"] = OTHER_HEAD
        forged = check._b64url_encode(check.canonical_json_bytes(envelope))
        comment = {
            "id": 12,
            "body": f"{check.MARKER_PREFIX}{forged}{check.MARKER_SUFFIX}",
        }
        result = self.evaluate([comment])
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "signature_invalid")

    def test_untrusted_signer_fails(self) -> None:
        result = self.evaluate(
            [
                signed_comment(
                    payload(),
                    private_key=OTHER_PRIVATE_KEY,
                    key_id="unknown",
                )
            ]
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "untrusted_reviewer_key")

    def test_expired_approval_fails_closed(self) -> None:
        result = self.evaluate(
            [
                signed_comment(
                    payload(
                        issued_at=NOW - timedelta(minutes=9),
                        expires_at=NOW - timedelta(minutes=5),
                    )
                )
            ]
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "attestation_expired")

    def test_future_attestation_is_invalid(self) -> None:
        result = self.evaluate(
            [
                signed_comment(
                    payload(
                        issued_at=NOW + timedelta(minutes=5),
                        expires_at=NOW + timedelta(hours=1),
                    )
                )
            ]
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "attestation_time_invalid")

    def test_conflicting_latest_decisions_fail_closed(self) -> None:
        issued = NOW - timedelta(minutes=1)
        approved = payload(issued_at=issued)
        rejected = payload(
            decision=check.REJECT,
            issued_at=issued,
            expires_at=NOW + timedelta(minutes=30),
        )
        result = self.evaluate(
            [
                signed_comment(approved, comment_id=20),
                signed_comment(rejected, comment_id=21),
            ]
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "ambiguous_attestation")

    def test_draft_pr_fails_before_attestation(self) -> None:
        draft = check.PullRequestIdentity.from_mapping(
            pr_payload(draft=True),
            repository="ajoe734/pantheon",
        )
        result = check.evaluate_comments(
            pr=draft,
            comments=[signed_comment(payload())],
            trusted_keys=self.keys,
            now=NOW,
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "pr_is_draft")

    def test_paginated_comment_payload_is_flattened(self) -> None:
        comments = check._flatten_comments(
            [[signed_comment(payload())], [{"id": 99, "body": "noise"}]]
        )
        self.assertEqual(len(comments), 2)
        self.assertTrue(self.evaluate(comments).passed)


class CanonicalIssuerTests(unittest.TestCase):
    def write_state(
        self,
        root: Path,
        *,
        status: str = "review_approved",
        with_reopen: bool = False,
    ) -> None:
        task = {
            "id": "ABC-001",
            "owner": "Codex",
            "reviewer": "Claude",
            "status": status,
        }
        (root / "ai-status.json").write_text(
            json.dumps({"tasks": [task]}),
            encoding="utf-8",
        )
        events = [
            {
                "ts": check._format_time(APPROVED_AT),
                "agent": "Claude",
                "type": "review_approved",
                "task_id": "ABC-001",
                "message": "approved exact head",
                "review_binding": {
                    "pr": 88,
                    "head_sha": HEAD,
                    "head_branch": "task/ABC-001",
                    "base": "dev",
                },
            }
        ]
        if with_reopen:
            events.append(
                {
                    "ts": check._format_time(NOW - timedelta(minutes=3)),
                    "agent": "Claude",
                    "type": "reopen",
                    "task_id": "ABC-001",
                    "message": "changes required",
                }
            )
        (root / "ai-activity-log.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in events),
            encoding="utf-8",
        )

    def write_signer(self, path: Path, *, reviewer: str = "Claude") -> None:
        path.write_text(
            json.dumps(
                {
                    "key_id": "claude-2026-07",
                    "reviewer": reviewer,
                    "private_key_base64": base64.b64encode(PRIVATE_KEY).decode(),
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)

    def test_issuer_requires_and_transports_canonical_exact_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status_root = root / "status"
            protected_root = root / "protected"
            status_root.mkdir()
            protected_root.mkdir()
            self.write_state(status_root)
            key_file = protected_root / "reviewer-key.json"
            self.write_signer(key_file)
            issued_payload, comment_body = check.issue_from_canonical_state(
                repository="ajoe734/pantheon",
                task_id="ABC-001",
                actor="Claude",
                decision=check.APPROVE,
                message="independent review passed",
                pr_json=gh_pr_payload(),
                status_root=status_root,
                signer_key_file=key_file,
                now=NOW,
                nonce="review-00000000-0000-4000-8000-000000000002",
            )
            result = check.evaluate_comments(
                pr=check.PullRequestIdentity.from_mapping(
                    pr_payload(),
                    repository="ajoe734/pantheon",
                ),
                comments=[{"id": 50, "body": comment_body}],
                trusted_keys=trusted_keys(),
                now=NOW,
            )
            self.assertTrue(result.passed)
            self.assertEqual(issued_payload["canonical_status"], "review_approved")

    def test_owner_cannot_issue_with_reviewers_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status_root = root / "status"
            protected_root = root / "protected"
            status_root.mkdir()
            protected_root.mkdir()
            self.write_state(status_root)
            key_file = protected_root / "reviewer-key.json"
            self.write_signer(key_file)
            with self.assertRaisesRegex(
                check.CanonicalReviewError,
                "canonical reviewer",
            ):
                check.issue_from_canonical_state(
                    repository="ajoe734/pantheon",
                    task_id="ABC-001",
                    actor="Codex",
                    decision=check.APPROVE,
                    message="forged owner approval",
                    pr_json=gh_pr_payload(),
                    status_root=status_root,
                    signer_key_file=key_file,
                    now=NOW,
                )

    def test_reviewer_can_issue_canonical_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status_root = root / "status"
            protected_root = root / "protected"
            status_root.mkdir()
            protected_root.mkdir()
            self.write_state(
                status_root,
                status="in_progress",
                with_reopen=True,
            )
            key_file = protected_root / "reviewer-key.json"
            self.write_signer(key_file)
            _, comment_body = check.issue_from_canonical_state(
                repository="ajoe734/pantheon",
                task_id="ABC-001",
                actor="Claude",
                decision=check.REJECT,
                message="changes required",
                pr_json=gh_pr_payload(),
                status_root=status_root,
                signer_key_file=key_file,
                now=NOW,
            )
            result = check.evaluate_comments(
                pr=check.PullRequestIdentity.from_mapping(
                    pr_payload(),
                    repository="ajoe734/pantheon",
                ),
                comments=[{"id": 51, "body": comment_body}],
                trusted_keys=trusted_keys(),
                now=NOW,
            )
            self.assertFalse(result.passed)
            self.assertEqual(result.reason, "attestation_rejected")

    def test_signer_key_inside_status_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status_root = Path(tmp)
            self.write_state(status_root)
            key_file = status_root / "reviewer-key.json"
            self.write_signer(key_file)
            with self.assertRaisesRegex(
                check.CanonicalReviewError,
                "candidate-controlled",
            ):
                check.issue_from_canonical_state(
                    repository="ajoe734/pantheon",
                    task_id="ABC-001",
                    actor="Claude",
                    decision=check.APPROVE,
                    message="approved",
                    pr_json=gh_pr_payload(),
                    status_root=status_root,
                    signer_key_file=key_file,
                    now=NOW,
                )

    def test_signer_key_permissions_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status_root = root / "status"
            protected_root = root / "protected"
            status_root.mkdir()
            protected_root.mkdir()
            self.write_state(status_root)
            key_file = protected_root / "reviewer-key.json"
            self.write_signer(key_file)
            key_file.chmod(0o644)
            with self.assertRaisesRegex(
                check.CanonicalReviewError,
                "group/other",
            ):
                check.issue_from_canonical_state(
                    repository="ajoe734/pantheon",
                    task_id="ABC-001",
                    actor="Claude",
                    decision=check.APPROVE,
                    message="approved",
                    pr_json=gh_pr_payload(),
                    status_root=status_root,
                    signer_key_file=key_file,
                    now=NOW,
                )


class ProtectionPlanTests(unittest.TestCase):
    def baseline(self, *, admins: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        protection = {
            "required_status_checks": {
                "strict": True,
                "contexts": ["Commit trailers", "Smoke acceptance"],
                "checks": [
                    {"context": "Commit trailers", "app_id": 15368},
                    {"context": "Smoke acceptance", "app_id": 15368},
                ],
            },
            "required_pull_request_reviews": {
                "required_approving_review_count": 0,
                "dismiss_stale_reviews": False,
                "require_last_push_approval": False,
            },
            "enforce_admins": {"enabled": admins},
        }
        repository = {"allow_auto_merge": True}
        return protection, repository

    def test_activation_is_app_pinned_and_rollback_exact(self) -> None:
        protection, repository = self.baseline()
        plan = check.build_protection_plan(
            repository_slug="ajoe734/pantheon",
            branch="dev",
            protection=protection,
            repository=repository,
        )
        activation_checks = plan["activation"][0]["body"]["checks"]
        self.assertIn(
            {"context": check.CHECK_NAME, "app_id": 15368},
            activation_checks,
        )
        self.assertEqual(plan["activation"][1]["method"], "POST")
        self.assertEqual(
            plan["activation"][2]["body"],
            {"allow_auto_merge": False},
        )
        self.assertEqual(
            plan["rollback"][0]["body"]["checks"],
            protection["required_status_checks"]["checks"],
        )
        self.assertEqual(plan["rollback"][1]["method"], "DELETE")
        self.assertEqual(
            plan["rollback"][2]["body"],
            {"allow_auto_merge": True},
        )

    def test_plan_preserves_existing_admin_enforcement(self) -> None:
        protection, repository = self.baseline(admins=True)
        plan = check.build_protection_plan(
            repository_slug="ajoe734/pantheon",
            branch="dev",
            protection=protection,
            repository=repository,
        )
        self.assertEqual(plan["rollback"][1]["method"], "POST")

    def test_active_readback_requires_actions_app_admins_and_no_auto_merge(self) -> None:
        protection, repository = self.baseline(admins=True)
        protection["required_status_checks"]["checks"].append(
            {"context": check.CHECK_NAME, "app_id": 15368}
        )
        repository["allow_auto_merge"] = False
        result = check.verify_active_protection(
            protection=protection,
            repository=repository,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["failures"], [])

    def test_user_owned_or_unpinned_status_is_not_accepted(self) -> None:
        protection, repository = self.baseline(admins=True)
        protection["required_status_checks"]["checks"].append(
            {"context": check.CHECK_NAME, "app_id": None}
        )
        repository["allow_auto_merge"] = False
        result = check.verify_active_protection(
            protection=protection,
            repository=repository,
        )
        self.assertFalse(result["ok"])
        self.assertIn("expected 15368", result["failures"][0])

    def test_auto_merge_or_admin_bypass_fails_readback(self) -> None:
        protection, repository = self.baseline(admins=False)
        protection["required_status_checks"]["checks"].append(
            {"context": check.CHECK_NAME, "app_id": 15368}
        )
        result = check.verify_active_protection(
            protection=protection,
            repository=repository,
        )
        self.assertFalse(result["ok"])
        self.assertIn(
            "branch protection does not enforce administrators",
            result["failures"],
        )
        self.assertIn(
            "repository auto-merge is still enabled",
            result["failures"],
        )


if __name__ == "__main__":
    unittest.main()

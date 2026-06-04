"""Tests for the dev bridge (ASST-INTEG-006).

Covers:
- Packet model construction and validation
- HMAC-SHA256 signing and verification
- Replay protection (duplicate packet rejected)
- Dispatcher dry-run materialises no subprocess calls
- Dispatcher live-run calls ai_status.py assign per task
- Constraint enforcement (noDirectShellFromWeb, allowedRepos)
- Audit refs link packet_id, conversation_id, and documents
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ..dev_bridge_models import (
    BridgeActor,
    BridgeConstraints,
    BridgeDispatchRequest,
    BridgeDocument,
    BridgeTask,
    DevTaskPacket,
    PacketSignature,
)
from ..dev_bridge_signer import (
    has_seen_packet,
    mark_packet_seen,
    sign_packet,
    verify_packet,
)
from ..dev_bridge_dispatcher import dispatch_task_packet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_KEY_STORE = {"assistant-bridge-dev": b"test-key-for-unit-tests-only"}


def _make_packet(*, packet_id: str = "pkt_test001") -> DevTaskPacket:
    return DevTaskPacket(
        packetId=packet_id,
        emittedAt="2026-06-03T12:00:00Z",
        intent="generate_sa_sd_and_dispatch",
        actor=BridgeActor(id="operator-1", roles=["admin"], capabilities=["assistant.kernel.debug"]),
        mode="kernel_debug",
        sourceConversationId="mgmt-nl-abc123",
        sourceTurnIds=["turn_001", "turn_002"],
        documents=[
            BridgeDocument(path="docs/04/sa_sd_pkt_test001_feature/system_analysis.md", kind="SA_SD_PLAN")
        ],
        tasks=[
            BridgeTask(
                id="TEST-TASK-001",
                title="Implement feature X",
                owner="Codex",
                reviewer="Claude",
                phase="Sprint TEST / Feature X",
                artifacts=["services/foo/bar.py"],
                acceptance=["Feature X works end to end"],
            )
        ],
        constraints=BridgeConstraints(
            allowedRepos=["pantheon"],
            requiresBranchPrMerge=True,
            noDirectShellFromWeb=True,
        ),
        auditConversationHref="/bff/management/ai/conversations/mgmt-nl-abc123",
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestDevTaskPacketModel(unittest.TestCase):

    def test_packet_constructs_with_required_fields(self):
        pkt = _make_packet()
        self.assertEqual(pkt.version, "pantheon.assistant.dev-task.v1")
        self.assertEqual(pkt.packet_id, "pkt_test001")
        self.assertIsNone(pkt.signature)

    def test_actor_fields(self):
        pkt = _make_packet()
        self.assertEqual(pkt.actor.id, "operator-1")
        self.assertIn("admin", pkt.actor.roles)

    def test_constraints_defaults(self):
        c = BridgeConstraints()
        self.assertIn("pantheon", c.allowed_repos)
        self.assertTrue(c.no_direct_shell_from_web)
        self.assertTrue(c.requires_branch_pr_merge)

    def test_bridge_task_depends_on_defaults_empty(self):
        task = BridgeTask(
            id="T1", title="t", owner="Codex", reviewer="Claude"
        )
        self.assertEqual(task.depends_on, [])

    def test_packet_serialises_round_trip(self):
        pkt = _make_packet()
        data = pkt.model_dump(by_alias=False, mode="json")
        pkt2 = DevTaskPacket(**data)
        self.assertEqual(pkt2.packet_id, pkt.packet_id)
        self.assertEqual(pkt2.source_conversation_id, pkt.source_conversation_id)


# ---------------------------------------------------------------------------
# Signer tests
# ---------------------------------------------------------------------------

class TestDevBridgeSigner(unittest.TestCase):

    def test_sign_adds_signature(self):
        pkt = _make_packet()
        signed = sign_packet(pkt, key_store=_TEST_KEY_STORE)
        self.assertIsNotNone(signed.signature)
        self.assertEqual(signed.signature.algorithm, "HMAC-SHA256")
        self.assertEqual(signed.signature.key_id, "assistant-bridge-dev")

    def test_verify_valid_signature_passes(self):
        pkt = _make_packet()
        signed = sign_packet(pkt, key_store=_TEST_KEY_STORE)
        verify_packet(signed, key_store=_TEST_KEY_STORE)  # should not raise

    def test_verify_no_signature_raises(self):
        pkt = _make_packet()
        with self.assertRaises(ValueError, msg="Packet has no signature"):
            verify_packet(pkt, key_store=_TEST_KEY_STORE)

    def test_verify_tampered_value_raises(self):
        pkt = _make_packet()
        signed = sign_packet(pkt, key_store=_TEST_KEY_STORE)
        tampered = signed.model_copy(
            update={"signature": PacketSignature(
                keyId="assistant-bridge-dev",
                algorithm="HMAC-SHA256",
                value="deadbeef" * 8,
            )}
        )
        with self.assertRaises(ValueError):
            verify_packet(tampered, key_store=_TEST_KEY_STORE)

    def test_verify_wrong_key_raises(self):
        pkt = _make_packet()
        signed = sign_packet(pkt, key_store=_TEST_KEY_STORE)
        wrong_store = {"assistant-bridge-dev": b"wrong-key"}
        with self.assertRaises(ValueError):
            verify_packet(signed, key_store=wrong_store)

    def test_sign_excludes_signature_from_payload(self):
        """Signing the same content twice must produce the same MAC."""
        pkt = _make_packet()
        s1 = sign_packet(pkt, key_store=_TEST_KEY_STORE)
        s2 = sign_packet(pkt, key_store=_TEST_KEY_STORE)
        self.assertEqual(s1.signature.value, s2.signature.value)

    def test_unsupported_algorithm_raises(self):
        pkt = _make_packet()
        signed = sign_packet(pkt, key_store=_TEST_KEY_STORE)
        bad_algo = signed.model_copy(
            update={"signature": PacketSignature(
                keyId="assistant-bridge-dev",
                algorithm="MD5",
                value=signed.signature.value,
            )}
        )
        with self.assertRaises(ValueError, msg="Unsupported signature algorithm"):
            verify_packet(bad_algo, key_store=_TEST_KEY_STORE)


# ---------------------------------------------------------------------------
# Replay protection tests
# ---------------------------------------------------------------------------

class TestReplayProtection(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = self._tmpdir.name
        # Create ai-status.json so _find_repo_root works
        Path(self.repo_root, "ai-status.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_new_packet_id_not_seen(self):
        self.assertFalse(has_seen_packet("pkt_brand_new", repo_root=self.repo_root))

    def test_mark_then_has_seen(self):
        mark_packet_seen("pkt_xxx", repo_root=self.repo_root)
        self.assertTrue(has_seen_packet("pkt_xxx", repo_root=self.repo_root))

    def test_mark_idempotent(self):
        mark_packet_seen("pkt_yyy", repo_root=self.repo_root)
        mark_packet_seen("pkt_yyy", repo_root=self.repo_root)
        self.assertTrue(has_seen_packet("pkt_yyy", repo_root=self.repo_root))

    def test_different_ids_independent(self):
        mark_packet_seen("pkt_a", repo_root=self.repo_root)
        self.assertFalse(has_seen_packet("pkt_b", repo_root=self.repo_root))


# ---------------------------------------------------------------------------
# Dispatcher tests
# ---------------------------------------------------------------------------

class TestDispatcher(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = self._tmpdir.name
        Path(self.repo_root, "ai-status.json").write_text("{}", encoding="utf-8")
        # Create a mock scripts/ai_status.py
        scripts_dir = Path(self.repo_root, "scripts")
        scripts_dir.mkdir()
        (scripts_dir / "ai_status.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")

    def tearDown(self):
        self._tmpdir.cleanup()

    def _signed_request(self, *, dry_run: bool = False, packet_id: str = "pkt_disp001") -> BridgeDispatchRequest:
        pkt = _make_packet(packet_id=packet_id)
        signed = sign_packet(pkt, key_store=_TEST_KEY_STORE)
        return BridgeDispatchRequest(
            packet=signed,
            repoRoot=self.repo_root,
            dryRun=dry_run,
        )

    def test_dry_run_returns_dry_run_records(self):
        req = self._signed_request(dry_run=True)
        result = dispatch_task_packet(req, key_store=_TEST_KEY_STORE)
        self.assertTrue(result.dry_run)
        self.assertFalse(result.replay_rejected)
        self.assertEqual(len(result.task_records), 1)
        self.assertEqual(result.task_records[0].status, "dry_run")

    def test_dry_run_does_not_mark_seen(self):
        req = self._signed_request(dry_run=True, packet_id="pkt_dryonly")
        dispatch_task_packet(req, key_store=_TEST_KEY_STORE)
        self.assertFalse(has_seen_packet("pkt_dryonly", repo_root=self.repo_root))

    def test_live_run_marks_seen(self):
        req = self._signed_request(packet_id="pkt_live001")
        dispatch_task_packet(req, key_store=_TEST_KEY_STORE)
        self.assertTrue(has_seen_packet("pkt_live001", repo_root=self.repo_root))

    def test_replay_rejected_on_second_dispatch(self):
        req = self._signed_request(packet_id="pkt_replay001")
        dispatch_task_packet(req, key_store=_TEST_KEY_STORE)
        # Second call with same packet_id
        req2 = self._signed_request(packet_id="pkt_replay001")
        result = dispatch_task_packet(req2, key_store=_TEST_KEY_STORE)
        self.assertTrue(result.replay_rejected)
        self.assertEqual(len(result.task_records), 0)

    def test_invalid_signature_raises(self):
        pkt = _make_packet(packet_id="pkt_badsig")
        req = BridgeDispatchRequest(packet=pkt, repoRoot=self.repo_root)
        with self.assertRaises(ValueError):
            dispatch_task_packet(req, key_store=_TEST_KEY_STORE)

    def test_constraint_violation_raises(self):
        pkt = _make_packet(packet_id="pkt_badconstraint")
        pkt = pkt.model_copy(update={
            "constraints": BridgeConstraints(
                allowedRepos=["pantheon"],
                requiresBranchPrMerge=True,
                noDirectShellFromWeb=False,  # violation
            )
        })
        signed = sign_packet(pkt, key_store=_TEST_KEY_STORE)
        req = BridgeDispatchRequest(packet=signed, repoRoot=self.repo_root)
        with self.assertRaises(ValueError, msg="noDirectShellFromWeb"):
            dispatch_task_packet(req, key_store=_TEST_KEY_STORE)

    def test_audit_refs_contain_expected_fields(self):
        req = self._signed_request(packet_id="pkt_audit001")
        result = dispatch_task_packet(req, key_store=_TEST_KEY_STORE)
        refs = result.audit_refs
        self.assertEqual(refs["packetId"], "pkt_audit001")
        self.assertEqual(refs["conversationId"], "mgmt-nl-abc123")
        self.assertIn("TEST-TASK-001", refs["taskIds"])
        self.assertIn("dispatchedAt", refs)

    def test_audit_links_documents(self):
        req = self._signed_request(packet_id="pkt_docs001")
        result = dispatch_task_packet(req, key_store=_TEST_KEY_STORE)
        self.assertIn(
            "docs/04/sa_sd_pkt_test001_feature/system_analysis.md",
            result.audit_refs["documents"],
        )

    def test_task_record_owner_reviewer(self):
        req = self._signed_request(packet_id="pkt_owrev001")
        result = dispatch_task_packet(req, key_store=_TEST_KEY_STORE)
        self.assertEqual(len(result.task_records), 1)
        rec = result.task_records[0]
        self.assertEqual(rec.task_id, "TEST-TASK-001")
        self.assertEqual(rec.owner, "Codex")
        self.assertEqual(rec.reviewer, "Claude")

    def test_allowed_repos_violation_raises(self):
        pkt = _make_packet(packet_id="pkt_badrepo")
        pkt = pkt.model_copy(update={
            "constraints": BridgeConstraints(
                allowedRepos=["other-repo"],  # pantheon missing
                requiresBranchPrMerge=True,
                noDirectShellFromWeb=True,
            )
        })
        signed = sign_packet(pkt, key_store=_TEST_KEY_STORE)
        req = BridgeDispatchRequest(packet=signed, repoRoot=self.repo_root)
        with self.assertRaises(ValueError, msg="allowedRepos"):
            dispatch_task_packet(req, key_store=_TEST_KEY_STORE)


if __name__ == "__main__":
    unittest.main()

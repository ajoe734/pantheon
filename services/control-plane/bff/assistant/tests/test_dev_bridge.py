"""Tests for the dev bridge (ASST-INTEG-006).

Covers:
- Packet model construction and validation
- Ed25519 signing and verification
- Replay protection (duplicate packet rejected)
- Dispatcher dry-run materialises no subprocess calls
- Dispatcher live-run calls one atomic ai_status.py materialization batch
- Constraint enforcement (noDirectShellFromWeb, allowedRepos)
- Audit refs link packet_id, conversation_id, and documents
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import base64
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
    packet_digest,
    sign_packet,
    verify_packet,
)
from .. import dev_bridge_dispatcher
from ..dev_bridge_dispatcher import dispatch_task_packet
from .dev_bridge_test_support import write_materializing_ai_status


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

    def test_production_signer_uses_configured_active_key_id(self):
        pkt = _make_packet()
        private_key = b"p" * 32
        public_key = Ed25519PrivateKey.from_private_bytes(private_key).public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        with patch.dict(
            os.environ,
            {
                "BRIDGE_SIGNING_KEY_ID": "bridge-prod-2026-08",
                "BRIDGE_SIGNING_PRIVATE_KEY": private_key.hex(),
                "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": json.dumps({
                    "bridge-prod-2026-08": base64.urlsafe_b64encode(public_key).decode().rstrip("=")
                }),
            },
            clear=True,
        ):
            signed = sign_packet(pkt)
        self.assertEqual(signed.signature.key_id, "bridge-prod-2026-08")

    def test_production_signer_rejects_private_public_mismatch(self):
        with patch.dict(
            os.environ,
            {
                "BRIDGE_SIGNING_KEY_ID": "bridge-prod-2026-08",
                "BRIDGE_SIGNING_PRIVATE_KEY": (b"p" * 32).hex(),
                "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": json.dumps({
                    "bridge-prod-2026-08": base64.urlsafe_b64encode(b"q" * 32).decode().rstrip("=")
                }),
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "does not match"):
                sign_packet(_make_packet())

    def test_missing_key_has_no_development_fallback_authority(self):
        pkt = _make_packet()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "BRIDGE_SIGNING_KEY_ID"):
                sign_packet(pkt)

    def test_sign_adds_signature(self):
        pkt = _make_packet()
        signed = sign_packet(pkt, key_store=_TEST_KEY_STORE)
        self.assertIsNotNone(signed.signature)
        self.assertEqual(signed.signature.algorithm, "Ed25519")
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
                algorithm="Ed25519",
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
        """Signing the same content twice must produce the same Ed25519 signature."""
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
        replay_rows = Path(
            self.repo_root, ".orchestrator", "dev-bridge-seen-packets.txt"
        ).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(replay_rows), 1)

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
        write_materializing_ai_status(Path(self.repo_root))

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

    def test_ai_status_subprocess_receives_no_signing_authority(self):
        req = self._signed_request(packet_id="pkt_env_boundary")
        secrets = {
            "BRIDGE_SIGNING_PRIVATE_KEY": "private",
            "BRIDGE_SIGNING_KEY": "legacy-symmetric",
            "BRIDGE_SIGNING_KEY_ID": "active-id",
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_KEY": "assertion-private",
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_PRIVATE_KEY": "assertion-ed25519-private",
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_KEY_ID": "assertion-active-id",
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_JSON": "{}",
        }
        dispatch_task_packet(
            req,
            key_store=_TEST_KEY_STORE,
            runtime_env=secrets,
        )
        calls = [
            json.loads(line)
            for line in Path(self.repo_root, "calls.jsonl").read_text().splitlines()
            if line.strip()
        ]
        self.assertTrue(calls)
        self.assertTrue(all(call["signing_authority_markers"] == {} for call in calls))

    def test_git_subprocess_receives_no_signing_authority(self):
        completed = __import__("subprocess").CompletedProcess(
            args=["git"], returncode=0, stdout="abc\n", stderr=""
        )
        with patch.dict(os.environ, {
            "BRIDGE_SIGNING_PRIVATE_KEY": "bridge-private",
            "BRIDGE_SIGNING_KEY_ID": "bridge-id",
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_PRIVATE_KEY": "operator-private",
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_KEY_ID": "operator-id",
        }, clear=False), patch.object(
            dev_bridge_dispatcher.subprocess, "run", return_value=completed
        ) as run:
            self.assertEqual(dev_bridge_dispatcher._git_stdout(Path(self.repo_root), "rev-parse", "HEAD"), "abc")
        child_env = run.call_args.kwargs["env"]
        self.assertNotIn("BRIDGE_SIGNING_PRIVATE_KEY", child_env)
        self.assertNotIn("BRIDGE_SIGNING_KEY_ID", child_env)
        self.assertNotIn("PANTHEON_CANONICAL_MUTATION_ASSERTION_PRIVATE_KEY", child_env)
        self.assertNotIn("PANTHEON_CANONICAL_MUTATION_ASSERTION_KEY_ID", child_env)

    def test_replay_rejected_on_second_dispatch(self):
        req = self._signed_request(packet_id="pkt_replay001")
        dispatch_task_packet(req, key_store=_TEST_KEY_STORE)
        # Second call with same packet_id
        req2 = self._signed_request(packet_id="pkt_replay001")
        result = dispatch_task_packet(req2, key_store=_TEST_KEY_STORE)
        self.assertTrue(result.replay_rejected)
        self.assertEqual(len(result.task_records), 1)
        self.assertEqual(result.task_records[0].status, "already_dispatched")
        self.assertEqual(result.audit_refs["packetDigest"], packet_digest(req.packet))

    def test_replay_after_live_reassignment_is_not_a_provenance_mismatch(self):
        """SUP-CANONICAL-PACKET-ATOMIC-MATERIALIZATION-20260811.

        A later Human/Ops or supervisor-governed owner/reviewer change is live
        routing state, not part of the packet's immutable declared scope. A
        replay of the same exact packet must still validate the frozen
        `dev_bridge.task_spec` provenance, but must not fail just because the
        live top-level owner/reviewer no longer equal the packet's originally
        signed values.
        """

        req = self._signed_request(packet_id="pkt_reassign001")
        dispatch_task_packet(req, key_store=_TEST_KEY_STORE)

        status_path = Path(self.repo_root, "ai-status.json")
        state = json.loads(status_path.read_text(encoding="utf-8"))
        task = next(t for t in state["tasks"] if t["id"] == "TEST-TASK-001")
        self.assertEqual(task["owner"], "Codex")
        self.assertEqual(task["reviewer"], "Claude")
        # Simulate a governed reassignment landing after materialization.
        task["owner"] = "Claude"
        task["reviewer"] = "Codex2"
        status_path.write_text(json.dumps(state), encoding="utf-8")

        req2 = self._signed_request(packet_id="pkt_reassign001")
        result = dispatch_task_packet(req2, key_store=_TEST_KEY_STORE)

        self.assertTrue(result.replay_rejected)
        self.assertEqual(result.admission_status, "admitted_replay")
        self.assertEqual(result.errors, [])
        # The reassignment must survive the replay untouched.
        reread = json.loads(status_path.read_text(encoding="utf-8"))
        replayed_task = next(t for t in reread["tasks"] if t["id"] == "TEST-TASK-001")
        self.assertEqual(replayed_task["owner"], "Claude")
        self.assertEqual(replayed_task["reviewer"], "Codex2")
        # The originally signed provenance stays frozen regardless of routing.
        self.assertEqual(replayed_task["dev_bridge"]["task_spec"]["owner"], "Codex")
        self.assertEqual(replayed_task["dev_bridge"]["task_spec"]["reviewer"], "Claude")

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

    def test_requires_branch_pr_merge_false_raises(self):
        pkt = _make_packet(packet_id="pkt_no_pr_merge")
        pkt = pkt.model_copy(update={
            "constraints": BridgeConstraints(
                allowedRepos=["pantheon"],
                requiresBranchPrMerge=False,
                noDirectShellFromWeb=True,
            )
        })
        signed = sign_packet(pkt, key_store=_TEST_KEY_STORE)
        req = BridgeDispatchRequest(packet=signed, repoRoot=self.repo_root)
        with self.assertRaisesRegex(ValueError, "requiresBranchPrMerge"):
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

    def test_unconfigured_allowed_repo_raises(self):
        pkt = _make_packet(packet_id="pkt_unconfigured_repo")
        pkt = pkt.model_copy(update={
            "constraints": BridgeConstraints(
                allowedRepos=["pantheon", "execute-plans"],
                requiresBranchPrMerge=True,
                noDirectShellFromWeb=True,
            )
        })
        signed = sign_packet(pkt, key_store=_TEST_KEY_STORE)
        req = BridgeDispatchRequest(packet=signed, repoRoot=self.repo_root)
        with patch.dict(
            "os.environ",
            {"PANTHEON_ASSISTANT_DEV_BRIDGE_ALLOWED_REPOS": "pantheon"},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "unconfigured repositories"):
                dispatch_task_packet(req, key_store=_TEST_KEY_STORE)

    def test_configured_allowed_repos_pass(self):
        pkt = _make_packet(packet_id="pkt_configured_repo")
        pkt = pkt.model_copy(update={
            "constraints": BridgeConstraints(
                allowedRepos=["pantheon", "execute-plans"],
                requiresBranchPrMerge=True,
                noDirectShellFromWeb=True,
            )
        })
        signed = sign_packet(pkt, key_store=_TEST_KEY_STORE)
        req = BridgeDispatchRequest(packet=signed, repoRoot=self.repo_root, dryRun=True)
        with patch.dict(
            "os.environ",
            {"PANTHEON_ASSISTANT_DEV_BRIDGE_ALLOWED_REPOS": "pantheon,execute-plans"},
            clear=False,
        ):
            result = dispatch_task_packet(req, key_store=_TEST_KEY_STORE)
        self.assertFalse(result.replay_rejected)
        self.assertEqual(result.task_records[0].status, "dry_run")


if __name__ == "__main__":
    unittest.main()

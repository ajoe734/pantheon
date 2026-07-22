#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from adapters.base import DeliveryRequest
from adapters.file_inbox import FileInboxAdapter
import adapters.file_inbox as file_inbox
import common


class FileInboxTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / ".orchestrator").mkdir()
        self.inbox = self.root / ".llm-inbox" / "codex.md"
        self.config = {
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "state_file": str(self.root / ".orchestrator" / "state.json"),
            },
            "agents": {
                "codex": {
                    "display_name": "Codex",
                    "provider": "codex",
                    "file_inbox_path": ".llm-inbox/codex.md",
                }
            },
            "providers": {"codex": {"file_inbox": {"open_in_vscode": False}}},
        }
        self.request = DeliveryRequest(
            agent_id="Codex",
            provider="codex",
            delivery_mode="file_inbox",
            message="resume the owned task",
            context_files=["AI_COLLABORATION_GUIDE.md"],
        )
        self.adapter = FileInboxAdapter(config=self.config, provider_capabilities={})

    def test_delivery_is_durable_readback_verified_and_holds_runtime_sidecar(self) -> None:
        real_write = common.durable_write_bytes
        lock_path = self.root / ".orchestrator" / "runtime-admission.lock"

        def assert_locked_write(path: Path, payload: bytes) -> None:
            probe = os.open(lock_path, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(probe)
            real_write(path, payload)

        with mock.patch.object(file_inbox, "durable_write_bytes", side_effect=assert_locked_write):
            result = self.adapter.deliver(self.request)

        self.assertTrue(result.ok)
        self.assertEqual(result.payload_path, str(self.inbox))
        body = self.inbox.read_text(encoding="utf-8")
        self.assertIn("resume the owned task", body)
        self.assertIn("AI_COLLABORATION_GUIDE.md", body)
        self.assertEqual(list(self.inbox.parent.glob(f".{self.inbox.name}.*.tmp")), [])

    def test_delivery_rejects_symlink_data_leaf_without_touching_target(self) -> None:
        target = self.root / "outside.md"
        target.write_text("operator-owned\n", encoding="utf-8")
        self.inbox.parent.mkdir(parents=True)
        self.inbox.symlink_to(target)

        with self.assertRaisesRegex(RuntimeError, "data leaf cannot be a symlink"):
            self.adapter.deliver(self.request)

        self.assertTrue(self.inbox.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "operator-owned\n")

    def test_delivery_fails_when_post_replace_readback_does_not_match(self) -> None:
        with mock.patch.object(file_inbox, "_read_regular_file", return_value=b"corrupt"):
            with self.assertRaisesRegex(RuntimeError, "readback mismatch"):
                self.adapter.deliver(self.request)


if __name__ == "__main__":
    unittest.main()

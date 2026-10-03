"""worker_runner must resolve the supervisor runtime state exactly like the supervisor.

On 2026-09-08 a runtime promotion moved ``.orchestrator/state.json`` under
``worker-runtime/`` and fenced the retired path with a FIFO.  worker_runner
still read the retired path, so every worker died at launch ("worker launch
receipt must be a stable regular file") and the supervisor re-dispatched it in
a loop.  A fence (directory or FIFO) must never be selected.
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

_P = os.path.join(os.path.dirname(__file__), "worker_runner.py")
_spec = importlib.util.spec_from_file_location("worker_runner", _P)
wr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wr)


class RuntimeReceiptFenceTests(unittest.TestCase):
    def test_launch_receipt_is_read_from_modern_state_despite_fifo_fence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="worker-runner-receipt-") as temp_dir:
            root = Path(temp_dir)
            orch = root / ".orchestrator"
            modern = orch / "worker-runtime"
            modern.mkdir(parents=True)
            worker = {"run_id": "run-1", "task_id": "T-1", "status": "starting"}
            (modern / "state.json").write_text(
                json.dumps({"workers": {"run-1": worker}}), encoding="utf-8"
            )
            os.mkfifo(str(orch / "state.json"), 0o600)

            self.assertEqual(wr._runtime_worker_receipt(root, "run-1"), worker)
            self.assertIsNone(wr._runtime_worker_receipt(root, "run-2"))


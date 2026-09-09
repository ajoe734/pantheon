"""DTG-CLEAN-M2 characterization tests for dev_bridge_materialize.py.

Not a re-test of scripts/test_ai_status.py's and test_dev_bridge_reliability.py's
extensive coverage (which already exercises this exact code through
ai_status.py's re-export and continues to pass unchanged); this proves the
module is genuinely usable on its own and that the shared re-entrancy guard
is a real singleton reachable from a completely independent ai_status.py
copy -- the exact scenario that surfaced a real bug during this extraction
(a lazy ``import ai_status`` resolved to a *different* module instance than
the isolated copy calling in, silently breaking the bridge-provenance guard).
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / ".orchestrator"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from development_bridge import dev_bridge_materialize


class DevBridgeMaterializeModuleTests(unittest.TestCase):
    def test_privileged_readback_requires_frozen_full_spec_policy(self) -> None:
        from test_execution_authorization import ExecutionAuthorizationTestCase
        import execution_authorization

        fixture = ExecutionAuthorizationTestCase()
        fixture.setUp()
        task = deepcopy(fixture._granted_task())
        task["execution_authorization"] = execution_authorization.pending_authorization_hold(fixture.policy)
        bridge = deepcopy(task["dev_bridge"])
        row = {
            "task_id": task["id"], "owner": task["owner"], "reviewer": task["reviewer"],
            "title": task["title"], "task_metadata": {"dev_bridge": bridge},
        }
        ai_status = dev_bridge_materialize._ai_status_module()
        with mock.patch.object(ai_status, "_bridge_assignment_from_metadata", return_value=bridge):
            receipt = dev_bridge_materialize.read_dev_bridge_materialized_batch({"tasks": [task]}, {"tasks": [row]})
            self.assertEqual(receipt[0]["taskSpecHash"], fixture.policy["task_spec_hash"])
            for policy in (None, {}, {**fixture.policy, "task_spec_hash": "0" * 64}, {**fixture.policy, "requires_execution_authorization": False}):
                with self.subTest(policy=policy):
                    task["execution_authorization"]["policy"] = policy
                    with self.assertRaisesRegex(SystemExit, "execution-policy mismatch"):
                        dev_bridge_materialize.read_dev_bridge_materialized_batch({"tasks": [task]}, {"tasks": [row]})

    def test_module_imports_with_no_circular_dependency(self) -> None:
        # No importlib.reload() here: reloading this module would rebind its
        # module-level _DEV_BRIDGE_MATERIALIZATION_LOCAL to a brand new
        # threading.local(), orphaning the reference ai_status.py (and any
        # already-loaded isolated copy of it) imported earlier -- silently
        # reintroducing the exact split-singleton bug this extraction fixed,
        # for every test that runs afterward in the same process.
        ai_status = dev_bridge_materialize._ai_status_module()
        self.assertTrue(hasattr(ai_status, "get_task"))

    def test_entry_points_are_exported(self) -> None:
        for name in (
            "load_dev_bridge_materialize_batch",
            "dev_bridge_replay_ledger",
            "verify_signed_dev_bridge_packet",
            "validate_dev_bridge_batch_dependency_closure",
            "dev_bridge_materialize_mutation_environment",
            "run_dev_bridge_materialize_batch",
            "read_dev_bridge_materialized_batch",
        ):
            self.assertTrue(callable(getattr(dev_bridge_materialize, name)), name)

    def test_materialization_guard_is_shared_across_independent_ai_status_copies(
        self,
    ) -> None:
        """Regression test for the exact bug this extraction introduced and
        fixed: an isolated ai_status.py copy (loaded the same way
        test_dev_bridge_reliability.py loads one) must observe the
        materialization guard this module sets, because both sides now
        import the *same* threading.local() instance from this module
        rather than each reaching for a bare, possibly-different
        'import ai_status'."""

        spec = importlib.util.spec_from_file_location(
            "dev_bridge_materialize_test_isolated_ai_status",
            REPO_ROOT / "scripts" / "ai_status.py",
        )
        assert spec is not None and spec.loader is not None
        isolated = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = isolated
        try:
            spec.loader.exec_module(isolated)
            self.assertIs(
                isolated._DEV_BRIDGE_MATERIALIZATION_LOCAL,
                dev_bridge_materialize._DEV_BRIDGE_MATERIALIZATION_LOCAL,
            )
            row = {
                "task_metadata": {"dev_bridge": {"packet_id": "pkt_x"}},
                "title": "X",
                "assignment_next": None,
            }
            with dev_bridge_materialize.dev_bridge_materialize_mutation_environment(
                row, "assistant.dev.source"
            ):
                self.assertTrue(
                    getattr(
                        isolated._DEV_BRIDGE_MATERIALIZATION_LOCAL, "active", False
                    )
                )
            self.assertFalse(
                getattr(isolated._DEV_BRIDGE_MATERIALIZATION_LOCAL, "active", False)
            )
        finally:
            sys.modules.pop(spec.name, None)


if __name__ == "__main__":
    unittest.main()

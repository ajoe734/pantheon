"""Regression tests for Agora interaction worker CLI launcher and container healthcheck."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = REPO_ROOT / "scripts" / "run_agora_interaction_worker.py"
PERSONA_CLIENT_PATH = (
    REPO_ROOT
    / "services"
    / "control-plane"
    / "bff"
    / "agora"
    / "interaction"
    / "persona_client.py"
)


class AgoraInteractionWorkerLauncherTests(unittest.TestCase):
    def test_healthcheck_subprocess_with_clean_pythonpath_succeeds(self) -> None:
        """Verify the container healthcheck command succeeds without ModuleNotFoundError."""
        clean_env = os.environ.copy()
        clean_env.pop("PYTHONPATH", None)

        proc = subprocess.run(
            [sys.executable, str(LAUNCHER_PATH), "--healthcheck"],
            cwd=str(REPO_ROOT),
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(
            proc.returncode,
            0,
            f"Healthcheck exited with code {proc.returncode}.\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}",
        )
        self.assertIn("Healthcheck OK", proc.stdout + proc.stderr)
        self.assertNotIn("ModuleNotFoundError", proc.stderr)
        self.assertNotIn("No module named services", proc.stderr)

    def test_healthcheck_from_foreign_working_directory_succeeds(self) -> None:
        """Verify the launcher resolves repo imports even when executed from a foreign directory."""
        clean_env = os.environ.copy()
        clean_env.pop("PYTHONPATH", None)

        proc = subprocess.run(
            [sys.executable, str(LAUNCHER_PATH), "--healthcheck"],
            cwd="/tmp",
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(
            proc.returncode,
            0,
            f"Foreign cwd healthcheck exited with code {proc.returncode}.\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}",
        )
        self.assertIn("Healthcheck OK", proc.stdout + proc.stderr)
        self.assertNotIn("ModuleNotFoundError", proc.stderr)

    def test_help_argument_subprocess_succeeds(self) -> None:
        """Verify the launcher argument parser outputs help without errors."""
        clean_env = os.environ.copy()
        clean_env.pop("PYTHONPATH", None)

        proc = subprocess.run(
            [sys.executable, str(LAUNCHER_PATH), "--help"],
            cwd=str(REPO_ROOT),
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(proc.returncode, 0)
        self.assertIn("Agora Persona interaction background worker", proc.stdout)
        self.assertIn("--healthcheck", proc.stdout)

    def test_persona_discovery_uses_typed_canonical_client(self) -> None:
        """The nonexistent `store.FastBffReadStore` import and its catch-all
        empty fallback must be gone; the launcher must depend on the typed
        canonical Persona client instead."""
        source = LAUNCHER_PATH.read_text()
        self.assertNotIn("FastBffReadStore", source)
        self.assertNotIn("MinimalReadStore", source)
        self.assertIn("build_canonical_persona_client", source)

    def test_persona_client_module_has_no_empty_fallback(self) -> None:
        """`persona_client.py` must construct the canonical Persona client
        directly and must not catch construction errors to substitute an
        empty implementation."""
        self.assertTrue(PERSONA_CLIENT_PATH.exists())
        source = PERSONA_CLIENT_PATH.read_text()
        self.assertNotIn("from store import", source)
        self.assertNotIn("except Exception", source)

    def test_persona_client_construction_failure_is_not_swallowed(self) -> None:
        """A required-dependency construction failure must propagate to the
        caller, proving there is no empty-fallback branch left to catch it."""
        for path in (
            str(REPO_ROOT),
            str(REPO_ROOT / "services" / "control-plane" / "bff"),
        ):
            if path not in sys.path:
                sys.path.insert(0, path)

        from agora.interaction import persona_client

        original_factory = persona_client.create_read_surface_ports

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated required Persona dependency construction failure")

        persona_client.create_read_surface_ports = _boom
        try:
            with self.assertRaises(RuntimeError):
                persona_client.build_canonical_persona_client()
        finally:
            persona_client.create_read_surface_ports = original_factory

    def test_healthcheck_subprocess_fails_when_persona_client_cannot_construct(self) -> None:
        """A container healthcheck must fail, not report false health, when
        the required Persona discovery client cannot be constructed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sitecustomize_path = Path(tmpdir) / "sitecustomize.py"
            sitecustomize_path.write_text(
                "import builtins\n"
                "orig_import = builtins.__import__\n"
                "def custom_import(name, globals=None, locals=None, fromlist=(), level=0):\n"
                "    mod = orig_import(name, globals, locals, fromlist, level)\n"
                "    if 'agora.interaction.persona_client' in (name, getattr(mod, '__name__', '')):\n"
                "        if hasattr(mod, 'build_canonical_persona_client'):\n"
                "            def _boom():\n"
                "                raise RuntimeError('simulated Persona client construction failure in healthcheck subprocess')\n"
                "            mod.build_canonical_persona_client = _boom\n"
                "    return mod\n"
                "builtins.__import__ = custom_import\n"
            )
            clean_env = os.environ.copy()
            clean_env["PYTHONPATH"] = tmpdir

            proc = subprocess.run(
                [sys.executable, str(LAUNCHER_PATH), "--healthcheck"],
                cwd=str(REPO_ROOT),
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=15,
            )

        self.assertNotEqual(
            proc.returncode,
            0,
            f"Healthcheck reported success despite an unconstructable Persona client.\n"
            f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}",
        )
        self.assertNotIn("Healthcheck OK", proc.stdout + proc.stderr)

    def test_production_adapter_registry_wires_authentic_adapters(self) -> None:
        """The production adapter registry must wire AuthenticStageAdapter with real mode
        for all allowlisted stages, not synthetic DefaultAllowlistedAdapter.
        """
        for path in (
            str(REPO_ROOT),
            str(REPO_ROOT / "services" / "control-plane" / "bff"),
        ):
            if path not in sys.path:
                sys.path.insert(0, path)

        from agora.research.dispatcher import (
            ALLOWLISTED_STAGE_BACKENDS,
            AuthenticStageAdapter,
            build_authentic_adapter_registry,
        )

        registry = build_authentic_adapter_registry()
        for stage_type, backend in ALLOWLISTED_STAGE_BACKENDS.items():
            adapter = registry.get(stage_type)
            self.assertIsNotNone(adapter, f"Missing adapter for {stage_type}")
            self.assertIsInstance(
                adapter,
                AuthenticStageAdapter,
                f"Adapter for {stage_type} is not an AuthenticStageAdapter",
            )
            self.assertEqual(adapter.preferred_backend, backend)
            self.assertEqual(adapter.mode, "real")

    def test_research_store_construction_failure_is_not_swallowed(self) -> None:
        """A required research store failure must propagate and fail startup, not be caught/swallowed."""
        clean_env = os.environ.copy()
        clean_env["AGORA_WORKSHOP_STORE_BACKEND"] = "memory"
        clean_env["AGORA_GOVERNANCE_STORE_BACKEND"] = "memory"
        clean_env["AGORA_RESEARCH_STORE_BACKEND"] = "invalid_unknown_backend"

        proc = subprocess.run(
            [sys.executable, str(LAUNCHER_PATH), "--once"],
            cwd=str(REPO_ROOT),
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Unsupported AGORA_RESEARCH_STORE_BACKEND", proc.stderr + proc.stdout)

    def test_healthcheck_subprocess_fails_when_research_client_cannot_construct(self) -> None:
        """A container healthcheck must fail if required research backend clients cannot be constructed."""
        clean_env = os.environ.copy()
        clean_env.pop("PYTHONPATH", None)
        clean_env["AGORA_RESEARCH_ADAPTER_MODE"] = "real"
        clean_env["AGORA_RESEARCH_FAIL_BACKEND_CLIENT"] = "1"

        proc = subprocess.run(
            [sys.executable, str(LAUNCHER_PATH), "--healthcheck"],
            cwd=str(REPO_ROOT),
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Healthcheck failed", proc.stdout + proc.stderr)

    def test_e2e_bff_enqueue_separate_worker_restart_persistence(self) -> None:
        """Prove BFF enqueue -> separate worker -> fresh read/restart parity across store reconstruction."""
        for path in (
            str(REPO_ROOT),
            str(REPO_ROOT / "services" / "control-plane" / "bff"),
        ):
            if path not in sys.path:
                sys.path.insert(0, path)

        from types import SimpleNamespace
        from agora.interaction.worker import AgoraInteractionWorker
        from agora.research.dispatcher import (
            AuthenticStageAdapter,
            ResearchDispatcher,
            build_authentic_adapter_registry,
            build_canonical_research_backend_clients,
        )
        from agora.research.receipt import resolve_run_provenance
        from agora.research.store import MemoryResearchPlanStore

        with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
            storage_path = tmp.name

            # 1. BFF creates plan, run, and enqueues to outbox in durable store
            bff_store = MemoryResearchPlanStore(storage_path=storage_path)
            plan_id = "plan-e2e-restart"
            run_id = "run-e2e-restart"
            trace_id = "trace-e2e-restart"
            tenant_id = "pantheon-dev"
            user_id = "agora-user-a"
            stage_item = {
                "stage_id": "stage-proto-1",
                "stage_type": "prototype_backtest",
                "routing": {"backend_mode": "real", "preferred_backend": "vectorbt"},
            }
            plan = {
                "plan_id": plan_id,
                "strategy_id": "strat-e2e",
                "lock_version": 2,
                "stages": [stage_item],
                "correlation_id": trace_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
            }
            bff_store.create_plan(plan)
            bff_store.create_run({
                "run_id": run_id,
                "plan_id": plan_id,
                "stage_id": "stage-proto-1",
                "stage_type": "prototype_backtest",
                "execution_status": "queued",
                "outcome": "inconclusive",
                "correlation_id": trace_id,
                "provenance": "unavailable",
                "tenant_id": tenant_id,
                "user_id": user_id,
            })
            bff_store.create_outbox_record({
                "outbox_id": f"rob:{plan_id}:stage-proto-1:{run_id}",
                "run_id": run_id,
                "plan_id": plan_id,
                "stage_id": "stage-proto-1",
                "stage_type": "prototype_backtest",
                "stage": stage_item,
                "plan": plan,
                "backend": "vectorbt",
                "status": "queued",
                "tenant_id": tenant_id,
                "user_id": user_id,
                "payload": {"plan": plan, "stage": stage_item},
                "downstream_idempotency_key": f"idemp:{run_id}",
            })
            del bff_store

            # 2. Separate worker opens the store from disk with authentic adapter and drains outbox
            worker_store = MemoryResearchPlanStore(storage_path=storage_path)
            recorded_requests = []

            def recording_transport(req):
                import json
                body = json.loads(req.data.decode("utf-8")) if req.data else {}
                recorded_requests.append({
                    "url": req.full_url,
                    "method": req.get_method(),
                    "headers": dict(req.headers),
                    "body": body,
                })
                return {
                    "status": "succeeded",
                    "outcome": "succeeded",
                    "backend_reference": f"vectorbt://runs/{body.get('run_id')}",
                    "artifact_digest": "sha256:authentic_vectorbt_artifact_digest_12345",
                    "metrics": [
                        {"name": "sharpe", "value": 2.5, "category": "performance", "provenance": "real"}
                    ],
                }

            backend_clients = build_canonical_research_backend_clients(
                mode="real",
                default_base_url="http://vectorbt:8000",
                default_transport=recording_transport,
            )
            adapter_registry = build_authentic_adapter_registry(
                mode="real",
                execution_owners=backend_clients,
            )
            dispatcher = ResearchDispatcher(
                store=worker_store,
                adapter_registry=adapter_registry,
            )
            worker = AgoraInteractionWorker(
                research_store=worker_store,
                research_dispatcher=dispatcher,
                worker_id="separate-worker-1",
            )
            drained = worker.drain_research_outbox()
            self.assertGreaterEqual(drained, 1)
            self.assertEqual(len(recorded_requests), 1)
            self.assertIn("vectorbt", recorded_requests[0]["url"])
            self.assertEqual(recorded_requests[0]["body"]["run_id"], run_id)
            del worker
            del dispatcher
            del worker_store

            # 3. Fresh read / restart: reconstruct store from disk and verify persistence and provenance
            reconstructed_store = MemoryResearchPlanStore(storage_path=storage_path)
            run = reconstructed_store.get_run(run_id)
            self.assertIsNotNone(run)
            self.assertEqual(run["execution_status"], "succeeded")
            self.assertEqual(run["outcome"], "pass")
            self.assertEqual(run["provenance"], "real")
            self.assertEqual(run["executor"], "vectorbt_executor")

            receipt = reconstructed_store.get_execution_receipt(run_id)
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt["mode"], "real")
            self.assertEqual(receipt["run_id"], run_id)
            self.assertEqual(receipt["artifact_digest"], "sha256:authentic_vectorbt_artifact_digest_12345")
            self.assertTrue(receipt["backend_reference"].startswith("vectorbt://runs/"))

            prov, resolved_receipt = resolve_run_provenance(
                reconstructed_store,
                run,
                expected_correlation_id=trace_id,
                expected_owner="vectorbt_executor",
            )
            self.assertEqual(prov, "real")
            self.assertIsNotNone(resolved_receipt)

    def test_worker_outbox_fails_closed_on_absent_backend(self) -> None:
        """Worker outbox draining must fail closed when authentic research backend is absent."""
        for path in (
            str(REPO_ROOT),
            str(REPO_ROOT / "services" / "control-plane" / "bff"),
        ):
            if path not in sys.path:
                sys.path.insert(0, path)

        from agora.interaction.worker import AgoraInteractionWorker
        from agora.research.dispatcher import (
            ResearchDispatcher,
            build_authentic_adapter_registry,
            build_canonical_research_backend_clients,
        )
        from agora.research.store import MemoryResearchPlanStore

        store = MemoryResearchPlanStore()
        plan_id = "plan-absent-test"
        run_id = "run-absent-test"
        stage_item = {
            "stage_id": "stage-proto-absent",
            "stage_type": "prototype_backtest",
            "routing": {"backend_mode": "real", "preferred_backend": "vectorbt"},
        }
        plan = {
            "plan_id": plan_id,
            "strategy_id": "strat-absent",
            "lock_version": 1,
            "stages": [stage_item],
            "correlation_id": "corr-absent",
            "tenant_id": "t1",
            "user_id": "u1",
        }
        store.create_plan(plan)
        store.create_run({
            "run_id": run_id,
            "plan_id": plan_id,
            "stage_id": "stage-proto-absent",
            "stage_type": "prototype_backtest",
            "execution_status": "queued",
            "outcome": "inconclusive",
            "correlation_id": "corr-absent",
            "provenance": "unavailable",
            "tenant_id": "t1",
            "user_id": "u1",
        })
        store.create_outbox_record({
            "outbox_id": f"rob:{plan_id}:stage-proto-absent:{run_id}",
            "run_id": run_id,
            "plan_id": plan_id,
            "stage_id": "stage-proto-absent",
            "stage_type": "prototype_backtest",
            "stage": stage_item,
            "plan": plan,
            "backend": "vectorbt",
            "status": "queued",
            "tenant_id": "t1",
            "user_id": "u1",
            "payload": {"plan": plan, "stage": stage_item},
            "downstream_idempotency_key": f"idemp:{run_id}",
        })

        # Client built in clean environment without base_url or backend_fn
        backend_clients = build_canonical_research_backend_clients(mode="real")
        adapter_registry = build_authentic_adapter_registry(
            mode="real",
            execution_owners=backend_clients,
        )
        dispatcher = ResearchDispatcher(
            store=store,
            adapter_registry=adapter_registry,
        )
        worker = AgoraInteractionWorker(
            research_store=store,
            research_dispatcher=dispatcher,
            worker_id="absent-backend-worker",
        )
        worker.drain_research_outbox()

        # Run must NOT succeed, and no real receipt must exist
        run = store.get_run(run_id)
        self.assertNotEqual(run.get("execution_status"), "succeeded")
        receipt = store.get_execution_receipt(run_id)
        self.assertIsNone(receipt)


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from services.execution.lean_runtime.bootstrap_contract import (
    PANTHEON_LEAN_REMOTE,
    PANTHEON_LEAN_SOURCE_PATH,
)
from services.execution.lean_runtime.runtime_context import (
    RuntimeContextError,
    RuntimeContextSource,
)
from services.execution.lean_runtime.runtime_bootstrap import (
    _SidecarHandler,
    _load_runtime_context,
    _load_sidecar_state,
)


def _manifest(**overrides):
    payload = {
        "request_id": "request-paper-ctx",
        "trace_id": "trace-paper-ctx",
        "runtime_binding_id": "rtb-paper-ctx",
        "runtime_id": "rt-paper-ctx",
        "deployment_plan_id": "dp-paper-ctx",
        "deployment_stage": "paper",
        "runtime_role": "paper",
        "artifact": {
            "artifact_id": "artifact-paper-ctx",
            "artifact_version": "1.0.0",
            "artifact_checksum": "sha256:paperctx",
            "strategy_id": "strategy-paper-ctx",
        },
        "capital": {
            "capital_pool_id": "pool-paper-ctx",
            "persona_capital_binding_id": "pcb-paper-ctx",
        },
        "bridge": {
            "repo": PANTHEON_LEAN_REMOTE,
            "path": PANTHEON_LEAN_SOURCE_PATH,
            "commit": "abc1234",
            "runtime_adapter_version": "0.1.0",
        },
    }
    payload.update(overrides)
    return payload


def _env(**overrides):
    payload = {
        "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
        "PANTHEON_RUNTIME_MODE": "paper",
        "PANTHEON_RUNTIME_BINDING_ID": "rtb-paper-env",
        "PANTHEON_RUNTIME_ID": "rt-paper-env",
        "PANTHEON_DEPLOYMENT_PLAN_ID": "dp-paper-env",
        "PANTHEON_DEPLOYMENT_STAGE": "paper",
        "PANTHEON_ARTIFACT_ID": "artifact-paper-env",
        "PANTHEON_ARTIFACT_VERSION": "1.0.0",
        "PANTHEON_ARTIFACT_CHECKSUM": "sha256:paperenv",
        "PANTHEON_STRATEGY_ID": "strategy-paper-env",
        "PANTHEON_CAPITAL_POOL_ID": "pool-paper-env",
        "PANTHEON_PERSONA_CAPITAL_BINDING_ID": "pcb-paper-env",
        "PANTHEON_ENGINE_BRIDGE_REMOTE": PANTHEON_LEAN_REMOTE,
        "PANTHEON_ENGINE_BRIDGE_SOURCE_PATH": PANTHEON_LEAN_SOURCE_PATH,
        "PANTHEON_ENGINE_BRIDGE_COMMIT": "abc1234",
        "PANTHEON_TRACE_ID": "trace-paper-env",
        "PANTHEON_REQUEST_ID": "request-paper-env",
    }
    payload.update(overrides)
    return payload


class RuntimeBootstrapContextTests(unittest.TestCase):
    def test_paper_context_loads_from_launch_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "launch-manifest.json"
            manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
                    "PANTHEON_RUNTIME_MODE": "paper",
                },
                clear=True,
            ):
                context = _load_runtime_context(
                    launch_manifest=str(manifest_path),
                    role="pantheon-paper-execution-runtime",
                )

        self.assertIsNotNone(context)
        self.assertEqual(context.context_source, RuntimeContextSource.LAUNCH_MANIFEST)
        self.assertEqual(context.runtime_binding_id, "rtb-paper-ctx")
        self.assertEqual(context.deployment_stage, "paper")

    def test_paper_context_loads_from_env_fallback(self):
        with patch.dict(os.environ, _env(), clear=True):
            context = _load_runtime_context(
                launch_manifest=None,
                role="pantheon-paper-execution-runtime",
            )

        self.assertIsNotNone(context)
        self.assertEqual(context.context_source, RuntimeContextSource.ENV_VARS)
        self.assertEqual(context.runtime_binding_id, "rtb-paper-env")
        self.assertEqual(context.artifact.artifact_id, "artifact-paper-env")

    def test_staging_paper_runtime_missing_context_fails_closed(self):
        with patch.dict(
            os.environ,
            {
                "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
                "PANTHEON_RUNTIME_MODE": "staging",
                "PANTHEON_DEPLOYMENT_STAGE": "staging",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeContextError, "runtime context is required"):
                _load_runtime_context(
                    launch_manifest=None,
                    role="pantheon-paper-execution-runtime",
                )

    def test_live_sidecar_does_not_require_runtime_context(self):
        with patch.dict(
            os.environ,
            {
                "PANTHEON_RUNTIME_ROLE": "live",
                "PANTHEON_RUNTIME_MODE": "live",
            },
            clear=True,
        ):
            context = _load_runtime_context(launch_manifest=None, role="live")

        self.assertIsNone(context)


class RuntimeBootstrapLiveGuardTests(unittest.TestCase):
    def test_live_sidecar_health_reports_not_activated(self):
        env = {
            "PANTHEON_RUNTIME_ROLE": "live",
            "PANTHEON_RUNTIME_MODE": "live",
            "PANTHEON_LIVE_RUNTIME_ID": "live-runtime-001",
            "PANTHEON_REQUIRED_SECRET_KEYS": "BROKER_API_KEY",
            "BROKER_API_KEY": "",
        }

        with patch.dict(os.environ, env, clear=False):
            payload = _load_sidecar_state("live")

        self.assertEqual(payload["runtime_role"], "live")
        self.assertTrue(payload["health_only"])
        self.assertEqual(payload["activation_status"], "not_activated")
        self.assertFalse(payload["live_broker_enabled"])
        self.assertFalse(payload["broker_connect_allowed"])
        self.assertFalse(payload["order_placement_allowed"])
        self.assertFalse(payload["bracket_order_submission_allowed"])
        self.assertEqual(payload["required_secret_status"][0]["env"], "BROKER_API_KEY")
        self.assertFalse(payload["required_secret_status"][0]["configured"])

    def test_live_sidecar_blocks_broker_connect_and_order_posts(self):
        env = {
            "PANTHEON_RUNTIME_ROLE": "live",
            "PANTHEON_RUNTIME_MODE": "live",
            "PANTHEON_LIVE_RUNTIME_ID": "live-runtime-001",
            "PANTHEON_LIVE_BROKER_ENABLED": "true",
        }

        with patch.dict(os.environ, env, clear=False):
            server = ThreadingHTTPServer(("127.0.0.1", 0), _SidecarHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(f"{base_url}/healthz", timeout=2) as response:
                    health_payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(health_payload["activation_status"], "not_activated")
                self.assertTrue(health_payload["health_only"])
                self.assertFalse(health_payload["live_broker_enabled"])
                self.assertTrue(health_payload["requested_live_broker_enabled"])

                broker_payload = self._post_blocked(base_url, "/api/broker/connect")
                order_payload = self._post_blocked(base_url, "/api/orders")

                self.assertEqual(broker_payload["action"], "broker_connect")
                self.assertEqual(order_payload["action"], "order_placement")
                for payload in (broker_payload, order_payload):
                    self.assertEqual(payload["status"], "blocked")
                    self.assertEqual(payload["activation_status"], "not_activated")
                    self.assertFalse(payload["broker_connect_allowed"])
                    self.assertFalse(payload["order_placement_allowed"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def _post_blocked(self, base_url: str, path: str) -> dict:
        request = urllib.request.Request(
            f"{base_url}{path}",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(raised.exception.code, 403)
        return json.loads(raised.exception.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()


class RuntimeBootstrapOrderGatePolicyTests(unittest.TestCase):
    """The production bootstrap hands the order gate a reader for the pool policy."""

    POOL = {
        "pool_id": "pool-paper-env",
        "tenant_id": "default",
        "name": "persona paper pool",
        "owner_id": "default",
        "owner_type": "org",
        "status": "active",
        "currency": "TWD",
        "budget": 100000.0,
        "single_runtime_enforced": True,
        "metadata": {
            "internal": True,
            "execution_context": "paper",
            "risk_policy": {
                "risk_policy_id": "rp-pool-paper-env",
                "version": "1.0",
                "status": "active",
                "allowed_stages": ["paper"],
                "gross_limit": 2.0,
                "net_limit": 2.0,
                "max_leverage": 2.0,
                "allowed_asset_classes": ["equity", "future"],
            },
        },
    }

    def _bootstrap_reader(self, env):
        from services.execution.lean_runtime import expression_inputs, runtime_bootstrap

        urls: list[str] = []

        def fetch(url, headers, timeout):
            urls.append(url)
            self.assertEqual(headers["Authorization"], "Bearer capital-reader")
            return self.POOL

        captured: dict = {}

        def fake_main(**kwargs):
            captured.update(kwargs)

        with patch.dict(os.environ, env, clear=True), patch.object(
            runtime_bootstrap, "main", fake_main
        ), patch.object(expression_inputs, "_http_get_json", fetch):
            runtime_bootstrap.run([])
        return captured, urls

    def _algo(self, reader, events):
        from services.execution.lean_runtime.paper_runtime import PaperExecutionAlgorithm

        return PaperExecutionAlgorithm(
            initial_cash=100_000.0,
            event_sink=events.append,
            capital_pool_id="pool-paper-env",
            capital_pool_reader=reader,
            in_process_broker=True,
        )

    @staticmethod
    def _submit_future(algo, signal_id, price, quantity):
        algo.SetCurrentSignalContext(
            {
                "instrument_id": "ssf:QFF:202611",
                "kind": "stock_future",
                "contract": "QFF",
                "contract_month": "202611",
                "multiplier": 100.0,
                "price_basis": "futures_daily_settlement_prev_session",
                "futures_settlement_price": price,
                "price_date": "2026-11-05",
                "trade_date": "2026-11-06",
                "decision_id": f"dec-{signal_id}",
            }
        )
        algo.SubmitTaiwanBrokerOrder(
            "2330.TW",
            signal_id=signal_id,
            side="buy",
            quantity=quantity,
            quantity_type="CONTRACTS",
            action="BUY",
        )

    def test_bootstrap_passes_the_capital_reader_and_the_gate_enforces_the_pool_policy(self):
        env = _env(
            PANTHEON_CAPITAL_API_URL="http://capital:8092",
            DEPLOYMENT_CAPITAL_SERVICE_TOKEN="capital-reader",
            PANTHEON_TENANT_ID="default",
        )
        captured, urls = self._bootstrap_reader(env)
        reader = captured["capital_pool_reader"]
        self.assertTrue(callable(reader))
        self.assertEqual(captured["runtime_context"].capital.capital_pool_id, "pool-paper-env")

        events: list = []
        algo = self._algo(reader, events)
        # 2.247x of equity: above max_leverage 2.0 and gross_limit 2.0.
        self._submit_future(algo, "s-over", 2247.0, 1.0)
        # 0.5x of equity: inside both limits.
        self._submit_future(algo, "s-inside", 500.0, 1.0)

        self.assertTrue(urls and all(u.endswith("/api/capital-pools/pool-paper-env") for u in urls))
        rejected = [e for e in events if e.event_type == "order_rejection"]
        self.assertEqual(len(rejected), 1)
        meta = rejected[0].metadata
        self.assertIn(meta["reason"], {"gross_limit", "leverage_limit"})
        self.assertEqual(meta["limit"], 2.0)
        self.assertGreater(meta["required"], 2.0)
        self.assertIn("available", meta)
        self.assertEqual(meta["risk_status"], "rejected")
        self.assertEqual(algo.performance_ledger()["fill_count"], 1)
        self.assertEqual(list(algo.instrument_positions), [next(iter(algo.instrument_positions))])

    def test_gross_limit_binds_when_leverage_is_not_set(self):
        pool = json.loads(json.dumps(self.POOL))
        del pool["metadata"]["risk_policy"]["max_leverage"]
        events: list = []
        algo = self._algo(lambda _pool_id: pool, events)
        self._submit_future(algo, "s-gross", 2247.0, 1.0)
        [rejection] = [e for e in events if e.event_type == "order_rejection"]
        self.assertEqual(rejection.metadata["reason"], "gross_limit")
        self.assertEqual(rejection.metadata["limit"], 2.0)

    def test_equity_only_pool_rejects_a_future_order(self):
        pool = json.loads(json.dumps(self.POOL))
        pool["metadata"]["risk_policy"]["allowed_asset_classes"] = ["equity"]
        events: list = []
        algo = self._algo(lambda _pool_id: pool, events)
        self._submit_future(algo, "s-asset", 500.0, 1.0)
        [rejection] = [e for e in events if e.event_type == "order_rejection"]
        self.assertEqual(rejection.metadata["reason"], "asset_class_not_allowed")

    def test_unconfigured_capital_service_keeps_the_previous_resolution_path(self):
        captured, urls = self._bootstrap_reader(_env())
        self.assertIsNone(captured["capital_pool_reader"])
        self.assertEqual(urls, [])

    def test_bootstrap_wires_dataset_final_settlement_source(self):
        from services.execution.lean_runtime.futures_session_inputs import DatasetFinalSettlementSource
        from services.execution.lean_runtime.instrument_candidates import lookup_final_settlement_price

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "market_data_store"
            part = root / "normalized" / "taifex_ssf_contracts" / "date=2026-11-18"
            part.mkdir(parents=True, exist_ok=True)
            row = {
                "dataset": "taifex_ssf_contracts",
                "market_date": "2026-11-18",
                "contract": "SRF",
                "underlying_code": "0050",
                "contract_month": "202611",
                "final_settlement_price": 95.25,
                "final_settlement_date": "2026-11-18",
                "available_time": "2026-11-18T06:00:00Z",
                "ingest_time": "2026-11-18T06:00:01Z",
            }
            (part / "run-1.jsonl").write_text(
                json.dumps({"metadata": {"normalized_row": row}}) + "\n",
                encoding="utf-8",
            )

            env = _env(PANTHEON_TAIFEX_SSF_NORMALIZED_ROOT=str(root))
            captured, _ = self._bootstrap_reader(env)
            source = captured["final_settlement_source"]
            self.assertIsInstance(source, DatasetFinalSettlementSource)
            resolved = lookup_final_settlement_price("SRF", "202611", source, as_of_trading_day="2026-11-18")
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved["final_settlement_price"], 95.25)



"""Tests for tw_signal_producer strategy-driven signal generation."""
import unittest
from unittest.mock import patch, MagicMock
import json
import sys
from pathlib import Path

# Add repo root and scripts directory to python path
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Import the script
import scripts.tw_signal_producer as tw_signal_producer


class TestTWSignalProducer(unittest.TestCase):
    @patch("subprocess.run")
    def test_tw_signal_producer_evaluates_and_pushes_successfully(self, mock_run) -> None:
        # 1. Mock runtime bindings query
        mock_bindings = [
            {
                "binding_id": "rb-tw-test",
                "runtime_id": "runtime-tw-equity-paper",
                "status": "active",
                "metadata": {
                    "strategy_id": "tw_session_momentum",
                    "strategy_artifact": {
                        "artifact_schema_version": "1.0",
                        "artifact_id": "artifact-tw-session-momentum-v1",
                        "strategy_id": "tw_session_momentum",
                        "version": "1.0.0",
                        "algorithm_ref": {
                            "engine": "lean",
                            "repository": "ajoe734/pantheon-lean",
                            "commit": "5ad0249432459c119f26718007e083808ef7995d",
                            "path": "Algorithm.Python/pantheon_algo/base.py",
                            "entrypoint": "pantheon_algo.base:PantheonAlgoBase",
                            "signal_interface": "services.execution.lean_runtime.paper_signal_producer:Strategy",
                            "signal_schema_version": "1.0",
                            "logic_interpreter": "services.registry.strategy_artifact:evaluate_strategy_action"
                        },
                        "strategy_logic": {
                            "kind": "close_to_close_momentum",
                            "lookback_parameter": "lookback_bars",
                            "threshold_parameter": "momentum_threshold",
                            "positive_action": "BUY",
                            "non_positive_action": "SELL"
                        },
                        "parameters": {
                            "symbols": ["2330.TW"],
                            "bar_frequency": "1d",
                            "data_source": "source-ingest:normalized/taiwanstockprice/finmind-daily",
                            "lookback_bars": 2,
                            "momentum_threshold": 0.0,
                            "order_quantity": 1,
                            "quantity_type": "SHARES",
                            "zero_momentum_action": "SELL"
                        },
                        "mutation_surface": {
                            "controls": [
                                {
                                    "parameter_key": "lookback_bars",
                                    "value_type": "integer",
                                    "current_value": 2,
                                    "allowed_range": {
                                        "min": 2,
                                        "max": 60
                                    },
                                    "step": 1
                                },
                                {
                                    "parameter_key": "momentum_threshold",
                                    "value_type": "number",
                                    "current_value": 0.0,
                                    "allowed_range": {
                                        "min": 0.0,
                                        "max": 0.05
                                    },
                                    "step": 0.001
                                }
                            ],
                            "immutable_parameters": [
                                "symbols",
                                "bar_frequency",
                                "data_source",
                                "order_quantity",
                                "quantity_type",
                                "zero_momentum_action"
                            ]
                        },
                        "lineage": {
                            "source_run_ids": ["EVOLOOP-003"]
                        }
                    }
                }
            }
        ]

        # 2. Mock source closes query response
        # 2330 close 100.0 on 2026-07-09, 101.0 on 2026-07-13
        mock_closes_lines = [
            json.dumps({"metadata": {"body": json.dumps({"stock_id": "2330", "close": 100.0, "date": "2026-07-09"})}}),
            json.dumps({"metadata": {"body": json.dumps({"stock_id": "2330", "close": 101.0, "date": "2026-07-13"})}}),
        ]
        mock_closes_stdout = "\n".join(mock_closes_lines)

        # Configure mock_run return values
        r1 = MagicMock()
        r1.returncode = 0
        r1.stdout = json.dumps(mock_bindings)

        r2 = MagicMock()
        r2.returncode = 0
        r2.stdout = mock_closes_stdout

        r3 = MagicMock()
        r3.returncode = 0

        mock_run.side_effect = [r1, r2, r3]

        # Run main function
        result = tw_signal_producer.main()

        # Assertions
        self.assertEqual(result, 0)
        self.assertEqual(mock_run.call_count, 3)

        # Check call arguments
        calls = mock_run.call_args_list
        # Call 1: Fetch runtime bindings
        self.assertEqual(calls[0][0][0], ["docker", "exec", "pantheon-runtime-manager-1", "cat", "/data/runtime/runtime_bindings.json"])
        # Call 2: Fetch closes from source ingest
        self.assertIn("docker", calls[1][0][0])
        self.assertIn("pantheon-source-ingest-1", calls[1][0][0])
        # Call 3: RPUSH to redis
        rpush_cmd = calls[2][0][0]
        self.assertEqual(rpush_cmd[0:5], ["docker", "exec", "pantheon-signal-store-1", "redis-cli", "RPUSH"])
        self.assertEqual(rpush_cmd[5], "pantheon:signals:pending:rb-tw-test")
        
        # Verify signal fields
        pushed_sig = json.loads(rpush_cmd[6])
        self.assertEqual(pushed_sig["strategy_id"], "tw_session_momentum")
        self.assertEqual(pushed_sig["symbol"], "2330.TW")
        self.assertEqual(pushed_sig["action"], "BUY")
        self.assertEqual(pushed_sig["direction"], "LONG")
        self.assertEqual(pushed_sig["quantity"], 1.0)
        self.assertEqual(pushed_sig["quantity_type"], "SHARES")
        self.assertEqual(pushed_sig["binding_id"], "rb-tw-test")

    @patch("subprocess.run")
    def test_tw_signal_producer_no_active_binding_fails(self, mock_run) -> None:
        # Mock bindings query returning no active binding
        r1 = MagicMock()
        r1.returncode = 0
        r1.stdout = json.dumps([])
        mock_run.side_effect = [r1]

        result = tw_signal_producer.main()
        self.assertEqual(result, 1)
        self.assertEqual(mock_run.call_count, 1)

    @patch("subprocess.run")
    def test_tw_signal_producer_missing_artifact_fails(self, mock_run) -> None:
        # Mock bindings query returning active binding but missing strategy_artifact
        mock_bindings = [
            {
                "binding_id": "rb-tw-test",
                "runtime_id": "runtime-tw-equity-paper",
                "status": "active",
                "metadata": {
                    "strategy_id": "tw_session_momentum"
                    # missing strategy_artifact
                }
            }
        ]
        r1 = MagicMock()
        r1.returncode = 0
        r1.stdout = json.dumps(mock_bindings)
        mock_run.side_effect = [r1]

        result = tw_signal_producer.main()
        self.assertEqual(result, 1)
        self.assertEqual(mock_run.call_count, 1)

    @patch("subprocess.run")
    def test_tw_signal_producer_insufficient_closes_skips(self, mock_run) -> None:
        # Mock bindings query (1 active binding)
        mock_bindings = [
            {
                "binding_id": "rb-tw-test",
                "runtime_id": "runtime-tw-equity-paper",
                "status": "active",
                "metadata": {
                    "strategy_id": "tw_session_momentum",
                    "strategy_artifact": {
                        "artifact_schema_version": "1.0",
                        "artifact_id": "artifact-tw-session-momentum-v1",
                        "strategy_id": "tw_session_momentum",
                        "version": "1.0.0",
                        "algorithm_ref": {
                            "engine": "lean",
                            "repository": "ajoe734/pantheon-lean",
                            "commit": "5ad0249432459c119f26718007e083808ef7995d",
                            "path": "Algorithm.Python/pantheon_algo/base.py",
                            "entrypoint": "pantheon_algo.base:PantheonAlgoBase",
                            "signal_interface": "services.execution.lean_runtime.paper_signal_producer:Strategy",
                            "signal_schema_version": "1.0",
                            "logic_interpreter": "services.registry.strategy_artifact:evaluate_strategy_action"
                        },
                        "strategy_logic": {
                            "kind": "close_to_close_momentum",
                            "lookback_parameter": "lookback_bars",
                            "threshold_parameter": "momentum_threshold",
                            "positive_action": "BUY",
                            "non_positive_action": "SELL"
                        },
                        "parameters": {
                            "symbols": ["2330.TW"],
                            "bar_frequency": "1d",
                            "data_source": "source-ingest:normalized/taiwanstockprice/finmind-daily",
                            "lookback_bars": 2,
                            "momentum_threshold": 0.0,
                            "order_quantity": 1,
                            "quantity_type": "SHARES",
                            "zero_momentum_action": "SELL"
                        },
                        "mutation_surface": {
                            "controls": [],
                            "immutable_parameters": []
                        },
                        "lineage": {"source_run_ids": []}
                    }
                }
            }
        ]

        # Mock closes query returning insufficient closes (only 1 day, but lookback is 2)
        mock_closes_lines = [
            json.dumps({"metadata": {"body": json.dumps({"stock_id": "2330", "close": 100.0, "date": "2026-07-09"})}})
        ]
        mock_closes_stdout = "\n".join(mock_closes_lines)

        r1 = MagicMock()
        r1.returncode = 0
        r1.stdout = json.dumps(mock_bindings)

        r2 = MagicMock()
        r2.returncode = 0
        r2.stdout = mock_closes_stdout

        mock_run.side_effect = [r1, r2]

        result = tw_signal_producer.main()
        
        # Should return 0 (completed check), but no redis rpush should have happened
        self.assertEqual(result, 0)
        self.assertEqual(mock_run.call_count, 2)


if __name__ == "__main__":
    unittest.main()

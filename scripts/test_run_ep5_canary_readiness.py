#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import run_ep5_canary_readiness as readiness


def base_env() -> dict[str, str]:
    return {
        "EXECUTION_BROKER_PROVIDER": "IBKR",
        "TW_EXECUTION_PROVIDER": "Shioaji",
        "CRYPTO_EXECUTION_PROVIDER": "Kraken",
        "TW_RESEARCH_PROVIDER": "TEJ",
        "BROKER_API_KEY_SECRET_NAME": "pantheon-dev-broker-api-key",
        "BROKER_API_SECRET_SECRET_NAME": "pantheon-dev-broker-api-secret",
        "SHIOAJI_API_KEY_SECRET_NAME": "pantheon-dev-shioaji-api-key",
        "SHIOAJI_SECRET_KEY_SECRET_NAME": "pantheon-dev-shioaji-secret-key",
        "KRAKEN_API_KEY_SECRET_NAME": "pantheon-dev-kraken-api-key",
        "KRAKEN_API_SECRET_SECRET_NAME": "pantheon-dev-kraken-api-secret",
        "TEJ_API_KEY_SECRET_NAME": "pantheon-dev-tej-api-key",
        "CANARY_BROKER_ACCOUNT_REF": "broker-subaccount-paper-us-equities",
        "SHIOAJI_ACCOUNT_NAME": "paper-tw-equities",
        "TEJ_DATASET_CODE": "TWN/APRCD1",
    }


class RunEp5CanaryReadinessTest(unittest.TestCase):
    def test_evaluate_provider_matrix_requires_all_secret_refs(self) -> None:
        env = base_env()
        del env["KRAKEN_API_SECRET_SECRET_NAME"]

        items = readiness.evaluate_provider_matrix(env)
        by_name = {item.name: item for item in items}

        self.assertTrue(by_name["governed_provider_matrix_declared"].passed)
        self.assertFalse(by_name["governed_provider_secret_refs_present"].passed)
        self.assertIn("KRAKEN_API_SECRET_SECRET_NAME", by_name["governed_provider_secret_refs_present"].detail)

    def test_build_datasource_smoke_payloads_preserves_provider_boundaries(self) -> None:
        payload = readiness.build_datasource_smoke_payloads(base_env())

        self.assertEqual(payload["providers"]["ibkr"]["expected_provider"], "IBKR")
        self.assertEqual(payload["providers"]["shioaji"]["expected_provider"], "Shioaji")
        self.assertEqual(payload["providers"]["kraken"]["expected_provider"], "Kraken")
        self.assertEqual(payload["providers"]["tej"]["expected_provider"], "TEJ")
        self.assertEqual(payload["providers"]["ibkr"]["order_payload"]["broker"], "IBKR")
        self.assertEqual(payload["providers"]["shioaji"]["quote_subscription"]["exchange"], "TSE")
        self.assertEqual(payload["providers"]["kraken"]["order_payload"]["provider"], "Kraken")
        self.assertEqual(
            payload["providers"]["tej"]["normalized_dataset"]["governance_metadata"]["source_key"],
            "tej",
        )
        self.assertEqual(
            payload["providers"]["tej"]["normalized_dataset"]["governance_metadata"]["source_class"],
            "research_grade",
        )

    def test_run_datasource_smoke_writes_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / "canary.env"
            output_dir = Path(tmpdir) / "out"
            env_file.write_text(
                "\n".join(f"{key}={value}" for key, value in base_env().items()) + "\n",
                encoding="utf-8",
            )

            args = type("Args", (), {"env_file": str(env_file), "output_dir": str(output_dir)})
            exit_code = readiness.command_run_datasource_smoke(args)

            self.assertEqual(exit_code, 0)
            summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            payload = json.loads((output_dir / "datasource-smoke.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "pass")
            self.assertEqual(sorted(summary["providers"]), ["ibkr", "kraken", "shioaji", "tej"])
            self.assertEqual(payload["task_id"], "APP-003-DATASOURCE-OPS-001")
            self.assertEqual(payload["providers"]["tej"]["dataset_code"], "TWN/APRCD1")


if __name__ == "__main__":
    unittest.main()

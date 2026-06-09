#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_marketdata_credential_smoke as smoke


class _FakeHeaders(dict):
    def get(self, key: str, default: object | None = None) -> object | None:
        for header_key, value in self.items():
            if header_key.lower() == key.lower():
                return value
        return default


class _FakeResponse:
    status = 200

    def __init__(self) -> None:
        self.headers = _FakeHeaders(
            {
                "content-type": "application/json",
                "x-ratelimit-limit": "100",
                "x-ratelimit-remaining": "99",
            }
        )

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self, _size: int) -> bytes:
        return b'{"results":[{"symbol":"AAPL"}]}'


class MarketDataCredentialSmokeTest(unittest.TestCase):
    def assert_evidence_fields(self, packet: dict[str, object]) -> None:
        self.assertIn("rate_limit", packet)
        self.assertIn("session_provenance", packet)
        self.assertIn("status", packet["rate_limit"])
        self.assertIn("quota", packet["rate_limit"])
        self.assertIn("status", packet["session_provenance"])
        self.assertFalse(packet["session_provenance"]["raw_secret_material_present_in_artifact"])

    def test_missing_required_credential_records_unavailable_evidence(self) -> None:
        packet = smoke.run_provider("massive_polygon", {}, allow_network=False)

        self.assertEqual(packet["status"], "credential_unavailable")
        self.assertEqual(packet["provider"], "Massive / Polygon")
        self.assertFalse(packet["order_side_effects_allowed"])
        self.assertFalse(packet["capital_side_effects_allowed"])
        self.assertFalse(packet["credential"]["raw_secret_material_present_in_artifact"])
        self.assert_evidence_fields(packet)
        self.assertEqual(packet["rate_limit"]["status"], "unavailable")
        self.assertEqual(packet["session_provenance"]["status"], "unavailable")

    def test_polygon_request_redacts_raw_api_key(self) -> None:
        packet = smoke.run_provider(
            "massive_polygon",
            {
                "POLYGON_API_KEY": "raw-polygon-key-123",
                "US_MARKET_DATA_SECRET_REF": "secret://pantheon/polygon",
            },
            allow_network=False,
        )

        encoded = json.dumps(packet)
        self.assertNotIn("raw-polygon-key-123", encoded)
        self.assertIn("<redacted>", packet["request"]["url"])
        self.assertTrue(packet["credential"]["credential_present"])
        self.assertEqual(packet["credential"]["secret_ref"], "secret://pantheon/polygon")
        self.assert_evidence_fields(packet)

    def test_public_reference_without_network_is_explicit_read_unavailable(self) -> None:
        packet = smoke.run_provider("twse", {}, allow_network=False)

        self.assertEqual(packet["status"], "read_unavailable")
        self.assertTrue(packet["credential"]["credential_not_required"])
        self.assertEqual(packet["source_class"], "official_reference")
        self.assert_evidence_fields(packet)
        self.assertEqual(packet["rate_limit"]["status"], "unavailable")
        self.assertEqual(packet["session_provenance"]["session_type"], "stateless_http_get")

    def test_http_read_records_rate_limit_and_session_provenance(self) -> None:
        with mock.patch.object(smoke, "urlopen", return_value=_FakeResponse()):
            packet = smoke.run_provider("twse", {}, allow_network=True)

        self.assertEqual(packet["status"], "read_ok")
        self.assert_evidence_fields(packet)
        self.assertEqual(packet["rate_limit"]["status"], "observed")
        self.assertEqual(packet["rate_limit"]["headers"]["x-ratelimit-remaining"], "99")
        self.assertEqual(packet["session_provenance"]["status"], "observed")
        self.assertEqual(packet["session_provenance"]["session_type"], "stateless_http_get")

    def test_mops_uses_default_official_post_probe_without_smoke_url(self) -> None:
        with mock.patch.object(smoke, "urlopen", return_value=_FakeResponse()) as opened:
            packet = smoke.run_provider("mops", {}, allow_network=True)

        request = opened.call_args.args[0]
        self.assertEqual(packet["status"], "read_ok")
        self.assertEqual(packet["request"]["method"], "POST")
        self.assertEqual(packet["request"]["url"], "https://mops.twse.com.tw/mops/api/home_page/t05sr01_1")
        self.assertEqual(packet["request"]["body_keys"], ["count", "marketKind"])
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(packet["session_provenance"]["session_type"], "stateless_http_post")

    def test_tej_default_probe_targets_trial_database_and_redacts_key(self) -> None:
        packet = smoke.run_provider("tej", {"TEJ_API_KEY": "raw-tej-key-123"}, allow_network=False)

        self.assertEqual(packet["status"], "read_unavailable")
        self.assertIn("/api/datatables/TRAIL/TAPRCD.json?", packet["request"]["url"])
        self.assertIn("opts.columns=coid%2Cmdate%2Cclose_d", packet["request"]["url"])
        self.assertNotIn("raw-tej-key-123", json.dumps(packet))
        self.assertIn("<redacted>", packet["request"]["url"])

    def test_finmind_default_probe_targets_data_endpoint_and_redacts_token(self) -> None:
        packet = smoke.run_provider("finmind", {"FINMIND_API_TOKEN": "raw-finmind-token-123"}, allow_network=False)

        self.assertEqual(packet["status"], "read_unavailable")
        self.assertIn("/api/v4/data?", packet["request"]["url"])
        self.assertIn("dataset=TaiwanStockPrice", packet["request"]["url"])
        self.assertIn("data_id=2330", packet["request"]["url"])
        self.assertNotIn("raw-finmind-token-123", json.dumps(packet))
        self.assertIn("<redacted>", packet["request"]["url"])

    def test_finmind_missing_required_credential_records_unavailable_evidence(self) -> None:
        packet = smoke.run_provider("finmind", {}, allow_network=False)

        self.assertEqual(packet["status"], "credential_unavailable")
        self.assertEqual(packet["provider"], "FinMind")
        self.assert_evidence_fields(packet)

    def test_order_capable_providers_disable_order_path(self) -> None:
        for provider in ("ibkr", "shioaji", "kraken"):
            with self.subTest(provider=provider):
                packet = smoke.run_provider(provider, {}, allow_network=False)
                self.assertTrue(packet["order_capable_provider"])
                self.assertEqual(packet["order_path"], "disabled_for_marketdata_smoke")
                self.assertFalse(packet["order_side_effects_allowed"])
                self.assert_evidence_fields(packet)

    def test_quote_readback_files_record_provenance_without_order_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cases = {
                "ibkr": ("IBKR_QUOTE_READBACK_JSON", {"captured_at": "2026-05-01T12:00:00Z", "ticks": [{"symbol": "AAPL", "last": 183.42}]}),
                "shioaji": (
                    "SHIOAJI_QUOTE_READBACK_JSON",
                    {"captured_at": "2026-05-01T12:01:00Z", "ticks": [{"code": "2330", "close": 780.0}]},
                ),
            }
            for provider, (env_key, payload) in cases.items():
                with self.subTest(provider=provider):
                    readback = Path(tmpdir) / f"{provider}-readback.json"
                    readback.write_text(json.dumps(payload), encoding="utf-8")
                    packet = smoke.run_provider(provider, {env_key: str(readback)}, allow_network=False)

                    self.assertEqual(packet["status"], "read_ok")
                    self.assertEqual(packet["readback"]["source"], env_key)
                    self.assertNotIn("order_payload", packet)
                    self.assert_evidence_fields(packet)
                    self.assertEqual(packet["rate_limit"]["status"], "unavailable")
                    self.assertEqual(packet["session_provenance"]["session_type"], "quote_readback_file")
                    self.assertEqual(packet["session_provenance"]["details"]["file_name"], f"{provider}-readback.json")
                    self.assertEqual(packet["session_provenance"]["details"]["captured_at"], payload["captured_at"])
                    self.assertEqual(len(packet["session_provenance"]["details"]["file_sha256"]), 64)

    def test_run_smoke_writes_provider_packets_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            summary = smoke.run_smoke({}, output_dir, allow_network=False, providers=["twse", "ibkr"])

            self.assertEqual(summary["status"], "pass")
            self.assertTrue((output_dir / "twse.json").exists())
            self.assertTrue((output_dir / "ibkr.json").exists())
            self.assertTrue((output_dir / "summary.json").exists())
            self.assertFalse(summary["raw_secret_material_present_in_artifacts"])
            for provider in ("twse", "ibkr"):
                packet = json.loads((output_dir / f"{provider}.json").read_text(encoding="utf-8"))
                self.assert_evidence_fields(packet)


if __name__ == "__main__":
    unittest.main()

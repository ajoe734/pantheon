"""Unit tests for data-plane schema models.

Tests cover SecurityMaster, ContractMaster, MarketCalendarSession,
and all dataset lineage objects (RawDataset, NormalizedDataset,
FeatureDataset, DatasetVersion).
"""

import json
import sys
import unittest
from pathlib import Path
from jsonschema import Draft7Validator, FormatChecker

# Handle hyphenated directory name
_DATA_PLANE = Path(__file__).resolve().parent.parent
if str(_DATA_PLANE.parent) not in sys.path:
    sys.path.insert(0, str(_DATA_PLANE.parent))

# Import models using importlib due to hyphen in directory name
import importlib.util


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_models = _DATA_PLANE / "models"
security_master_mod = _load_module("security_master", _models / "security_master.py")
contract_master_mod = _load_module("contract_master", _models / "contract_master.py")
market_calendar_mod = _load_module("market_calendar_session", _models / "market_calendar_session.py")
dataset_lineage_mod = _load_module("dataset_lineage", _models / "dataset_lineage.py")

SecurityMaster = security_master_mod.SecurityMaster
ContractMaster = contract_master_mod.ContractMaster
MarketCalendarSession = market_calendar_mod.MarketCalendarSession
RawDataset = dataset_lineage_mod.RawDataset
NormalizedDataset = dataset_lineage_mod.NormalizedDataset
FeatureDataset = dataset_lineage_mod.FeatureDataset
DatasetVersion = dataset_lineage_mod.DatasetVersion

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEMAS_DIR = _DATA_PLANE / "schemas"
FORMAT_CHECKER = FormatChecker()


def _load_schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def _validate_schema(name: str, payload: dict) -> list[str]:
    validator = Draft7Validator(_load_schema(name), format_checker=FORMAT_CHECKER)
    return sorted(error.message for error in validator.iter_errors(payload))


class TestSecurityMaster(unittest.TestCase):
    """SecurityMaster model and schema validation."""

    def test_valid_security(self):
        sec = SecurityMaster(
            security_id="SEC-US0378331005",
            market="US",
            venue="NASDAQ",
            symbol_native="AAPL",
            symbol_canonical="US0378331005",
            asset_type="equity",
            currency="USD",
        )
        valid, errors = SecurityMaster.validate(sec)
        self.assertTrue(valid, errors)

    def test_missing_required_fields(self):
        sec = SecurityMaster(
            security_id="",
            market="",
            venue="",
            symbol_native="",
            symbol_canonical="",
            asset_type="",
            currency="",
        )
        valid, errors = SecurityMaster.validate(sec)
        self.assertFalse(valid)
        self.assertGreater(len(errors), 0)

    def test_to_dict_roundtrip(self):
        sec = SecurityMaster(
            security_id="SEC-TW2330",
            market="TW",
            venue="TWSE",
            symbol_native="2330",
            symbol_canonical="TW2330TWSE",
            asset_type="equity",
            currency="TWD",
        )
        data = sec.to_dict()
        sec2 = SecurityMaster.from_dict(data)
        self.assertEqual(sec.security_id, sec2.security_id)
        self.assertEqual(sec.market, sec2.market)

    def test_schema_file_exists(self):
        schema_path = SCHEMAS_DIR / "security_master.schema.json"
        self.assertTrue(schema_path.exists())

    def test_json_serialization(self):
        sec = SecurityMaster(
            security_id="SEC-CRYPTO-BTC",
            market="CRYPTO",
            venue="BINANCE",
            symbol_native="BTCUSDT",
            symbol_canonical="BTC",
            asset_type="crypto",
            currency="USDT",
        )
        json_str = sec.to_json()
        data = json.loads(json_str)
        self.assertEqual(data["security_id"], "SEC-CRYPTO-BTC")

    def test_invalid_asset_type_rejected_by_model_and_schema(self):
        sec = SecurityMaster(
            security_id="SEC-US0378331005",
            market="US",
            venue="NASDAQ",
            symbol_native="AAPL",
            symbol_canonical="US0378331005",
            asset_type="banana",
            currency="USD",
        )
        valid, errors = SecurityMaster.validate(sec)
        self.assertFalse(valid)
        self.assertTrue(any("asset_type" in error for error in errors))
        self.assertTrue(_validate_schema("security_master", sec.to_dict()))

    def test_from_dict_applies_timestamp_defaults(self):
        sec = SecurityMaster.from_dict(
            {
                "security_id": "SEC-US0378331005",
                "market": "US",
                "venue": "NASDAQ",
                "symbol_native": "AAPL",
                "symbol_canonical": "US0378331005",
                "asset_type": "equity",
                "currency": "USD",
            }
        )
        self.assertIsNotNone(sec.created_at)
        self.assertIsNotNone(sec.updated_at)
        self.assertEqual(_validate_schema("security_master", sec.to_dict()), [])


class TestContractMaster(unittest.TestCase):
    """ContractMaster model and schema validation."""

    def test_valid_future(self):
        con = ContractMaster(
            contract_id="CON-US-ES-202606",
            underlying_id="SEC-US-SPX",
            market="US",
            venue="CME",
            contract_type="future",
            expiry="2026-06-19",
            multiplier=50.0,
            tick_size=0.25,
        )
        valid, errors = ContractMaster.validate(con)
        self.assertTrue(valid, errors)

    def test_valid_option(self):
        con = ContractMaster(
            contract_id="CON-US-AAPL-202606-C150",
            underlying_id="SEC-US0378331005",
            market="US",
            venue="OPRA",
            contract_type="option",
            expiry="2026-06-19",
            multiplier=100.0,
            tick_size=0.01,
            strike=150.0,
            option_right="call",
        )
        valid, errors = ContractMaster.validate(con)
        self.assertTrue(valid, errors)

    def test_option_missing_strike(self):
        con = ContractMaster(
            contract_id="CON-US-AAPL-202606-C150",
            underlying_id="SEC-US0378331005",
            market="US",
            venue="OPRA",
            contract_type="option",
            expiry="2026-06-19",
            multiplier=100.0,
            tick_size=0.01,
        )
        valid, errors = ContractMaster.validate(con)
        self.assertFalse(valid)
        self.assertTrue(any("strike" in e for e in errors))

    def test_invalid_multiplier(self):
        con = ContractMaster(
            contract_id="CON-US-ES-202606",
            underlying_id="SEC-US-SPX",
            market="US",
            venue="CME",
            contract_type="future",
            expiry="2026-06-19",
            multiplier=-1.0,
            tick_size=0.25,
        )
        valid, errors = ContractMaster.validate(con)
        self.assertFalse(valid)

    def test_schema_file_exists(self):
        schema_path = SCHEMAS_DIR / "contract_master.schema.json"
        self.assertTrue(schema_path.exists())

    def test_invalid_enums_rejected_by_model_and_schema(self):
        con = ContractMaster(
            contract_id="CON-US-AAPL-202606-C150",
            underlying_id="SEC-US0378331005",
            market="US",
            venue="OPRA",
            contract_type="option",
            expiry="2026-06-19",
            multiplier=100.0,
            tick_size=0.01,
            strike=150.0,
            option_right="banana",
        )
        valid, errors = ContractMaster.validate(con)
        self.assertFalse(valid)
        self.assertTrue(any("option_right" in error for error in errors))
        self.assertTrue(_validate_schema("contract_master", con.to_dict()))


class TestMarketCalendarSession(unittest.TestCase):
    """MarketCalendarSession model and schema validation."""

    def test_valid_session(self):
        cal = MarketCalendarSession(
            market="US",
            trade_date="2026-04-13",
            session_open="09:30:00",
            session_close="16:00:00",
            timezone="America/New_York",
        )
        valid, errors = MarketCalendarSession.validate(cal)
        self.assertTrue(valid, errors)

    def test_holiday_session(self):
        cal = MarketCalendarSession(
            market="TW",
            trade_date="2026-02-28",
            session_open="",
            session_close="",
            timezone="Asia/Taipei",
            holiday_flag=True,
        )
        valid, errors = MarketCalendarSession.validate(cal)
        self.assertTrue(valid, errors)

    def test_missing_required_fields(self):
        cal = MarketCalendarSession(
            market="",
            trade_date="",
            session_open="",
            session_close="",
            timezone="",
        )
        valid, errors = MarketCalendarSession.validate(cal)
        self.assertFalse(valid)

    def test_schema_file_exists(self):
        schema_path = SCHEMAS_DIR / "market_calendar_session.schema.json"
        self.assertTrue(schema_path.exists())

    def test_holiday_session_is_schema_valid(self):
        cal = MarketCalendarSession(
            market="TW",
            trade_date="2026-02-28",
            session_open="",
            session_close="",
            timezone="Asia/Taipei",
            holiday_flag=True,
        )
        self.assertEqual(_validate_schema("market_calendar_session", cal.to_dict()), [])

    def test_non_holiday_empty_time_is_schema_invalid(self):
        cal = MarketCalendarSession(
            market="US",
            trade_date="2026-04-13",
            session_open="",
            session_close="16:00:00",
            timezone="America/New_York",
        )
        valid, errors = MarketCalendarSession.validate(cal)
        self.assertFalse(valid)
        self.assertTrue(_validate_schema("market_calendar_session", cal.to_dict()))


class TestRawDataset(unittest.TestCase):
    """RawDataset model and schema validation."""

    def test_valid_raw_dataset(self):
        ds = RawDataset(
            dataset_id="RAW-US-DAILY-2026Q1",
            source_class="market",
            market="US",
            instrument_scope=["SEC-US0378331005", "SEC-US88160R1014"],
            coverage_start="2026-01-01",
            coverage_end="2026-03-31",
            ingest_time="2026-04-01T00:00:00Z",
            storage_ref="gs://pantheon-data/raw/us-daily-2026q1.parquet",
            checksum="sha256:abc123",
        )
        valid, errors = RawDataset.validate(ds)
        self.assertTrue(valid, errors)

    def test_missing_required_fields(self):
        ds = RawDataset(
            dataset_id="",
            source_class="",
            market="",
            instrument_scope=[],
            coverage_start="",
            coverage_end="",
            ingest_time="",
            storage_ref="",
            checksum="",
        )
        valid, errors = RawDataset.validate(ds)
        self.assertFalse(valid)
        self.assertGreater(len(errors), 0)

    def test_schema_file_exists(self):
        schema_path = SCHEMAS_DIR / "raw_dataset.schema.json"
        self.assertTrue(schema_path.exists())

    def test_invalid_source_class_rejected_by_model_and_schema(self):
        ds = RawDataset(
            dataset_id="RAW-US-DAILY-2026Q1",
            source_class="banana",
            market="US",
            instrument_scope=["SEC-US0378331005"],
            coverage_start="2026-01-01",
            coverage_end="2026-03-31",
            ingest_time="2026-04-01T00:00:00Z",
            storage_ref="gs://pantheon-data/raw/us-daily-2026q1.parquet",
            checksum="sha256:abc123",
        )
        valid, errors = RawDataset.validate(ds)
        self.assertFalse(valid)
        self.assertTrue(any("source_class" in error for error in errors))
        self.assertTrue(_validate_schema("raw_dataset", ds.to_dict()))


class TestNormalizedDataset(unittest.TestCase):
    """NormalizedDataset model and schema validation."""

    def test_valid_normalized_dataset(self):
        ds = NormalizedDataset(
            dataset_id="NORM-US-DAILY-2026Q1-v1",
            parent_raw_dataset_id="RAW-US-DAILY-2026Q1",
            normalization_version="v1.0",
            storage_ref="gs://pantheon-data/norm/us-daily-2026q1-v1.parquet",
            checksum="sha256:def456",
        )
        valid, errors = NormalizedDataset.validate(ds)
        self.assertTrue(valid, errors)

    def test_missing_parent_reference(self):
        ds = NormalizedDataset(
            dataset_id="NORM-US-DAILY-2026Q1-v1",
            parent_raw_dataset_id="",
            normalization_version="v1.0",
            storage_ref="gs://pantheon-data/norm/us-daily-2026q1-v1.parquet",
            checksum="sha256:def456",
        )
        valid, errors = NormalizedDataset.validate(ds)
        self.assertFalse(valid)

    def test_schema_file_exists(self):
        schema_path = SCHEMAS_DIR / "normalized_dataset.schema.json"
        self.assertTrue(schema_path.exists())

    def test_invalid_available_time_policy_rejected_by_model_and_schema(self):
        ds = NormalizedDataset(
            dataset_id="NORM-US-DAILY-2026Q1-v1",
            parent_raw_dataset_id="RAW-US-DAILY-2026Q1",
            normalization_version="v1.0",
            storage_ref="gs://pantheon-data/norm/us-daily-2026q1-v1.parquet",
            checksum="sha256:def456",
            available_time_policy="banana",
        )
        valid, errors = NormalizedDataset.validate(ds)
        self.assertFalse(valid)
        self.assertTrue(any("available_time_policy" in error for error in errors))
        self.assertTrue(_validate_schema("normalized_dataset", ds.to_dict()))


class TestFeatureDataset(unittest.TestCase):
    """FeatureDataset model and schema validation."""

    def test_valid_feature_dataset(self):
        ds = FeatureDataset(
            dataset_id="FEAT-US-DAILY-2026Q1-v1",
            parent_normalized_dataset_id="NORM-US-DAILY-2026Q1-v1",
            feature_spec_version="v2.0",
            label_spec_version="v1.0",
            point_in_time_rule="available_time <= event_time + 0d",
            storage_ref="gs://pantheon-data/feat/us-daily-2026q1-v1.parquet",
            checksum="sha256:ghi789",
        )
        valid, errors = FeatureDataset.validate(ds)
        self.assertTrue(valid, errors)

    def test_missing_point_in_time_rule(self):
        ds = FeatureDataset(
            dataset_id="FEAT-US-DAILY-2026Q1-v1",
            parent_normalized_dataset_id="NORM-US-DAILY-2026Q1-v1",
            feature_spec_version="v2.0",
            label_spec_version="v1.0",
            point_in_time_rule="",
            storage_ref="gs://pantheon-data/feat/us-daily-2026q1-v1.parquet",
            checksum="sha256:ghi789",
        )
        valid, errors = FeatureDataset.validate(ds)
        self.assertFalse(valid)

    def test_schema_file_exists(self):
        schema_path = SCHEMAS_DIR / "feature_dataset.schema.json"
        self.assertTrue(schema_path.exists())


class TestDatasetVersion(unittest.TestCase):
    """DatasetVersion model and schema validation."""

    def test_valid_dataset_version(self):
        dv = DatasetVersion(
            dataset_version_id="DV-US-DAILY-2026Q1-FROZEN",
            market_scope=["US"],
            instrument_scope=["SEC-US0378331005"],
            raw_dataset_refs=["RAW-US-DAILY-2026Q1"],
            normalized_dataset_refs=["NORM-US-DAILY-2026Q1-v1"],
            feature_dataset_refs=["FEAT-US-DAILY-2026Q1-v1"],
        )
        valid, errors = DatasetVersion.validate(dv)
        self.assertTrue(valid, errors)

    def test_freeze(self):
        dv = DatasetVersion(
            dataset_version_id="DV-US-DAILY-2026Q1-FROZEN",
            market_scope=["US"],
            instrument_scope=["SEC-US0378331005"],
            raw_dataset_refs=["RAW-US-DAILY-2026Q1"],
            normalized_dataset_refs=["NORM-US-DAILY-2026Q1-v1"],
            feature_dataset_refs=["FEAT-US-DAILY-2026Q1-v1"],
        )
        self.assertIsNone(dv.frozen_at)
        dv.freeze()
        self.assertIsNotNone(dv.frozen_at)

    def test_missing_raw_refs(self):
        dv = DatasetVersion(
            dataset_version_id="DV-US-DAILY-2026Q1-FROZEN",
            market_scope=["US"],
            instrument_scope=["SEC-US0378331005"],
            raw_dataset_refs=[],
            normalized_dataset_refs=[],
            feature_dataset_refs=[],
        )
        valid, errors = DatasetVersion.validate(dv)
        self.assertFalse(valid)

    def test_schema_file_exists(self):
        schema_path = SCHEMAS_DIR / "dataset_version.schema.json"
        self.assertTrue(schema_path.exists())

    def test_missing_lineage_refs_rejected_by_model_and_schema(self):
        dv = DatasetVersion(
            dataset_version_id="DV-US-DAILY-2026Q1-FROZEN",
            market_scope=["US"],
            instrument_scope=["SEC-US0378331005"],
            raw_dataset_refs=["RAW-US-DAILY-2026Q1"],
            normalized_dataset_refs=[],
            feature_dataset_refs=[],
        )
        valid, errors = DatasetVersion.validate(dv)
        self.assertFalse(valid)
        self.assertTrue(any("normalized_dataset_refs" in error for error in errors))
        self.assertTrue(any("feature_dataset_refs" in error for error in errors))
        self.assertTrue(_validate_schema("dataset_version", dv.to_dict()))


class TestDatasetLineageChain(unittest.TestCase):
    """Test the full raw -> normalized -> feature -> version lineage chain."""

    def test_full_lineage(self):
        """Build a complete lineage chain and verify all references match."""
        raw = RawDataset(
            dataset_id="RAW-US-DAILY-2026Q1",
            source_class="market",
            market="US",
            instrument_scope=["SEC-US0378331005"],
            coverage_start="2026-01-01",
            coverage_end="2026-03-31",
            ingest_time="2026-04-01T00:00:00Z",
            storage_ref="gs://pantheon-data/raw/us-daily-2026q1.parquet",
            checksum="sha256:abc123",
        )

        norm = NormalizedDataset(
            dataset_id="NORM-US-DAILY-2026Q1-v1",
            parent_raw_dataset_id=raw.dataset_id,
            normalization_version="v1.0",
            storage_ref="gs://pantheon-data/norm/us-daily-2026q1-v1.parquet",
            checksum="sha256:def456",
        )

        feat = FeatureDataset(
            dataset_id="FEAT-US-DAILY-2026Q1-v1",
            parent_normalized_dataset_id=norm.dataset_id,
            feature_spec_version="v2.0",
            label_spec_version="v1.0",
            point_in_time_rule="available_time <= event_time + 0d",
            storage_ref="gs://pantheon-data/feat/us-daily-2026q1-v1.parquet",
            checksum="sha256:ghi789",
        )

        version = DatasetVersion(
            dataset_version_id="DV-US-DAILY-2026Q1-FROZEN",
            market_scope=["US"],
            instrument_scope=["SEC-US0378331005"],
            raw_dataset_refs=[raw.dataset_id],
            normalized_dataset_refs=[norm.dataset_id],
            feature_dataset_refs=[feat.dataset_id],
        )
        version.freeze()

        # Verify lineage chain
        self.assertEqual(norm.parent_raw_dataset_id, raw.dataset_id)
        self.assertEqual(feat.parent_normalized_dataset_id, norm.dataset_id)
        self.assertIn(raw.dataset_id, version.raw_dataset_refs)
        self.assertIn(norm.dataset_id, version.normalized_dataset_refs)
        self.assertIn(feat.dataset_id, version.feature_dataset_refs)
        self.assertIsNotNone(version.frozen_at)

        # Verify all objects pass validation
        for obj, cls in [
            (raw, RawDataset),
            (norm, NormalizedDataset),
            (feat, FeatureDataset),
            (version, DatasetVersion),
        ]:
            valid, errors = cls.validate(obj)
            self.assertTrue(valid, f"{cls.__name__} validation failed: {errors}")

        for schema_name, obj in [
            ("raw_dataset", raw),
            ("normalized_dataset", norm),
            ("feature_dataset", feat),
            ("dataset_version", version),
        ]:
            self.assertEqual(_validate_schema(schema_name, obj.to_dict()), [])


class TestAllSchemaFilesValid(unittest.TestCase):
    """Verify all JSON schema files are valid JSON."""

    def test_all_schemas_parse(self):
        schema_files = list(SCHEMAS_DIR.glob("*.schema.json"))
        self.assertGreater(len(schema_files), 0)
        for path in schema_files:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("$schema", data)
            self.assertIn("title", data)
            self.assertIn("properties", data)


if __name__ == "__main__":
    unittest.main()

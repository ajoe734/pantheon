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

ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Handle hyphenated directory name
_DATA_PLANE = Path(__file__).resolve().parent.parent
if str(_DATA_PLANE.parent) not in sys.path:
    sys.path.insert(0, str(_DATA_PLANE.parent))
_ADAPTERS_DIR = ROOT / "services" / "research" / "adapters"
if str(_ADAPTERS_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTERS_DIR))

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
taiwan_reference_mod = _load_module("taiwan_reference", _DATA_PLANE / "taiwan_reference.py")
us_reference_mod = _load_module("us_equity_reference", _DATA_PLANE / "us_equity_reference.py")
crypto_reference_mod = _load_module("crypto_reference", _DATA_PLANE / "crypto_reference.py")
from coingecko_client import CoinGeckoClient
from services.execution.kraken_adapter import KrakenAdapter, KrakenConfig

SecurityMaster = security_master_mod.SecurityMaster
ContractMaster = contract_master_mod.ContractMaster
MarketCalendarSession = market_calendar_mod.MarketCalendarSession
RawDataset = dataset_lineage_mod.RawDataset
NormalizedDataset = dataset_lineage_mod.NormalizedDataset
FeatureDataset = dataset_lineage_mod.FeatureDataset
DatasetVersion = dataset_lineage_mod.DatasetVersion
build_tw_security_master = taiwan_reference_mod.build_tw_security_master
build_tw_calendar_session = taiwan_reference_mod.build_tw_calendar_session
build_tw_dataset_lineage_source = taiwan_reference_mod.build_tw_dataset_lineage_source
build_tw_symbol_mapping_record = taiwan_reference_mod.build_tw_symbol_mapping_record
build_shioaji_raw_dataset = taiwan_reference_mod.build_shioaji_raw_dataset
build_mops_raw_dataset = taiwan_reference_mod.build_mops_raw_dataset
build_tej_raw_dataset = taiwan_reference_mod.build_tej_raw_dataset
build_tw_normalized_dataset = taiwan_reference_mod.build_tw_normalized_dataset
join_tw_quote_with_reference = taiwan_reference_mod.join_tw_quote_with_reference
build_us_security_master = us_reference_mod.build_us_security_master
build_us_calendar_session = us_reference_mod.build_us_calendar_session
build_polygon_raw_dataset = us_reference_mod.build_polygon_raw_dataset
build_us_normalized_dataset = us_reference_mod.build_us_normalized_dataset
build_us_dataset_lineage_source = us_reference_mod.build_us_dataset_lineage_source
build_crypto_security_master = crypto_reference_mod.build_crypto_security_master
build_kraken_raw_dataset = crypto_reference_mod.build_kraken_raw_dataset
build_crypto_normalized_dataset = crypto_reference_mod.build_crypto_normalized_dataset
build_crypto_dataset_lineage_source = crypto_reference_mod.build_crypto_dataset_lineage_source
join_kraken_quote_with_reference = crypto_reference_mod.join_kraken_quote_with_reference

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


class TestTaiwanReferenceHelpers(unittest.TestCase):
    """Taiwan-specific canonical normalization helpers."""

    def test_build_tw_security_master_from_official_listing(self):
        sec = build_tw_security_master(
            {
                "symbol": "2330",
                "company_name": "台積電",
                "venue": "TWSE",
                "isin": "TW0002330008",
                "industry": "半導體業",
                "listing_date": "1994-09-05",
                "governance_metadata": {"source_key": "twse"},
            }
        )
        self.assertEqual(sec.security_id, "SEC-TW-2330-TWSE")
        self.assertEqual(sec.symbol_canonical, "2330.TWSE")
        self.assertEqual(sec.metadata_json["market_segment"], "listed")
        self.assertEqual(_validate_schema("security_master", sec.to_dict()), [])

    def test_build_tw_calendar_session_handles_holidays_and_early_close(self):
        holiday = build_tw_calendar_session("2026-02-17", holiday_flag=True)
        self.assertEqual(holiday.session_open, "")
        self.assertTrue(holiday.holiday_flag)

        early = build_tw_calendar_session("2026-02-11", early_close=True, venue="TPEx")
        self.assertEqual(early.session_close, "12:30:00")
        self.assertTrue(early.early_close_flag)
        self.assertEqual(_validate_schema("market_calendar_session", early.to_dict()), [])

    def test_build_tw_dataset_lineage_source(self):
        lineage = build_tw_dataset_lineage_source(
            dataset_name="twse_daily_listing_snapshot",
            source_key="twse",
            source_class="official_reference",
            frequency="daily",
            symbol_universe=["2330", "2317"],
        )
        self.assertEqual(lineage["market"], "TW")
        self.assertEqual(lineage["symbol_universe"], ["2330", "2317"])

    def test_build_tw_symbol_mapping_record_preserves_market_segment(self):
        mapping = build_tw_symbol_mapping_record(
            symbol="6488",
            venue="OTC",
            market_segment="OTC",
            source_keys=["tpex", "tej"],
        )
        self.assertEqual(mapping["symbol_canonical"], "6488.TPEX")
        self.assertEqual(mapping["market_segment"], "otc")
        self.assertEqual(mapping["source_keys"], ["tpex", "tej"])

        normalized = build_tw_symbol_mapping_record(
            symbol="2330",
            venue="TWSE",
            market_segment="上市",
        )
        self.assertEqual(normalized["market_segment"], "listed")

    def test_build_tw_raw_and_normalized_dataset(self):
        raw = build_shioaji_raw_dataset(
            dataset_id="RAW-TW-SHIOAJI-TICK-20260424",
            instrument_scope=["SEC-TW-2330-TWSE", "SEC-TW-6488-TPEX"],
            coverage_start="2026-04-24",
            coverage_end="2026-04-24",
            ingest_time="2026-04-24T01:35:00Z",
            storage_ref="gs://pantheon-data/raw/tw/shioaji/tick-20260424.parquet",
            checksum="sha256:aaa111",
        )
        valid, errors = RawDataset.validate(raw)
        self.assertTrue(valid, errors)
        self.assertEqual(raw.source_class, "broker_execution")
        self.assertEqual(raw.metadata_json["provider"], "Shioaji")

        mops_raw = build_mops_raw_dataset(
            dataset_id="RAW-TW-MOPS-DISCLOSURES-20260424",
            instrument_scope=["SEC-TW-2330-TWSE"],
            coverage_start="2026-04-24",
            coverage_end="2026-04-24",
            ingest_time="2026-04-24T13:00:00Z",
            storage_ref="gs://pantheon-data/raw/tw/mops/disclosures-20260424.jsonl",
            checksum="sha256:ccc333",
            route_ids=["t05st02", "t05st03", "t163sb01"],
        )
        valid, errors = RawDataset.validate(mops_raw)
        self.assertTrue(valid, errors)
        self.assertEqual(mops_raw.source_class, "official_reference")
        self.assertEqual(mops_raw.metadata_json["provider"], "MOPS")
        self.assertEqual(mops_raw.metadata_json["governed_role"], "tw_official_disclosure_truth")

        tej_raw = build_tej_raw_dataset(
            dataset_id="RAW-TW-TEJ-TRAIL-20260424",
            instrument_scope=["SEC-TW-2330-TWSE"],
            coverage_start="2026-04-24",
            coverage_end="2026-04-24",
            ingest_time="2026-04-24T13:05:00Z",
            storage_ref="gs://pantheon-data/raw/tw/tej/trail-20260424.parquet",
            checksum="sha256:ddd444",
            dataset_codes=["TRAIL/TAPRCD", "TRAIL/TATINST1", "TRAIL/TAIM1A"],
        )
        valid, errors = RawDataset.validate(tej_raw)
        self.assertTrue(valid, errors)
        self.assertEqual(tej_raw.source_class, "research_grade")
        self.assertTrue(tej_raw.metadata_json["does_not_replace_official_disclosure_truth"])

        norm = build_tw_normalized_dataset(
            dataset_id="NORM-TW-SHIOAJI-TICK-20260424-v1",
            parent_raw_dataset_id=raw.dataset_id,
            storage_ref="gs://pantheon-data/norm/tw/shioaji/tick-20260424-v1.parquet",
            checksum="sha256:bbb222",
            symbol_mapping_version="tw-symbol-map-v1",
            calendar_version="tw-calendar-2026-v1",
            disclosure_join_version="mops-monthly-revenue-v1",
            fundamentals_join_version="tej-aprcd1-v1",
        )
        valid, errors = NormalizedDataset.validate(norm)
        self.assertTrue(valid, errors)
        self.assertEqual(norm.normalization_version, "tw-equity-v1")
        self.assertEqual(norm.metadata_json["source_role"], "broker_quote_plus_official_reference_join")

    def test_join_tw_quote_with_reference(self):
        rows = join_tw_quote_with_reference(
            quote_snapshots=[
                {
                    "symbol": "2330",
                    "venue": "TSE",
                    "ts": "2026-04-24T09:05:00+08:00",
                    "close": 952.0,
                    "bid": 951.0,
                    "ask": 952.0,
                    "day_volume": 1024,
                },
                {
                    "symbol": "6488",
                    "venue": "OTC",
                    "ts": "2026-04-24T09:05:00+08:00",
                    "close": 388.5,
                    "bid": 388.0,
                    "ask": 389.0,
                    "day_volume": 128,
                },
            ],
            listings=[
                {
                    "symbol": "2330",
                    "venue": "TWSE",
                    "company_name": "台積電",
                    "market_segment": "listed",
                    "governance_metadata": {"source_key": "twse"},
                },
                {
                    "symbol": "6488",
                    "venue": "TPEx",
                    "company_name": "環球晶",
                    "governance_metadata": {"source_key": "tpex"},
                },
            ],
            disclosures=[
                {
                    "symbol": "2330",
                    "filing_code": "monthly_revenue",
                    "disclosure_date": "2026-04-10",
                },
                {
                    "symbol": "2330",
                    "filing_code": "board_resolution",
                    "disclosure_date": "2026-04-18",
                },
            ],
            tej_records=[
                {
                    "dataset_code": "TWN/APRCD1",
                    "symbol": "2330",
                    "as_of_date": "2026-04-24",
                    "values": {"pe_ratio": 18.2},
                },
                {
                    "dataset_code": "TWN/OWNERSHIP",
                    "symbol": "6488",
                    "as_of_date": "2026-04-24",
                    "values": {"foreign_holding_pct": 21.5},
                },
            ],
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["symbol_canonical"], "2330.TWSE")
        self.assertEqual(rows[0]["market_segment"], "listed")
        self.assertEqual(rows[0]["disclosure_count"], 2)
        self.assertEqual(rows[0]["latest_disclosure_date"], "2026-04-18")
        self.assertEqual(rows[0]["tej_latest_values"]["pe_ratio"], 18.2)
        self.assertEqual(rows[1]["symbol_canonical"], "6488.TPEX")
        self.assertEqual(rows[1]["market_segment"], "otc")


class TestUSReferenceHelpers(unittest.TestCase):
    """US-specific canonical normalization helpers."""

    def test_build_us_security_master(self):
        sec = build_us_security_master(
            {
                "venue": "NASDAQ",
                "symbol": "AAPL",
                "isin": "US0378331005",
                "cusip": "037833100",
                "figi": "BBG000B9XRY4",
                "company_name": "Apple Inc.",
                "governance_metadata": {"source_key": "polygon_tickers"},
            }
        )
        self.assertEqual(sec.security_id, "SEC-US-AAPL-NASDAQ")
        self.assertEqual(sec.symbol_canonical, "AAPL.US")
        self.assertEqual(_validate_schema("security_master", sec.to_dict()), [])

    def test_build_us_calendar_session_handles_early_close(self):
        early = build_us_calendar_session("2026-11-27", early_close=True, venue="NYSE")
        self.assertEqual(early.session_close, "13:00:00")
        self.assertTrue(early.early_close_flag)
        self.assertEqual(_validate_schema("market_calendar_session", early.to_dict()), [])

    def test_build_polygon_raw_dataset_and_normalized_dataset(self):
        raw = build_polygon_raw_dataset(
            dataset_id="RAW-US-POLYGON-1MIN-2026Q1",
            instrument_scope=["SEC-US-AAPL-NASDAQ"],
            coverage_start="2026-01-01",
            coverage_end="2026-03-31",
            ingest_time="2026-04-01T00:00:00Z",
            storage_ref="gs://pantheon-data/raw/us/polygon/1min-2026q1.parquet",
            checksum="sha256:abc123",
            dataset_type="aggregates_1m",
        )
        valid, errors = RawDataset.validate(raw)
        self.assertTrue(valid, errors)
        self.assertEqual(raw.source_class, "research_grade")
        self.assertEqual(raw.metadata_json["governed_role"], "us_research_grade_primary")

        norm = build_us_normalized_dataset(
            dataset_id="NORM-US-POLYGON-1MIN-2026Q1-v1",
            parent_raw_dataset_id=raw.dataset_id,
            storage_ref="gs://pantheon-data/norm/us/polygon/1min-2026q1-v1.parquet",
            checksum="sha256:def456",
            symbol_mapping_version="us-symbol-map-v1",
            corp_action_version="polygon-ca-v1",
            calendar_version="nyse-2026-v1",
        )
        valid, errors = NormalizedDataset.validate(norm)
        self.assertTrue(valid, errors)
        self.assertEqual(norm.metadata_json["provider"], "Massive / Polygon")
        self.assertEqual(norm.metadata_json["source_role"], "primary_research_grade")

    def test_build_us_dataset_lineage_source(self):
        lineage = build_us_dataset_lineage_source(
            dataset_name="polygon_daily_ohlcv",
            source_key="polygon",
            source_class="research_grade",
            frequency="daily",
            symbol_universe=["AAPL", "MSFT"],
        )
        self.assertEqual(lineage["market"], "US")
        self.assertEqual(lineage["source_class"], "research_grade")


class TestCryptoReferenceHelpers(unittest.TestCase):
    """Crypto-specific canonical normalization helpers."""

    def test_build_crypto_security_master_from_kraken_listing(self):
        sec = build_crypto_security_master(
            {
                "base_asset": "BTC",
                "quote_asset": "USD",
                "venue": "KRAKEN",
                "pair_status": "online",
                "coingecko_id": "bitcoin",
                "governance_metadata": {"source_key": "kraken_asset_pairs"},
            }
        )
        self.assertEqual(sec.security_id, "SEC-CRYPTO-BTCUSD-KRAKEN")
        self.assertEqual(sec.symbol_native, "BTC/USD")
        self.assertEqual(sec.symbol_canonical, "BTCUSD.KRAKEN")
        self.assertEqual(sec.metadata_json["coingecko_id"], "bitcoin")
        self.assertEqual(_validate_schema("security_master", sec.to_dict()), [])

    def test_build_kraken_raw_and_crypto_normalized_dataset(self):
        raw = build_kraken_raw_dataset(
            dataset_id="RAW-CRYPTO-KRAKEN-SPOT-20260424",
            instrument_scope=["SEC-CRYPTO-BTCUSD-KRAKEN"],
            coverage_start="2026-04-24",
            coverage_end="2026-04-24",
            ingest_time="2026-04-24T04:00:00Z",
            storage_ref="gs://pantheon-data/raw/crypto/kraken/spot-20260424.parquet",
            checksum="sha256:ccc333",
            dataset_type="spot_ohlcv",
        )
        valid, errors = RawDataset.validate(raw)
        self.assertTrue(valid, errors)
        self.assertEqual(raw.metadata_json["provider"], "Kraken")

        norm = build_crypto_normalized_dataset(
            dataset_id="NORM-CRYPTO-KRAKEN-SPOT-20260424-v1",
            parent_raw_dataset_id=raw.dataset_id,
            storage_ref="gs://pantheon-data/norm/crypto/kraken/spot-20260424-v1.parquet",
            checksum="sha256:ddd444",
            symbol_mapping_version="crypto-symbol-map-v1",
            reference_join_version="coingecko-asset-ref-v1",
        )
        valid, errors = NormalizedDataset.validate(norm)
        self.assertTrue(valid, errors)
        self.assertEqual(norm.normalization_version, "crypto-v1")
        self.assertEqual(norm.metadata_json["execution_truth_provider"], "Kraken")
        self.assertEqual(norm.metadata_json["reference_provider"], "CoinGecko")

    def test_build_crypto_dataset_lineage_source(self):
        lineage = build_crypto_dataset_lineage_source(
            dataset_name="kraken_spot_ohlcv",
            source_key="kraken",
            source_class="broker_execution",
            frequency="1m",
            symbol_universe=["BTCUSD", "ETHUSD"],
        )
        self.assertEqual(lineage["market"], "CRYPTO")
        self.assertEqual(lineage["symbol_universe"], ["BTCUSD", "ETHUSD"])

    def test_join_kraken_quote_with_reference(self):
        metadata_row = CoinGeckoClient(rate_limit_delay=0).normalize_asset(
            {
                "id": "bitcoin",
                "symbol": "btc",
                "name": "Bitcoin",
                "market_cap_rank": 1,
                "categories": ["Smart Contract Platform"],
            }
        ).to_dict()
        quote_snapshot = KrakenAdapter(
            KrakenConfig(api_key="key", api_secret="secret")
        ).normalize_quote(
            {
                "ts": "2026-04-24T16:00:00Z",
                "close": "64320.4",
                "bid": "64320.1",
                "ask": "64321.0",
                "volume": "128.55",
            },
            "BTCUSD.KRAKEN",
        )
        rows = join_kraken_quote_with_reference(
            quote_snapshots=[quote_snapshot.to_dict()],
            asset_metadata=[metadata_row],
        )
        self.assertEqual(rows[0]["quote_close"], 64320.4)
        self.assertEqual(rows[0]["quote_last"], 64320.4)
        self.assertEqual(rows[0]["execution_provider"], "Kraken")
        self.assertEqual(rows[0]["quote_transport"], "rest")
        self.assertEqual(rows[0]["replay_source"], "rest_snapshot")
        self.assertFalse(rows[0]["runtime_replay_ready"])
        self.assertEqual(rows[0]["reference_provider"], "CoinGecko")
        self.assertEqual(rows[0]["coingecko_id"], "bitcoin")

    def test_join_kraken_quote_with_reference_preserves_payload_close_when_last_differs(self):
        metadata_row = CoinGeckoClient(rate_limit_delay=0).normalize_asset(
            {
                "id": "bitcoin",
                "symbol": "btc",
                "name": "Bitcoin",
                "market_cap_rank": 1,
            }
        ).to_dict()
        quote_snapshot = KrakenAdapter(
            KrakenConfig(api_key="key", api_secret="secret")
        ).normalize_quote(
            {
                "ts": "2026-04-24T16:00:00Z",
                "last": "64321.1",
                "close": "64320.4",
                "bid": "64320.1",
                "ask": "64321.0",
                "volume": "128.55",
            },
            "BTCUSD.KRAKEN",
        )
        rows = join_kraken_quote_with_reference(
            quote_snapshots=[quote_snapshot.to_dict()],
            asset_metadata=[metadata_row],
        )
        self.assertEqual(rows[0]["quote_close"], 64320.4)
        self.assertEqual(rows[0]["quote_last"], 64321.1)
        self.assertEqual(quote_snapshot.last, 64321.1)
        self.assertEqual(quote_snapshot.close, 64320.4)

    def test_join_kraken_quote_with_reference_preserves_websocket_replay_metadata(self):
        metadata_row = CoinGeckoClient(rate_limit_delay=0).normalize_asset(
            {
                "id": "bitcoin",
                "symbol": "btc",
                "name": "Bitcoin",
                "market_cap_rank": 1,
            }
        ).to_dict()
        adapter = KrakenAdapter(KrakenConfig(api_key="key", api_secret="secret"))
        sync_state = adapter.reconcile_execution_sync(
            symbol="BTCUSD.KRAKEN",
            rest_payload={
                "ts": "2026-04-24T16:00:00Z",
                "close": "64320.4",
            },
            websocket_payload={
                "channel": "ticker",
                "ts": "2026-04-24T16:00:03Z",
                "a": ["64321.0", "1", "1.0"],
                "b": ["64320.1", "2", "2.0"],
                "c": ["64320.9", "0.3"],
                "v": ["120.0", "128.55"],
            },
        )
        rows = join_kraken_quote_with_reference(
            quote_snapshots=[sync_state.to_dict()],
            asset_metadata=[metadata_row],
        )
        self.assertEqual(rows[0]["quote_timestamp"], "2026-04-24T16:00:03Z")
        self.assertEqual(rows[0]["quote_last"], 64320.9)
        self.assertEqual(rows[0]["quote_close"], 64320.4)
        self.assertEqual(rows[0]["quote_transport"], "websocket")
        self.assertEqual(rows[0]["quote_channel"], "ticker")
        self.assertEqual(rows[0]["replay_source"], "websocket_backed_sync")
        self.assertTrue(rows[0]["runtime_replay_ready"])

    def test_join_kraken_quote_with_reference_supports_non_usd_quote_suffixes(self):
        metadata_row = CoinGeckoClient(rate_limit_delay=0).normalize_asset(
            {
                "id": "matic-network",
                "symbol": "matic",
                "name": "Polygon",
                "market_cap_rank": 42,
            }
        ).to_dict()
        quote_snapshot = KrakenAdapter(
            KrakenConfig(api_key="key", api_secret="secret")
        ).normalize_quote(
            {
                "ts": "2026-04-24T16:00:00Z",
                "close": "0.55",
                "bid": "0.54",
                "ask": "0.56",
            },
            "MATICGBP.KRAKEN",
        )
        rows = join_kraken_quote_with_reference(
            quote_snapshots=[quote_snapshot.to_dict()],
            asset_metadata=[metadata_row],
        )
        self.assertEqual(rows[0]["symbol_canonical"], "MATICGBP.KRAKEN")
        self.assertEqual(rows[0]["coingecko_id"], "matic-network")
        self.assertEqual(rows[0]["asset_name"], "Polygon")


class TestRawDataset(unittest.TestCase):
    """RawDataset model and schema validation."""

    def test_valid_raw_dataset(self):
        ds = RawDataset(
            dataset_id="RAW-US-DAILY-2026Q1",
            source_class="research_grade",
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
            source_class="research_grade",
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

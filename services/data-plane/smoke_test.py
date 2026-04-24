"""Smoke test for data-plane schemas.

Validates the complete end-to-end flow:
1. All JSON schema files parse correctly.
2. All model objects serialize to valid JSON matching their schemas.
3. A full lineage chain (raw -> normalized -> feature -> version) is coherent.
4. Cross-references between SecurityMaster, ContractMaster, and datasets are valid.
"""

import json
import sys
from pathlib import Path
from jsonschema import Draft7Validator, FormatChecker

_DATA_PLANE = Path(__file__).resolve().parent
if str(_DATA_PLANE.parent.parent) not in sys.path:
    sys.path.insert(0, str(_DATA_PLANE.parent.parent))

from importlib.util import spec_from_file_location, module_from_spec


def _load(name, path):
    spec = spec_from_file_location(name, path)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_models = _DATA_PLANE / "models"
_sm = _load("security_master", _models / "security_master.py")
_cm = _load("contract_master", _models / "contract_master.py")
_mc = _load("market_calendar_session", _models / "market_calendar_session.py")
_dl = _load("dataset_lineage", _models / "dataset_lineage.py")

SCHEMAS_DIR = _DATA_PLANE / "schemas"
FORMAT_CHECKER = FormatChecker()

PASS_COUNT = 0
FAIL_COUNT = 0


def check(name, condition, detail=""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  PASS: {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL: {name} — {detail}")


def _schema_errors(filename: str, payload: dict) -> list[str]:
    schema = json.loads((SCHEMAS_DIR / filename).read_text(encoding="utf-8"))
    validator = Draft7Validator(schema, format_checker=FORMAT_CHECKER)
    return sorted(error.message for error in validator.iter_errors(payload))


def main():
    global PASS_COUNT, FAIL_COUNT

    print("=== Data Plane Schema Smoke Test ===\n")

    # 1. All schema files parse
    print("[1] Schema files parse correctly")
    schema_files = {
        "security_master": "security_master.schema.json",
        "contract_master": "contract_master.schema.json",
        "market_calendar_session": "market_calendar_session.schema.json",
        "raw_dataset": "raw_dataset.schema.json",
        "normalized_dataset": "normalized_dataset.schema.json",
        "feature_dataset": "feature_dataset.schema.json",
        "dataset_version": "dataset_version.schema.json",
    }
    for name, filename in schema_files.items():
        path = SCHEMAS_DIR / filename
        exists = path.exists()
        check(f"{filename} exists", exists)
        if exists:
            data = json.loads(path.read_text(encoding="utf-8"))
            check(f"{filename} parses", "$schema" in data and "properties" in data)

    # 2. Model objects serialize and validate
    print("\n[2] Model objects serialize and validate")

    sec = _sm.SecurityMaster(
        security_id="SEC-US0378331005",
        market="US",
        venue="NASDAQ",
        symbol_native="AAPL",
        symbol_canonical="US0378331005",
        asset_type="equity",
        currency="USD",
    )
    valid, errors = _sm.SecurityMaster.validate(sec)
    check("SecurityMaster validates", valid, errors)
    sec_json = json.loads(sec.to_json())
    check("SecurityMaster serializes", sec_json["security_id"] == "SEC-US0378331005")
    check("SecurityMaster matches schema", not _schema_errors("security_master.schema.json", sec.to_dict()), _schema_errors("security_master.schema.json", sec.to_dict()))

    con = _cm.ContractMaster(
        contract_id="CON-US-ES-202606",
        underlying_id=sec.security_id,
        market="US",
        venue="CME",
        contract_type="future",
        expiry="2026-06-19",
        multiplier=50.0,
        tick_size=0.25,
    )
    valid, errors = _cm.ContractMaster.validate(con)
    check("ContractMaster validates", valid, errors)
    check("ContractMaster references SecurityMaster", con.underlying_id == sec.security_id)
    check("ContractMaster matches schema", not _schema_errors("contract_master.schema.json", con.to_dict()), _schema_errors("contract_master.schema.json", con.to_dict()))

    cal = _mc.MarketCalendarSession(
        market="US",
        trade_date="2026-04-13",
        session_open="09:30:00",
        session_close="16:00:00",
        timezone="America/New_York",
    )
    valid, errors = _mc.MarketCalendarSession.validate(cal)
    check("MarketCalendarSession validates", valid, errors)
    check("MarketCalendarSession matches schema", not _schema_errors("market_calendar_session.schema.json", cal.to_dict()), _schema_errors("market_calendar_session.schema.json", cal.to_dict()))

    holiday_cal = _mc.MarketCalendarSession(
        market="TW",
        trade_date="2026-02-28",
        session_open="",
        session_close="",
        timezone="Asia/Taipei",
        holiday_flag=True,
    )
    valid, errors = _mc.MarketCalendarSession.validate(holiday_cal)
    check("Holiday MarketCalendarSession validates", valid, errors)
    check("Holiday MarketCalendarSession matches schema", not _schema_errors("market_calendar_session.schema.json", holiday_cal.to_dict()), _schema_errors("market_calendar_session.schema.json", holiday_cal.to_dict()))

    # 3. Full lineage chain
    print("\n[3] Full lineage chain (raw -> normalized -> feature -> version)")

    raw = _dl.RawDataset(
        dataset_id="RAW-US-DAILY-2026Q1",
        source_class="research_grade",
        market="US",
        instrument_scope=[sec.security_id],
        coverage_start="2026-01-01",
        coverage_end="2026-03-31",
        ingest_time="2026-04-01T00:00:00Z",
        storage_ref="gs://pantheon-data/raw/us-daily-2026q1.parquet",
        checksum="sha256:abc123",
    )
    valid, errors = _dl.RawDataset.validate(raw)
    check("RawDataset validates", valid, errors)
    check("RawDataset matches schema", not _schema_errors("raw_dataset.schema.json", raw.to_dict()), _schema_errors("raw_dataset.schema.json", raw.to_dict()))

    norm = _dl.NormalizedDataset(
        dataset_id="NORM-US-DAILY-2026Q1-v1",
        parent_raw_dataset_id=raw.dataset_id,
        normalization_version="v1.0",
        storage_ref="gs://pantheon-data/norm/us-daily-2026q1-v1.parquet",
        checksum="sha256:def456",
    )
    valid, errors = _dl.NormalizedDataset.validate(norm)
    check("NormalizedDataset validates", valid, errors)
    check("NormalizedDataset -> RawDataset link", norm.parent_raw_dataset_id == raw.dataset_id)
    check("NormalizedDataset matches schema", not _schema_errors("normalized_dataset.schema.json", norm.to_dict()), _schema_errors("normalized_dataset.schema.json", norm.to_dict()))

    feat = _dl.FeatureDataset(
        dataset_id="FEAT-US-DAILY-2026Q1-v1",
        parent_normalized_dataset_id=norm.dataset_id,
        feature_spec_version="v2.0",
        label_spec_version="v1.0",
        point_in_time_rule="available_time <= event_time + 0d",
        storage_ref="gs://pantheon-data/feat/us-daily-2026q1-v1.parquet",
        checksum="sha256:ghi789",
    )
    valid, errors = _dl.FeatureDataset.validate(feat)
    check("FeatureDataset validates", valid, errors)
    check("FeatureDataset -> NormalizedDataset link", feat.parent_normalized_dataset_id == norm.dataset_id)
    check("FeatureDataset matches schema", not _schema_errors("feature_dataset.schema.json", feat.to_dict()), _schema_errors("feature_dataset.schema.json", feat.to_dict()))

    version = _dl.DatasetVersion(
        dataset_version_id="DV-US-DAILY-2026Q1-FROZEN",
        market_scope=["US"],
        instrument_scope=[sec.security_id],
        raw_dataset_refs=[raw.dataset_id],
        normalized_dataset_refs=[norm.dataset_id],
        feature_dataset_refs=[feat.dataset_id],
    )
    version.freeze()
    valid, errors = _dl.DatasetVersion.validate(version)
    check("DatasetVersion validates", valid, errors)
    check("DatasetVersion is frozen", version.frozen_at is not None)
    check("DatasetVersion -> RawDataset ref", raw.dataset_id in version.raw_dataset_refs)
    check("DatasetVersion -> NormalizedDataset ref", norm.dataset_id in version.normalized_dataset_refs)
    check("DatasetVersion -> FeatureDataset ref", feat.dataset_id in version.feature_dataset_refs)
    check("DatasetVersion matches schema", not _schema_errors("dataset_version.schema.json", version.to_dict()), _schema_errors("dataset_version.schema.json", version.to_dict()))

    # 4. event_time / available_time / ingest_time discipline
    print("\n[4] Availability discipline")
    check("RawDataset has ingest_time", raw.ingest_time is not None and raw.ingest_time != "")
    check("NormalizedDataset has available_time_policy", norm.available_time_policy in [
        "at_open", "at_reported", "at_ingest", "delayed_minutes", "custom"
    ])
    check("FeatureDataset has point_in_time_rule", feat.point_in_time_rule != "")

    # 5. Source class enumeration
    print("\n[5] Source class coverage")
    for sc in [
        "official_reference",
        "broker_execution",
        "research_grade",
        "derivative_analytics",
        "crypto_analytics",
        "internal_can",
    ]:
        check(f"SourceClass '{sc}' usable", sc in [e.value for e in _dl.SourceClass])

    # Summary
    print(f"\n=== Results: {PASS_COUNT} passed, {FAIL_COUNT} failed ===")
    if FAIL_COUNT > 0:
        print("SMOKE TEST FAILED")
        sys.exit(1)
    else:
        print("ALL SMOKE CHECKS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()

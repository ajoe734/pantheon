"""Generate JSON schemas from data-plane models.

This script is standalone to avoid import issues with hyphenated directory names.
It defines the enums inline so it can run without importing the model modules.
"""

import json
from pathlib import Path


# Inline enum values (mirror the model definitions)
ASSET_TYPES = ["equity", "etf", "future", "option", "forex", "crypto", "bond", "index", "warrant", "right"]
LISTING_STATUSES = ["active", "suspended", "delisted", "pending"]
CONTRACT_TYPES = ["future", "option", "future_option", "swap", "forward", "spread"]
OPTION_RIGHTS = ["call", "put"]
SETTLEMENT_TYPES = ["cash", "physical"]
MARGIN_TYPES = ["portfolio", "strat_scan", "fixed", "none"]
SOURCE_CLASSES = ["market", "fundamental", "event", "alternative", "execution_internal", "human_feedback"]
AVAILABLE_TIME_POLICIES = ["at_open", "at_reported", "at_ingest", "delayed_minutes", "custom"]
TIME_PATTERN = "^([01]\\d|2[0-3]):[0-5]\\d:[0-5]\\d$"

SCHEMAS = {
    "security_master": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "SecurityMaster",
        "description": "Canonical security identity for Pantheon Data Plane.",
        "type": "object",
        "required": [
            "security_id", "market", "venue", "symbol_native",
            "symbol_canonical", "asset_type", "currency",
        ],
        "properties": {
            "security_id": {"type": "string", "description": "Globally unique identifier (e.g., SEC-US0378331005)."},
            "market": {"type": "string", "description": "Market code (e.g., US, TW, CRYPTO)."},
            "venue": {"type": "string", "description": "Primary venue / exchange code (e.g., NASDAQ, TWSE)."},
            "symbol_native": {"type": "string", "description": "Native symbol as used by the venue (e.g., AAPL, 2330)."},
            "symbol_canonical": {"type": "string", "description": "Normalized cross-venue symbol (e.g., ISIN or internal canonical form)."},
            "asset_type": {"type": "string", "enum": ASSET_TYPES, "description": "One of the supported asset types."},
            "currency": {"type": "string", "description": "ISO 4217 currency code (e.g., USD, TWD)."},
            "underlying_id": {"type": ["string", "null"], "description": "For derivatives, references the underlying security_id."},
            "listing_status": {"type": "string", "enum": LISTING_STATUSES, "default": "active", "description": "Current lifecycle status."},
            "metadata_json": {"type": "object", "default": {}, "description": "Free-form venue or market-specific metadata."},
            "created_at": {"type": "string", "format": "date-time", "description": "ISO 8601 timestamp of record creation."},
            "updated_at": {"type": "string", "format": "date-time", "description": "ISO 8601 timestamp of last update."},
        },
    },
    "contract_master": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "ContractMaster",
        "description": "Canonical derivative contract identity for Pantheon Data Plane.",
        "type": "object",
        "required": [
            "contract_id", "underlying_id", "market", "venue",
            "contract_type", "expiry", "multiplier", "tick_size",
        ],
        "properties": {
            "contract_id": {"type": "string", "description": "Globally unique identifier (e.g., CON-US-ES-202606)."},
            "underlying_id": {"type": "string", "description": "Reference to the underlying SecurityMaster security_id."},
            "market": {"type": "string", "description": "Market code (e.g., US, TW, CRYPTO)."},
            "venue": {"type": "string", "description": "Primary venue / exchange code (e.g., CME, TAIFEX)."},
            "contract_type": {"type": "string", "enum": CONTRACT_TYPES, "description": "One of the ContractType enum values."},
            "expiry": {"type": "string", "format": "date", "description": "Contract expiry date in ISO 8601 format."},
            "strike": {"type": ["number", "null"], "description": "Strike price for options; null for futures/forwards."},
            "option_right": {"type": ["string", "null"], "enum": [None] + OPTION_RIGHTS, "description": "CALL or PUT for options; null otherwise."},
            "multiplier": {"type": "number", "exclusiveMinimum": 0, "description": "Contract multiplier (e.g., 50 for ES futures)."},
            "tick_size": {"type": "number", "exclusiveMinimum": 0, "description": "Minimum price increment."},
            "settlement_type": {"type": "string", "enum": SETTLEMENT_TYPES, "default": "cash", "description": "Cash or physical settlement."},
            "margin_type": {"type": "string", "enum": MARGIN_TYPES, "default": "none", "description": "Margin calculation method."},
            "metadata_json": {"type": "object", "default": {}, "description": "Free-form venue or contract-specific metadata."},
            "created_at": {"type": "string", "format": "date-time", "description": "ISO 8601 timestamp of record creation."},
            "updated_at": {"type": "string", "format": "date-time", "description": "ISO 8601 timestamp of last update."},
        },
    },
    "market_calendar_session": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "MarketCalendarSession",
        "description": "Market trading calendar session for Pantheon Data Plane.",
        "type": "object",
        "required": ["market", "trade_date", "session_open", "session_close", "timezone"],
        "properties": {
            "market": {"type": "string", "description": "Market code (e.g., US, TW, CRYPTO)."},
            "trade_date": {"type": "string", "format": "date", "description": "Trading date in ISO 8601 format."},
            "session_open": {"type": "string", "description": "Session open time in HH:MM:SS format (market local). Empty string is allowed for holidays."},
            "session_close": {"type": "string", "description": "Session close time in HH:MM:SS format (market local). Empty string is allowed for holidays."},
            "early_close_flag": {"type": "boolean", "default": False, "description": "True if this session has an early close."},
            "holiday_flag": {"type": "boolean", "default": False, "description": "True if this date is a holiday (no trading)."},
            "timezone": {"type": "string", "description": "IANA timezone identifier."},
            "metadata_json": {"type": "object", "default": {}, "description": "Free-form session-specific metadata."},
            "created_at": {"type": "string", "format": "date-time", "description": "ISO 8601 timestamp of record creation."},
        },
        "allOf": [
            {
                "if": {
                    "properties": {"holiday_flag": {"const": True}},
                    "required": ["holiday_flag"],
                },
                "then": {
                    "properties": {
                        "session_open": {"pattern": f"^$|{TIME_PATTERN}"},
                        "session_close": {"pattern": f"^$|{TIME_PATTERN}"},
                    }
                },
                "else": {
                    "properties": {
                        "session_open": {"pattern": TIME_PATTERN},
                        "session_close": {"pattern": TIME_PATTERN},
                    }
                },
            }
        ],
    },
    "raw_dataset": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "RawDataset",
        "description": "Ingested dataset before any normalization.",
        "type": "object",
        "required": [
            "dataset_id", "source_class", "market", "instrument_scope",
            "coverage_start", "coverage_end", "ingest_time",
            "storage_ref", "checksum",
        ],
        "properties": {
            "dataset_id": {"type": "string", "description": "Globally unique dataset identifier."},
            "source_class": {"type": "string", "enum": SOURCE_CLASSES, "description": "Data source classification."},
            "market": {"type": "string", "description": "Market code (e.g., US, TW, CRYPTO)."},
            "instrument_scope": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "List of security_id or contract_id values this dataset covers."},
            "coverage_start": {"type": "string", "format": "date", "description": "Earliest data point date in the dataset."},
            "coverage_end": {"type": "string", "format": "date", "description": "Latest data point date in the dataset."},
            "ingest_time": {"type": "string", "format": "date-time", "description": "ISO 8601 timestamp when this dataset was ingested."},
            "storage_ref": {"type": "string", "description": "Reference to storage location (e.g., GCS URI, object store path)."},
            "checksum": {"type": "string", "description": "SHA-256 checksum of the raw data file(s)."},
            "metadata_json": {"type": "object", "default": {}, "description": "Free-form ingestion metadata."},
            "created_at": {"type": "string", "format": "date-time", "description": "ISO 8601 timestamp of record creation."},
        },
    },
    "normalized_dataset": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "NormalizedDataset",
        "description": "Dataset after normalization and symbol mapping.",
        "type": "object",
        "required": [
            "dataset_id", "parent_raw_dataset_id", "normalization_version",
            "available_time_policy", "storage_ref", "checksum",
        ],
        "properties": {
            "dataset_id": {"type": "string", "description": "Globally unique dataset identifier."},
            "parent_raw_dataset_id": {"type": "string", "description": "Reference to the source RawDataset dataset_id."},
            "normalization_version": {"type": "string", "description": "Version string for this normalization pass."},
            "symbol_mapping_version": {"type": ["string", "null"], "description": "Version of the symbol mapping used."},
            "corp_action_version": {"type": ["string", "null"], "description": "Version of corporate action adjustments applied."},
            "calendar_version": {"type": ["string", "null"], "description": "Version of the market calendar used."},
            "available_time_policy": {"type": "string", "enum": AVAILABLE_TIME_POLICIES, "description": "How available_time is determined."},
            "storage_ref": {"type": "string", "description": "Reference to storage location."},
            "checksum": {"type": "string", "description": "SHA-256 checksum of the normalized data file(s)."},
            "metadata_json": {"type": "object", "default": {}, "description": "Free-form normalization metadata."},
            "created_at": {"type": "string", "format": "date-time", "description": "ISO 8601 timestamp of record creation."},
        },
    },
    "feature_dataset": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "FeatureDataset",
        "description": "Feature-engineered dataset ready for research or training.",
        "type": "object",
        "required": [
            "dataset_id", "parent_normalized_dataset_id",
            "feature_spec_version", "label_spec_version",
            "point_in_time_rule", "storage_ref", "checksum",
        ],
        "properties": {
            "dataset_id": {"type": "string", "description": "Globally unique dataset identifier."},
            "parent_normalized_dataset_id": {"type": "string", "description": "Reference to the source NormalizedDataset dataset_id."},
            "feature_spec_version": {"type": "string", "description": "Version of the feature specification used."},
            "label_spec_version": {"type": "string", "description": "Version of the label specification used."},
            "point_in_time_rule": {"type": "string", "description": "Rule description ensuring no look-ahead bias (e.g., available_time <= event_time + 0d)."},
            "storage_ref": {"type": "string", "description": "Reference to storage location."},
            "checksum": {"type": "string", "description": "SHA-256 checksum of the feature data file(s)."},
            "metadata_json": {"type": "object", "default": {}, "description": "Free-form feature engineering metadata."},
            "created_at": {"type": "string", "format": "date-time", "description": "ISO 8601 timestamp of record creation."},
        },
    },
    "dataset_version": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "DatasetVersion",
        "description": "Frozen lineage snapshot across raw, normalized, and feature datasets. This is the unit of replay.",
        "type": "object",
        "required": [
            "dataset_version_id", "market_scope", "instrument_scope",
            "raw_dataset_refs", "normalized_dataset_refs", "feature_dataset_refs",
        ],
        "properties": {
            "dataset_version_id": {"type": "string", "description": "Globally unique version identifier."},
            "market_scope": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "List of market codes covered."},
            "instrument_scope": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "List of security_id or contract_id values covered."},
            "raw_dataset_refs": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "List of RawDataset dataset_id values."},
            "normalized_dataset_refs": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "List of NormalizedDataset dataset_id values."},
            "feature_dataset_refs": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "List of FeatureDataset dataset_id values."},
            "frozen_at": {"type": ["string", "null"], "format": "date-time", "description": "ISO 8601 timestamp when this version was frozen."},
            "metadata_json": {"type": "object", "default": {}, "description": "Free-form version metadata."},
            "created_at": {"type": "string", "format": "date-time", "description": "ISO 8601 timestamp of record creation."},
        },
    },
}


def write_schemas(output_dir: str) -> None:
    """Write all JSON schema files to the output directory."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    for name, schema in SCHEMAS.items():
        path = out / f"{name}.schema.json"
        path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {path}")


if __name__ == "__main__":
    write_schemas(str(Path(__file__).parent.parent / "schemas"))

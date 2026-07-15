"""Fail-closed authority contract for persona-teaching evaluation inputs.

This module deliberately has no environment-variable or wall-clock defaults.  A
caller must name both authority files, provide a trusted UTC clock, and identify
the strategy/candidate whose vectorbt input is being prepared.

``normalized_storage_refs`` are intentionally narrower than the general source
ingestion storage manifest: every ref must point to a frozen canonical OHLCV
JSONL file whose lines are the seven record fields accepted below.  Source
wrappers containing ``metadata`` or other transport envelopes are not an
evaluation authority and must be materialized into this DatasetVersion format
first.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlparse

from jsonschema import Draft7Validator, FormatChecker


class AuthorityValidationError(ValueError):
    """Raised when evaluation authority inputs cannot be admitted safely."""


_DATASET_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "data-plane"
    / "schemas"
    / "dataset_version.schema.json"
)
_DATASET_REQUIRED_FIELDS = frozenset(
    {
        "dataset_version_id",
        "market_scope",
        "instrument_scope",
        "raw_dataset_refs",
        "normalized_dataset_refs",
        "feature_dataset_refs",
        "frozen_at",
        "metadata_json",
        "created_at",
        "source_controller_readback_ref",
        "source_evidence_bundle_ids",
        "normalized_storage_refs",
        "records",
    }
)
_DATASET_ALLOWED_FIELDS = _DATASET_REQUIRED_FIELDS
_RECORD_FIELDS = frozenset({"instrument", "date", "open", "high", "low", "close", "volume"})
_STORAGE_REF_FIELDS = frozenset({"uri", "sha256", "row_count"})
_POLICY_FIELDS = frozenset(
    {
        "policy_id",
        "policy_version",
        "status",
        "approval_decision_ref",
        "effective_from",
        "effective_until",
        "required_backend",
        "proof_ttl_seconds",
        "max_future_skew_seconds",
        "max_staleness_days",
        "min_bars_per_instrument",
        "min_instruments",
        "required_instruments",
        "min_sharpe_ratio",
        "min_total_return",
        "max_drawdown",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
_MIN_VECTORBT_BARS = 30
_MIN_VECTORBT_INSTRUMENTS = 2
_UTC = timezone.utc


@dataclass(frozen=True)
class EvaluationAuthoritySnapshot:
    """Validated, digest-bound inputs admitted to the evaluation backend."""

    evaluated_at: str
    expires_at: str
    dataset_version_id: str
    dataset_digest: str
    policy_id: str
    policy_version: str
    policy_digest: str
    approval_decision_ref: str
    effective_from: str
    effective_until: str
    required_backend: str
    proof_ttl_seconds: int
    authority_refs: Mapping[str, Any]
    thresholds: Mapping[str, Any]
    gates: tuple[Mapping[str, Any], ...]
    vectorbt_dataset: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable copy suitable for an evaluation proof."""

        return {
            "status": "admitted",
            "evaluated_at": self.evaluated_at,
            "expires_at": self.expires_at,
            "dataset": {
                "dataset_version_id": self.dataset_version_id,
                "digest": self.dataset_digest,
                **copy.deepcopy(dict(self.authority_refs)),
            },
            "policy": {
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "digest": self.policy_digest,
                "status": "active",
                "approval_decision_ref": self.approval_decision_ref,
                "effective_from": self.effective_from,
                "effective_until": self.effective_until,
                "required_backend": self.required_backend,
                "proof_ttl_seconds": self.proof_ttl_seconds,
            },
            "thresholds": copy.deepcopy(dict(self.thresholds)),
            "gates": [copy.deepcopy(dict(gate)) for gate in self.gates],
            "vectorbt_dataset": copy.deepcopy(dict(self.vectorbt_dataset)),
        }


def stable_json_sha256(payload: Any) -> str:
    """Return a stable lowercase SHA-256 hex digest for a JSON value."""

    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AuthorityValidationError("payload is not finite canonical JSON") from exc
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_evaluation_authority(
    dataset_path: str | Path,
    policy_path: str | Path,
    *,
    trusted_now: datetime,
    strategy_id: str,
    dataset_schema_path: str | Path | None = None,
    authority_root: str | Path | None = None,
) -> EvaluationAuthoritySnapshot:
    """Load and admit versioned dataset and threshold-policy authorities.

    ``trusted_now`` must come from the service clock.  In particular, request
    timestamps must not be forwarded here as a freshness clock.
    """

    now = _trusted_utc(trusted_now)
    normalized_strategy_id = _required_text(strategy_id, "strategy_id")
    dataset_authority_path = Path(dataset_path)
    dataset = _load_json_object(dataset_authority_path, "dataset")
    policy = _load_json_object(policy_path, "policy")
    normalized_policy = _validate_policy(policy, now)
    normalized_authority_root = _resolve_authority_root(dataset_authority_path, authority_root)
    normalized_dataset = _validate_dataset(
        dataset,
        normalized_policy,
        now,
        Path(dataset_schema_path) if dataset_schema_path is not None else _DATASET_SCHEMA_PATH,
        normalized_authority_root,
    )

    thresholds = {
        key: normalized_policy[key]
        for key in (
            "max_staleness_days",
            "max_future_skew_seconds",
            "min_bars_per_instrument",
            "min_instruments",
            "required_instruments",
            "min_sharpe_ratio",
            "min_total_return",
            "max_drawdown",
        )
    }
    evaluated_at = _isoformat(now)
    expires_at = _isoformat(now + timedelta(seconds=normalized_policy["proof_ttl_seconds"]))
    authority_refs = {
        "frozen_at": normalized_dataset["frozen_at"],
        "created_at": normalized_dataset["created_at"],
        "instrument_scope": list(normalized_dataset["instrument_scope"]),
        "source_controller_readback_ref": normalized_dataset["source_controller_readback_ref"],
        "source_evidence_bundle_ids": list(normalized_dataset["source_evidence_bundle_ids"]),
        "normalized_storage_refs": copy.deepcopy(normalized_dataset["normalized_storage_refs"]),
        "bars_per_instrument": dict(normalized_dataset["bars_per_instrument"]),
        "latest_bar_at_by_instrument": dict(normalized_dataset["latest_bar_at_by_instrument"]),
    }
    gates: tuple[Mapping[str, Any], ...] = (
        {
            "gate_id": "dataset_version_schema",
            "state": "passed",
            "dataset_version_id": normalized_dataset["dataset_version_id"],
        },
        {
            "gate_id": "dataset_authority_lineage",
            "state": "passed",
            "authority_status": "authoritative",
            "source_controller_readback_ref": normalized_dataset["source_controller_readback_ref"],
        },
        {
            "gate_id": "threshold_policy_active",
            "state": "passed",
            "policy_id": normalized_policy["policy_id"],
            "policy_version": normalized_policy["policy_version"],
        },
        {
            "gate_id": "required_instrument_scope",
            "state": "passed",
            "required_instruments": list(normalized_policy["required_instruments"]),
        },
        {
            "gate_id": "ohlcv_quality",
            "state": "passed",
            "record_count": len(normalized_dataset["records"]),
        },
        {
            "gate_id": "per_instrument_freshness",
            "state": "passed",
            "latest_bar_at_by_instrument": dict(normalized_dataset["latest_bar_at_by_instrument"]),
        },
    )
    vectorbt_dataset = {
        "dataset_id": normalized_dataset["dataset_version_id"],
        "strategy_id": normalized_strategy_id,
        "source_dataset_refs": list(normalized_dataset["normalized_dataset_refs"]),
        "data_frequency": "daily",
        "records": copy.deepcopy(normalized_dataset["records"]),
    }
    return EvaluationAuthoritySnapshot(
        evaluated_at=evaluated_at,
        expires_at=expires_at,
        dataset_version_id=normalized_dataset["dataset_version_id"],
        dataset_digest=stable_json_sha256(dataset),
        policy_id=normalized_policy["policy_id"],
        policy_version=normalized_policy["policy_version"],
        policy_digest=stable_json_sha256(policy),
        approval_decision_ref=normalized_policy["approval_decision_ref"],
        effective_from=normalized_policy["effective_from"],
        effective_until=normalized_policy["effective_until"],
        required_backend=normalized_policy["required_backend"],
        proof_ttl_seconds=normalized_policy["proof_ttl_seconds"],
        authority_refs=authority_refs,
        thresholds=thresholds,
        gates=gates,
        vectorbt_dataset=vectorbt_dataset,
    )


def _load_json_object(path_value: str | Path, label: str) -> dict[str, Any]:
    path = Path(path_value)
    try:
        text = path.read_text(encoding="utf-8")
        payload = _strict_json_loads(text, label)
    except AuthorityValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthorityValidationError(f"{label} authority is unreadable JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise AuthorityValidationError(f"{label} authority must be a JSON object")
    return payload


def _validate_policy(policy: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    _require_exact_fields(policy, _POLICY_FIELDS, _POLICY_FIELDS, "policy")
    normalized: dict[str, Any] = {
        "policy_id": _required_text(policy["policy_id"], "policy.policy_id"),
        "policy_version": _required_text(policy["policy_version"], "policy.policy_version"),
        "approval_decision_ref": _required_text(
            policy["approval_decision_ref"], "policy.approval_decision_ref"
        ),
    }
    if policy["status"] != "active":
        raise AuthorityValidationError("policy.status must equal 'active'")
    normalized["status"] = "active"
    if policy["required_backend"] != "vectorbt_portfolio":
        raise AuthorityValidationError("policy.required_backend must equal 'vectorbt_portfolio'")
    normalized["required_backend"] = "vectorbt_portfolio"

    effective_from = _parse_datetime(policy["effective_from"], "policy.effective_from")
    effective_until = _parse_datetime(policy["effective_until"], "policy.effective_until")
    if effective_from >= effective_until:
        raise AuthorityValidationError("policy effective_from must be before effective_until")
    if now < effective_from:
        raise AuthorityValidationError("policy is not yet effective at trusted_now")
    if now >= effective_until:
        raise AuthorityValidationError("policy is expired at trusted_now")
    normalized["effective_from"] = _isoformat(effective_from)
    normalized["effective_until"] = _isoformat(effective_until)

    normalized["proof_ttl_seconds"] = _strict_int(
        policy["proof_ttl_seconds"], "policy.proof_ttl_seconds", minimum=1
    )
    normalized["max_future_skew_seconds"] = _strict_int(
        policy["max_future_skew_seconds"], "policy.max_future_skew_seconds", minimum=0
    )
    normalized["max_staleness_days"] = _strict_int(
        policy["max_staleness_days"], "policy.max_staleness_days", minimum=0
    )
    normalized["min_bars_per_instrument"] = _strict_int(
        policy["min_bars_per_instrument"],
        "policy.min_bars_per_instrument",
        minimum=_MIN_VECTORBT_BARS,
    )
    normalized["min_instruments"] = _strict_int(
        policy["min_instruments"],
        "policy.min_instruments",
        minimum=_MIN_VECTORBT_INSTRUMENTS,
    )
    required_instruments = _text_list(
        policy["required_instruments"], "policy.required_instruments", require_unique=True
    )
    if len(required_instruments) < normalized["min_instruments"]:
        raise AuthorityValidationError(
            "policy.required_instruments must contain at least policy.min_instruments entries"
        )
    normalized["required_instruments"] = required_instruments
    normalized["min_sharpe_ratio"] = _finite_number(
        policy["min_sharpe_ratio"], "policy.min_sharpe_ratio"
    )
    normalized["min_total_return"] = _finite_number(
        policy["min_total_return"], "policy.min_total_return"
    )
    if normalized["min_total_return"] < -1.0:
        raise AuthorityValidationError("policy.min_total_return must be greater than or equal to -1")
    normalized["max_drawdown"] = _finite_number(policy["max_drawdown"], "policy.max_drawdown")
    if not 0.0 <= normalized["max_drawdown"] <= 1.0:
        raise AuthorityValidationError("policy.max_drawdown must be between 0 and 1")
    return normalized


def _validate_dataset(
    dataset: Mapping[str, Any],
    policy: Mapping[str, Any],
    now: datetime,
    schema_path: Path,
    authority_root: Path,
) -> dict[str, Any]:
    _require_exact_fields(dataset, _DATASET_REQUIRED_FIELDS, _DATASET_ALLOWED_FIELDS, "dataset")
    schema = _load_json_object(schema_path, "DatasetVersion schema")
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(dict(dataset)), key=lambda error: repr(list(error.path)))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<root>"
        raise AuthorityValidationError(
            f"dataset DatasetVersion schema validation failed at {location}: {first.message}"
        )

    normalized: dict[str, Any] = {
        "dataset_version_id": _required_text(
            dataset["dataset_version_id"], "dataset.dataset_version_id"
        ),
        "market_scope": _text_list(dataset["market_scope"], "dataset.market_scope", require_unique=True),
        "instrument_scope": _text_list(
            dataset["instrument_scope"], "dataset.instrument_scope", require_unique=True
        ),
        "raw_dataset_refs": _text_list(
            dataset["raw_dataset_refs"], "dataset.raw_dataset_refs", require_unique=True
        ),
        "normalized_dataset_refs": _text_list(
            dataset["normalized_dataset_refs"],
            "dataset.normalized_dataset_refs",
            require_unique=True,
        ),
        "feature_dataset_refs": _text_list(
            dataset["feature_dataset_refs"], "dataset.feature_dataset_refs", require_unique=True
        ),
        "source_controller_readback_ref": _required_text(
            dataset["source_controller_readback_ref"],
            "dataset.source_controller_readback_ref",
        ),
        "source_evidence_bundle_ids": _text_list(
            dataset["source_evidence_bundle_ids"],
            "dataset.source_evidence_bundle_ids",
            require_unique=True,
        ),
    }
    metadata = dataset["metadata_json"]
    if not isinstance(metadata, Mapping):
        raise AuthorityValidationError("dataset.metadata_json must be an object")
    if metadata.get("authority_status") != "authoritative":
        raise AuthorityValidationError(
            "dataset.metadata_json.authority_status must equal 'authoritative'"
        )

    created_at = _parse_datetime(dataset["created_at"], "dataset.created_at")
    frozen_at = _parse_datetime(dataset["frozen_at"], "dataset.frozen_at")
    if created_at > frozen_at:
        raise AuthorityValidationError("dataset.created_at must not be after dataset.frozen_at")
    future_limit = now + timedelta(seconds=policy["max_future_skew_seconds"])
    if frozen_at > future_limit:
        raise AuthorityValidationError("dataset.frozen_at is beyond the permitted future skew")
    normalized["created_at"] = _isoformat(created_at)
    normalized["frozen_at"] = _isoformat(frozen_at)

    normalized_storage_refs, storage_records = _validate_storage_refs(
        dataset["normalized_storage_refs"], authority_root
    )
    normalized["normalized_storage_refs"] = normalized_storage_refs

    instrument_scope = set(normalized["instrument_scope"])
    records = _canonicalize_records(
        dataset["records"],
        label="dataset.records",
        instrument_scope=instrument_scope,
        future_limit=future_limit,
        frozen_at=frozen_at,
        max_future_skew_seconds=policy["max_future_skew_seconds"],
    )
    canonical_storage_records = _canonicalize_records(
        storage_records,
        label="dataset.normalized_storage_refs canonical JSONL",
        instrument_scope=instrument_scope,
        future_limit=future_limit,
        frozen_at=frozen_at,
        max_future_skew_seconds=policy["max_future_skew_seconds"],
    )
    if canonical_storage_records != records:
        raise AuthorityValidationError(
            "dataset.normalized_storage_refs canonical OHLCV records must exactly match dataset.records"
        )

    bars_per_instrument: dict[str, int] = {}
    latest_by_instrument: dict[str, datetime] = {}
    for record in records:
        instrument = str(record["instrument"])
        _, bar_at = _parse_record_date(record["date"], "canonical record date")
        bars_per_instrument[instrument] = bars_per_instrument.get(instrument, 0) + 1
        latest_by_instrument[instrument] = max(latest_by_instrument.get(instrument, bar_at), bar_at)

    observed_instruments = set(bars_per_instrument)
    if observed_instruments != instrument_scope:
        missing = sorted(instrument_scope - observed_instruments)
        raise AuthorityValidationError(
            f"dataset.instrument_scope has instruments without records: {', '.join(missing)}"
        )
    if len(observed_instruments) < policy["min_instruments"]:
        raise AuthorityValidationError(
            "dataset has insufficient instruments: "
            f"required {policy['min_instruments']}, got {len(observed_instruments)}"
        )
    required_instruments = set(policy["required_instruments"])
    missing_required_scope = sorted(required_instruments - instrument_scope)
    if missing_required_scope:
        raise AuthorityValidationError(
            "dataset.instrument_scope is missing required instruments: "
            + ", ".join(missing_required_scope)
        )
    max_age = timedelta(days=policy["max_staleness_days"])
    latest_bar_at_by_instrument: dict[str, str] = {}
    for instrument in normalized["instrument_scope"]:
        count = bars_per_instrument.get(instrument, 0)
        if count < policy["min_bars_per_instrument"]:
            raise AuthorityValidationError(
                f"dataset instrument {instrument} has {count} bars; "
                f"minimum is {policy['min_bars_per_instrument']}"
            )
        latest = latest_by_instrument[instrument]
        if now - latest > max_age:
            raise AuthorityValidationError(
                f"dataset instrument {instrument} is stale at trusted_now; "
                f"latest bar is {_isoformat(latest)}"
            )
        latest_bar_at_by_instrument[instrument] = _isoformat(latest)

    normalized["records"] = records
    normalized["bars_per_instrument"] = dict(sorted(bars_per_instrument.items()))
    normalized["latest_bar_at_by_instrument"] = latest_bar_at_by_instrument
    return normalized


def _validate_storage_refs(
    value: Any,
    authority_root: Path,
) -> tuple[list[dict[str, Any]], list[Mapping[str, Any]]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise AuthorityValidationError("dataset.normalized_storage_refs must be a non-empty array")
    refs: list[dict[str, Any]] = []
    records: list[Mapping[str, Any]] = []
    seen_uris: set[str] = set()
    for index, item in enumerate(value):
        label = f"dataset.normalized_storage_refs[{index}]"
        if not isinstance(item, Mapping):
            raise AuthorityValidationError(f"{label} must be an object")
        _require_exact_fields(item, _STORAGE_REF_FIELDS, _STORAGE_REF_FIELDS, label)
        uri = _required_text(item["uri"], f"{label}.uri")
        if uri in seen_uris:
            raise AuthorityValidationError(f"dataset.normalized_storage_refs has duplicate uri: {uri}")
        seen_uris.add(uri)
        checksum = _required_text(item["sha256"], f"{label}.sha256")
        if not _SHA256_PATTERN.fullmatch(checksum):
            raise AuthorityValidationError(f"{label}.sha256 must be 64 lowercase hexadecimal characters")
        row_count = _strict_int(item["row_count"], f"{label}.row_count", minimum=1)
        path = _resolve_storage_path(uri, authority_root, label)
        try:
            raw_bytes = path.read_bytes()
        except OSError as exc:
            raise AuthorityValidationError(f"{label}.uri cannot be read: {path}") from exc
        actual_checksum = hashlib.sha256(raw_bytes).hexdigest()
        if actual_checksum != checksum:
            raise AuthorityValidationError(f"{label}.sha256 does not match authority file bytes")
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AuthorityValidationError(f"{label}.uri must contain UTF-8 canonical OHLCV JSONL") from exc
        lines = text.splitlines()
        if not lines:
            raise AuthorityValidationError(f"{label}.uri must contain canonical OHLCV JSONL rows")
        parsed_rows: list[Mapping[str, Any]] = []
        for line_number, line in enumerate(lines, start=1):
            row_label = f"{label}.uri line {line_number}"
            if not line.strip():
                raise AuthorityValidationError(f"{row_label} must not be blank")
            payload = _strict_json_loads(line, row_label)
            if not isinstance(payload, Mapping):
                raise AuthorityValidationError(f"{row_label} must be a canonical OHLCV JSON object")
            parsed_rows.append(payload)
        if len(parsed_rows) != row_count:
            raise AuthorityValidationError(
                f"{label}.row_count does not match authority file rows: "
                f"declared {row_count}, got {len(parsed_rows)}"
            )
        refs.append({"uri": uri, "sha256": checksum, "row_count": row_count})
        records.extend(parsed_rows)
    return refs, records


def _strict_json_loads(text: str, label: str) -> Any:
    def reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AuthorityValidationError(f"{label} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_nonfinite_constant(value: str) -> None:
        raise AuthorityValidationError(f"{label} contains non-finite JSON constant: {value}")

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite_constant,
        )
    except AuthorityValidationError:
        raise
    except json.JSONDecodeError as exc:
        raise AuthorityValidationError(f"{label} is invalid JSON") from exc


def _resolve_authority_root(
    dataset_path: Path,
    configured_root: str | Path | None,
) -> Path:
    root_input = Path(configured_root) if configured_root is not None else dataset_path.parent
    if root_input.is_symlink():
        raise AuthorityValidationError("authority_root must not be a symlink")
    try:
        root = root_input.resolve(strict=True)
    except OSError as exc:
        raise AuthorityValidationError(f"authority_root does not exist: {root_input}") from exc
    if not root.is_dir():
        raise AuthorityValidationError(f"authority_root must be a directory: {root}")
    return root


def _resolve_storage_path(uri: str, authority_root: Path, label: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme:
        if parsed.scheme != "file":
            raise AuthorityValidationError(
                f"{label}.uri must be an absolute local path or file:// URI"
            )
        if parsed.netloc not in ("", "localhost") or parsed.query or parsed.fragment:
            raise AuthorityValidationError(f"{label}.uri is not an unambiguous local file URI")
        path = Path(unquote(parsed.path))
    else:
        path = Path(uri)
    if not path.is_absolute():
        raise AuthorityValidationError(f"{label}.uri must be an absolute local file path")

    try:
        lexical_relative = path.relative_to(authority_root)
    except ValueError as exc:
        raise AuthorityValidationError(f"{label}.uri escapes authority_root") from exc
    if any(part in (".", "..") for part in lexical_relative.parts):
        raise AuthorityValidationError(f"{label}.uri must not contain path traversal")
    current = authority_root
    for part in lexical_relative.parts:
        current = current / part
        if current.is_symlink():
            raise AuthorityValidationError(f"{label}.uri must not traverse a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AuthorityValidationError(f"{label}.uri does not exist: {path}") from exc
    try:
        resolved.relative_to(authority_root)
    except ValueError as exc:
        raise AuthorityValidationError(f"{label}.uri escapes authority_root") from exc
    if not resolved.is_file():
        raise AuthorityValidationError(f"{label}.uri must reference a regular file")
    return resolved


def _canonicalize_records(
    records_value: Any,
    *,
    label: str,
    instrument_scope: set[str],
    future_limit: datetime,
    frozen_at: datetime,
    max_future_skew_seconds: int,
) -> list[dict[str, Any]]:
    if (
        isinstance(records_value, (str, bytes))
        or not isinstance(records_value, Sequence)
        or not records_value
    ):
        raise AuthorityValidationError(f"{label} must be a non-empty array")
    records: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    frozen_limit = frozen_at + timedelta(seconds=max_future_skew_seconds)
    for index, raw_record in enumerate(records_value):
        record_label = f"{label}[{index}]"
        if not isinstance(raw_record, Mapping):
            raise AuthorityValidationError(f"{record_label} must be an object")
        _require_exact_fields(raw_record, _RECORD_FIELDS, _RECORD_FIELDS, record_label)
        instrument = _required_text(raw_record["instrument"], f"{record_label}.instrument")
        if instrument not in instrument_scope:
            raise AuthorityValidationError(
                f"{record_label}.instrument {instrument!r} is outside dataset.instrument_scope"
            )
        date_text, bar_at = _parse_record_date(raw_record["date"], f"{record_label}.date")
        key = (instrument, date_text)
        if key in seen_keys:
            raise AuthorityValidationError(
                f"{label} contains duplicate instrument/date: {instrument}/{date_text}"
            )
        seen_keys.add(key)
        if bar_at > future_limit:
            raise AuthorityValidationError(
                f"{label} contains future bar beyond permitted skew: {instrument}/{date_text}"
            )
        if bar_at > frozen_limit:
            raise AuthorityValidationError(
                f"{label} contains bar after dataset.frozen_at: {instrument}/{date_text}"
            )
        values = {
            field: _finite_number(raw_record[field], f"{record_label}.{field}")
            for field in _OHLCV_FIELDS
        }
        if any(values[field] <= 0 for field in ("open", "high", "low", "close")):
            raise AuthorityValidationError(f"{record_label} OHLC prices must be greater than zero")
        if values["volume"] < 0:
            raise AuthorityValidationError(f"{record_label}.volume must be non-negative")
        if values["high"] < max(values["open"], values["low"], values["close"]):
            raise AuthorityValidationError(f"{record_label}.high is inconsistent with OHLC values")
        if values["low"] > min(values["open"], values["high"], values["close"]):
            raise AuthorityValidationError(f"{record_label}.low is inconsistent with OHLC values")
        records.append({"instrument": instrument, "date": date_text, **values})
    return sorted(records, key=lambda record: (record["date"], record["instrument"]))


def _require_exact_fields(
    payload: Mapping[str, Any],
    required: frozenset[str],
    allowed: frozenset[str],
    label: str,
) -> None:
    missing = sorted(required - set(payload))
    if missing:
        raise AuthorityValidationError(f"{label} missing required fields: {', '.join(missing)}")
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise AuthorityValidationError(f"{label} contains unknown fields: {', '.join(unknown)}")


def _trusted_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AuthorityValidationError("trusted_now must be a timezone-aware datetime")
    return value.astimezone(_UTC)


def _parse_datetime(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AuthorityValidationError(f"{label} must be a timezone-aware ISO 8601 timestamp")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise AuthorityValidationError(f"{label} must be a timezone-aware ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AuthorityValidationError(f"{label} must be a timezone-aware ISO 8601 timestamp")
    return parsed.astimezone(_UTC)


def _parse_record_date(value: Any, label: str) -> tuple[str, datetime]:
    if not isinstance(value, str) or not _ISO_DATE_PATTERN.fullmatch(value):
        raise AuthorityValidationError(f"{label} must use YYYY-MM-DD")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=_UTC)
    except ValueError as exc:
        raise AuthorityValidationError(f"{label} must use YYYY-MM-DD") from exc
    if parsed.strftime("%Y-%m-%d") != value:
        raise AuthorityValidationError(f"{label} must use YYYY-MM-DD")
    return value, parsed


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuthorityValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _text_list(value: Any, label: str, *, require_unique: bool) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise AuthorityValidationError(f"{label} must be a non-empty array")
    values = [_required_text(item, f"{label}[]") for item in value]
    if require_unique and len(set(values)) != len(values):
        raise AuthorityValidationError(f"{label} must not contain duplicates")
    return values


def _strict_int(value: Any, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AuthorityValidationError(f"{label} must be an integer")
    if value < minimum:
        raise AuthorityValidationError(f"{label} must be at least {minimum}")
    return value


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuthorityValidationError(f"{label} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise AuthorityValidationError(f"{label} must be finite")
    return normalized


def _isoformat(value: datetime) -> str:
    return value.astimezone(_UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "AuthorityValidationError",
    "EvaluationAuthoritySnapshot",
    "load_evaluation_authority",
    "stable_json_sha256",
]

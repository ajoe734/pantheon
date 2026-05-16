"""Governed Qlib data handler and LightGBM alpha adapter for Pantheon.

Governance boundary:
- Input: Pantheon-governed OHLCV dataset with lineage refs
- Output: registry-ready model_artifact refs (artifact_state=draft) + registry_entry
- Qlib or its dependencies never write directly to registry, runtime, or LEAN.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

QLIB_VERSION_PIN = "0.9.6"
PRIMARY_BACKEND = "qlib_lgbm"
STUB_BACKEND = "stub_lgbm"
REQUIRED_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
MIN_INSTRUMENTS = 2  # smoke-test floor; production requires >=50 (ACTIVATION_CRITERIA §1)
MIN_PERIODS = 5  # smoke-test floor; production requires 2+ years daily
ACTIVATION_READY_MIN_INSTRUMENTS = 50
ACTIVATION_READY_MIN_HISTORY_YEARS = 2.0
ACTIVATION_READY_MIN_DAILY_PERIODS = 504
ACTIVATION_READY_ENABLE_ENV_VAR = "PANTHEON_QLIB_ACTIVATION_READY_ENABLED"
TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


class QlibWorkflowError(ValueError):
    """Raised when a governed Qlib workflow cannot be built safely."""


class ActivationReadyGate:
    """Explicit non-default gate for repo-local Qlib activation-ready entrypoints."""

    @classmethod
    def require_cli_flag(cls, enabled: bool, *, flag_name: str = "--enable-activation-ready") -> None:
        if enabled:
            return
        raise EnvironmentError(
            "Qlib activation-ready execution is disabled by default. "
            f"Re-run with {flag_name} to execute this offline gated path."
        )

    @classmethod
    def require_env(cls, *, env_var: str = ACTIVATION_READY_ENABLE_ENV_VAR) -> None:
        raw = os.getenv(env_var, "")
        if str(raw).strip().lower() in TRUE_ENV_VALUES:
            return
        raise EnvironmentError(
            "Qlib activation-ready execution is disabled by default. "
            f"Set {env_var}=1 to run this offline gated worker."
        )


@dataclass(frozen=True)
class PreparedQlibDataset:
    dataset_id: str
    strategy_id: str
    source_dataset_refs: tuple[str, ...]
    source_strategy_spec_id: str | None
    instruments: tuple[str, ...]
    periods: tuple[str, ...]
    feature_matrix: tuple[tuple[float, ...], ...]  # shape: (num_samples, num_features)
    labels: tuple[float, ...]  # forward-return labels
    feature_names: tuple[str, ...]
    data_frequency: str
    num_instruments: int
    num_periods: int
    min_periods_per_instrument: int
    history_start: str | None = None
    history_end: str | None = None
    history_years: float = 0.0
    min_instrument_history_years: float = 0.0

    @property
    def num_samples(self) -> int:
        return len(self.labels)

    @property
    def num_features(self) -> int:
        return len(self.feature_names)

    def dataset_summary(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "strategy_id": self.strategy_id,
            "source_dataset_refs": list(self.source_dataset_refs),
            "source_strategy_spec_id": self.source_strategy_spec_id,
            "num_instruments": self.num_instruments,
            "num_periods": self.num_periods,
            "num_samples": self.num_samples,
            "num_features": self.num_features,
            "feature_names": list(self.feature_names),
            "data_frequency": self.data_frequency,
            "instruments": list(self.instruments),
            "min_periods_per_instrument": self.min_periods_per_instrument,
            "history_start": self.history_start,
            "history_end": self.history_end,
            "history_years": self.history_years,
            "min_instrument_history_years": self.min_instrument_history_years,
        }


@dataclass(frozen=True)
class TrainingConfig:
    version: str = "1.0.0"
    requested_by: str = "Claude"
    seed: int = 42
    n_estimators: int = 10  # reduced for smoke tests; production uses 200
    num_leaves: int = 7
    max_depth: int = 3
    learning_rate: float = 0.05
    storage_backend: str = "object_store"
    storage_path_template: str = "research/qlib/{strategy_id}/{version}/artifact.bin"


@dataclass(frozen=True)
class BackendTrainingResult:
    backend: str
    run_id: str
    model_payload: dict[str, Any]
    metrics: dict[str, Any]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class QlibRunResult:
    prepared_dataset: PreparedQlibDataset
    training_result: BackendTrainingResult
    artifact_bundle: dict[str, Any]
    registry_entry: dict[str, Any]
    candidate_packet: dict[str, Any]
    artifact_refs: dict[str, Any]


class LightGBMBackend(Protocol):
    def train(self, dataset: PreparedQlibDataset, config: TrainingConfig) -> BackendTrainingResult:
        ...


class GovernedQlibDataAdapter:
    """Validates governed OHLCV input and computes momentum/volatility features.

    Governance rules:
    - All input records must carry governed source_dataset_refs (no ad-hoc files).
    - Missing OHLCV fields are rejected.
    - Forward-return labels are computed internally; callers may not supply raw labels
      that bypass the feature-engineering step.
    """

    def prepare(self, dataset: Mapping[str, Any]) -> PreparedQlibDataset:
        dataset_id = self._req_str(dataset, "dataset_id")
        strategy_id = self._req_str(dataset, "strategy_id")
        source_dataset_refs = self._normalize_refs(dataset)
        source_strategy_spec_id = self._opt_str(dataset.get("source_strategy_spec_id"))
        data_frequency = self._opt_str(dataset.get("data_frequency")) or "daily"

        records = dataset.get("records")
        if not isinstance(records, Sequence) or not records:
            raise QlibWorkflowError("dataset.records must be a non-empty list of OHLCV dicts")

        # Index records by instrument then by date for feature construction
        instrument_series: dict[str, list[dict[str, Any]]] = {}
        for rec in records:
            if not isinstance(rec, Mapping):
                raise QlibWorkflowError("Each record must be a mapping")
            instrument = self._req_str(rec, "instrument")
            for field_name in REQUIRED_OHLCV_FIELDS:
                if not isinstance(rec.get(field_name), (int, float)):
                    raise QlibWorkflowError(
                        f"record for {instrument} missing numeric field '{field_name}'"
                    )
            instrument_series.setdefault(instrument, []).append(rec)

        instruments = tuple(sorted(instrument_series.keys()))
        if len(instruments) < MIN_INSTRUMENTS:
            raise QlibWorkflowError(
                f"dataset must have at least {MIN_INSTRUMENTS} instruments; got {len(instruments)}"
            )

        all_features: list[tuple[float, ...]] = []
        all_labels: list[float] = []
        all_periods: list[str] = []
        parsed_dates: list[date] = []
        instrument_history_years: list[float] = []
        periods_per_instrument: list[int] = []

        for instrument in instruments:
            series = sorted(instrument_series[instrument], key=lambda r: r.get("date", ""))
            if len(series) < MIN_PERIODS:
                raise QlibWorkflowError(
                    f"instrument {instrument} has fewer than {MIN_PERIODS} periods"
                )
            periods_per_instrument.append(len(series))
            instrument_dates = [
                parsed_date
                for parsed_date in (_parse_record_date(row.get("date")) for row in series)
                if parsed_date is not None
            ]
            parsed_dates.extend(instrument_dates)
            instrument_history_years.append(_history_years(instrument_dates))
            closes = [float(r["close"]) for r in series]
            volumes = [float(r["volume"]) for r in series]
            for i in range(2, len(series)):
                # Simple feature set: 2-day and multi-day momentum, volume change, volatility
                momentum_1 = (closes[i] - closes[i - 1]) / (closes[i - 1] + 1e-9)
                momentum_2 = (closes[i] - closes[i - 2]) / (closes[i - 2] + 1e-9)
                vol_change = (volumes[i] - volumes[i - 1]) / (volumes[i - 1] + 1e-9)
                volatility = abs(closes[i] - closes[i - 1]) / (closes[i - 1] + 1e-9)
                features = (momentum_1, momentum_2, vol_change, volatility)
                label = (closes[min(i + 1, len(closes) - 1)] - closes[i]) / (closes[i] + 1e-9)
                all_features.append(features)
                all_labels.append(label)
                all_periods.append(str(series[i].get("date", f"{instrument}-t{i}")))

        if not all_features:
            raise QlibWorkflowError("No usable samples after feature engineering")

        periods_unique = tuple(dict.fromkeys(all_periods))
        history_start = min(parsed_dates).isoformat() if parsed_dates else None
        history_end = max(parsed_dates).isoformat() if parsed_dates else None
        return PreparedQlibDataset(
            dataset_id=dataset_id,
            strategy_id=strategy_id,
            source_dataset_refs=tuple(source_dataset_refs),
            source_strategy_spec_id=source_strategy_spec_id,
            instruments=instruments,
            periods=periods_unique,
            feature_matrix=tuple(all_features),
            labels=tuple(all_labels),
            feature_names=("momentum_1d", "momentum_2d", "volume_change_1d", "volatility_1d"),
            data_frequency=data_frequency,
            num_instruments=len(instruments),
            num_periods=len(periods_unique),
            min_periods_per_instrument=min(periods_per_instrument) if periods_per_instrument else 0,
            history_start=history_start,
            history_end=history_end,
            history_years=round(_history_years(parsed_dates), 4),
            min_instrument_history_years=round(
                min(instrument_history_years) if instrument_history_years else 0.0,
                4,
            ),
        )

    def _normalize_refs(self, dataset: Mapping[str, Any]) -> list[str]:
        refs = dataset.get("source_dataset_refs")
        if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes)):
            result = [r for r in refs if isinstance(r, str) and r.strip()]
            if result:
                return result
        single = self._opt_str(dataset.get("source_dataset_ref"))
        if single:
            return [single]
        raise QlibWorkflowError("dataset must include source_dataset_ref or source_dataset_refs")

    def _req_str(self, payload: Mapping[str, Any], key: str) -> str:
        value = self._opt_str(payload.get(key))
        if not value:
            raise QlibWorkflowError(f"'{key}' must be a non-empty string")
        return value

    def _opt_str(self, value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise QlibWorkflowError(f"expected string, got {type(value).__name__}")
        return value.strip()


class StubLightGBMBackend:
    """Mean-prediction stub for deterministic CI and smoke tests.

    Computes feature-mean per sample and predicts the grand mean label.
    No external dependencies required.
    """

    def train(self, dataset: PreparedQlibDataset, config: TrainingConfig) -> BackendTrainingResult:
        run_id = f"qlib-stub-{uuid.uuid4().hex[:12]}"
        labels = dataset.labels
        mean_label = sum(labels) / len(labels) if labels else 0.0
        predictions = [mean_label] * len(labels)
        # IC (information coefficient) as correlation proxy
        ic = self._pearson_r(list(labels), predictions)
        mse = sum((p - a) ** 2 for p, a in zip(predictions, labels)) / max(len(labels), 1)
        metrics = {
            "num_samples": dataset.num_samples,
            "num_features": dataset.num_features,
            "num_instruments": dataset.num_instruments,
            "ic": round(ic, 6),
            "mse": round(mse, 9),
            "mean_label": round(mean_label, 8),
        }
        model_payload = {
            "predictor": "stub_mean",
            "grand_mean_label": mean_label,
            "feature_names": list(dataset.feature_names),
            "num_leaves": config.num_leaves,
            "n_estimators": config.n_estimators,
        }
        return BackendTrainingResult(
            backend=STUB_BACKEND,
            run_id=run_id,
            model_payload=model_payload,
            metrics=metrics,
            notes=("Stub backend is intended for governed smoke tests and packaging validation.",),
        )

    def _pearson_r(self, xs: list[float], ys: list[float]) -> float:
        n = len(xs)
        if n < 2:
            return 0.0
        mx = sum(xs) / n
        my = sum(ys) / n
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        dx = sum((x - mx) ** 2 for x in xs) ** 0.5
        dy = sum((y - my) ** 2 for y in ys) ** 0.5
        denom = dx * dy
        return num / denom if denom > 1e-12 else 0.0


class _QlibPreparedColumn:
    def __init__(self, values: Any, index: Sequence[int]) -> None:
        self.values = values
        self.index = tuple(index)


class _QlibPreparedFrame:
    def __init__(self, feature_values: Any, label_values: Any, index: Sequence[int]) -> None:
        self._feature = _QlibPreparedColumn(feature_values, index)
        self._label = _QlibPreparedColumn(label_values, index)
        self.empty = len(self._feature.index) == 0

    def __getitem__(self, key: str) -> _QlibPreparedColumn:
        if key == "feature":
            return self._feature
        if key == "label":
            return self._label
        raise KeyError(key)


class _QlibDatasetView:
    """Minimal dataset wrapper matching the interface `qlib.contrib.model.gbdt.LGBModel` expects."""

    def __init__(
        self,
        *,
        train_features: Any,
        train_labels: Any,
        valid_features: Any,
        valid_labels: Any,
    ) -> None:
        self.segments = {"train": None, "valid": None}
        self._frames = {
            "train": _QlibPreparedFrame(train_features, train_labels, range(len(train_labels))),
            "valid": _QlibPreparedFrame(valid_features, valid_labels, range(len(valid_labels))),
        }

    def prepare(self, segment: str, col_set: Any = None, data_key: Any = None) -> Any:
        del data_key
        frame = self._frames.get(segment)
        if frame is None:
            raise QlibWorkflowError(f"Unknown dataset segment: {segment}")
        if col_set in (["feature", "label"], ("feature", "label")):
            return frame
        if col_set == "feature":
            return frame["feature"]
        raise QlibWorkflowError(f"Unsupported qlib dataset request: segment={segment!r}, col_set={col_set!r}")


class QlibLightGBMBackend:
    """Optional upstream backend using pyqlib LGBModel.

    Requires: pip install pyqlib==0.9.6 lightgbm
    """

    def train(self, dataset: PreparedQlibDataset, config: TrainingConfig) -> BackendTrainingResult:
        try:
            import numpy as np  # type: ignore
            from qlib.contrib.model.gbdt import LGBModel  # type: ignore
        except ImportError as exc:
            raise QlibWorkflowError(
                "Qlib backend unavailable. Install services/research/qlib/requirements.txt first."
            ) from exc

        X = np.asarray(dataset.feature_matrix, dtype=np.float32)
        y = np.asarray(dataset.labels, dtype=np.float32)

        # Qlib's LGBModel expects a dataset object exposing train/valid segments.
        split = min(max(1, int(len(y) * 0.8)), len(y) - 1)
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]
        qlib_dataset = _QlibDatasetView(
            train_features=X_train,
            train_labels=np.asarray([[float(label)] for label in y_train], dtype=np.float32),
            valid_features=X_val,
            valid_labels=np.asarray([[float(label)] for label in y_val], dtype=np.float32),
        )

        model = LGBModel(
            loss="mse",
            num_leaves=config.num_leaves,
            max_depth=config.max_depth,
            learning_rate=config.learning_rate,
            n_estimators=config.n_estimators,
            seed=config.seed,
        )
        model.fit(qlib_dataset)
        preds_val = model.predict(qlib_dataset, segment="valid")
        pred_values = [float(value) for value in preds_val]
        label_values = [float(value) for value in y_val]

        ic = StubLightGBMBackend()._pearson_r(pred_values, label_values) if len(label_values) >= 2 else 0.0
        mse = sum((pred - actual) ** 2 for pred, actual in zip(pred_values, label_values)) / max(
            len(label_values), 1
        )
        run_id = f"qlib-lgbm-{uuid.uuid4().hex[:12]}"
        metrics = {
            "num_samples": dataset.num_samples,
            "num_features": dataset.num_features,
            "num_instruments": dataset.num_instruments,
            "val_ic": round(ic, 6),
            "val_mse": round(mse, 9),
            "n_estimators": config.n_estimators,
        }
        model_payload = {
            "framework": "qlib",
            "model_class": "LGBModel",
            "framework_version": QLIB_VERSION_PIN,
            "feature_names": list(dataset.feature_names),
            "num_leaves": config.num_leaves,
            "max_depth": config.max_depth,
            "learning_rate": config.learning_rate,
            "n_estimators": config.n_estimators,
            "serialization_note": (
                "Serialize model weights separately before final registry submission."
            ),
        }
        return BackendTrainingResult(
            backend=PRIMARY_BACKEND,
            run_id=run_id,
            model_payload=model_payload,
            metrics=metrics,
            notes=("Qlib LGBModel training complete. Persist model weights before production use.",),
        )


def run_qlib_workflow(
    dataset: Mapping[str, Any],
    *,
    backend: LightGBMBackend | None = None,
    config: TrainingConfig | None = None,
    enforce_activation_ready: bool = False,
) -> QlibRunResult:
    prepared = GovernedQlibDataAdapter().prepare(dataset)
    if enforce_activation_ready:
        validate_activation_ready_dataset(prepared)
    training_config = config or TrainingConfig()
    trainer = backend or StubLightGBMBackend()
    training_result = trainer.train(prepared, training_config)
    artifact_bundle = _build_artifact_bundle(prepared, training_result, training_config)
    registry_entry = _build_registry_entry(prepared, training_result, artifact_bundle, training_config)
    candidate_packet = _build_candidate_packet(registry_entry, prepared, training_result)
    artifact_refs = _build_model_eval_artifact_refs(
        prepared,
        training_result,
        artifact_bundle,
        registry_entry,
        candidate_packet,
    )
    return QlibRunResult(
        prepared_dataset=prepared,
        training_result=training_result,
        artifact_bundle=artifact_bundle,
        registry_entry=registry_entry,
        candidate_packet=candidate_packet,
        artifact_refs=artifact_refs,
    )


def validate_activation_ready_dataset(dataset: PreparedQlibDataset) -> None:
    """Enforce Qlib production-data quality floors before model training."""
    failures: list[str] = []
    if dataset.num_instruments < ACTIVATION_READY_MIN_INSTRUMENTS:
        failures.append(
            f"instrument count {dataset.num_instruments} < {ACTIVATION_READY_MIN_INSTRUMENTS}"
        )
    if dataset.history_years < ACTIVATION_READY_MIN_HISTORY_YEARS:
        failures.append(
            f"history years {dataset.history_years:.2f} < {ACTIVATION_READY_MIN_HISTORY_YEARS:.1f}"
        )
    if dataset.min_instrument_history_years < ACTIVATION_READY_MIN_HISTORY_YEARS:
        failures.append(
            "minimum per-instrument history "
            f"{dataset.min_instrument_history_years:.2f} < {ACTIVATION_READY_MIN_HISTORY_YEARS:.1f}"
        )
    if dataset.min_periods_per_instrument < ACTIVATION_READY_MIN_DAILY_PERIODS:
        failures.append(
            "minimum periods per instrument "
            f"{dataset.min_periods_per_instrument} < {ACTIVATION_READY_MIN_DAILY_PERIODS}"
        )
    if dataset.data_frequency.lower() != "daily":
        failures.append(f"data_frequency={dataset.data_frequency!r}, need 'daily' for v1 activation")
    if not dataset.source_strategy_spec_id:
        failures.append("source_strategy_spec_id missing")
    if failures:
        raise QlibWorkflowError("Activation-ready Qlib data gates failed: " + "; ".join(failures))


def persist_qlib_run_artifacts(result: QlibRunResult, output_dir: str | Path) -> dict[str, Any]:
    """Persist activation-ready handoff artifacts without writing the registry."""
    base = Path(output_dir)
    base.mkdir(parents=True, exist_ok=True)
    files = {
        "artifact_bundle": base / "artifact_bundle.json",
        "registry_entry": base / "registry_entry.json",
        "candidate_packet": base / "candidate_packet.json",
        "artifact_refs": base / "artifact_refs.json",
    }
    payloads = {
        "artifact_bundle": result.artifact_bundle,
        "registry_entry": result.registry_entry,
        "candidate_packet": result.candidate_packet,
        "artifact_refs": result.artifact_refs,
    }
    for key, path in files.items():
        path.write_text(json.dumps(payloads[key], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "created_at": utc_now(),
        "registry_id": result.registry_entry["registry_id"],
        "checksum": result.registry_entry["checksum"],
        "backend": result.training_result.backend,
        "files": {key: str(path) for key, path in files.items()},
        "artifact_refs": copy.deepcopy(result.artifact_refs["refs"]),
    }
    manifest_path = base / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _build_model_eval_artifact_refs(
    dataset: PreparedQlibDataset,
    result: BackendTrainingResult,
    artifact_bundle: Mapping[str, Any],
    registry_entry: Mapping[str, Any],
    candidate_packet: Mapping[str, Any],
) -> dict[str, Any]:
    """Build stable refs for Qlib model and evaluation outputs without registering them."""
    registry_id = str(registry_entry["registry_id"])
    version = str(registry_entry["version"])
    artifact_state = str(registry_entry["artifact_state"])
    deployment_stage = str(registry_entry["deployment_summary"]["current_stage"])
    model_artifact_ref = f"{registry_id}@{version}"
    evaluation_report_id = f"eval-{registry_id}-{result.run_id}"
    evaluation_summary = copy.deepcopy(result.metrics)

    model_ref = {
        "artifact_name": "model_artifact",
        "artifact_type": registry_entry["artifact_type"],
        "artifact_ref": model_artifact_ref,
        "registry_id": registry_id,
        "strategy_id": registry_entry["strategy_id"],
        "version": version,
        "artifact_state": artifact_state,
        "deployment_stage": deployment_stage,
        "storage_ref": copy.deepcopy(registry_entry["storage_ref"]),
        "checksum": registry_entry["checksum"],
        "source_run_id": result.run_id,
        "source_dataset_refs": list(dataset.source_dataset_refs),
        "source_strategy_spec_id": dataset.source_strategy_spec_id,
        "source_field": "registry_entry",
    }
    evaluation_ref = {
        "artifact_name": "evaluation_report",
        "artifact_type": "evaluation_result",
        "artifact_ref": f"{evaluation_report_id}@{version}",
        "registry_id": evaluation_report_id,
        "strategy_id": registry_entry["strategy_id"],
        "version": version,
        "artifact_state": "draft",
        "deployment_stage": "none",
        "target_artifact_ref": model_artifact_ref,
        "target_artifact_id": registry_id,
        "target_artifact_version": version,
        "target_artifact_checksum": registry_entry["checksum"],
        "storage_ref": {
            "backend": "inline",
            "path": "$.artifact_bundle.evaluation_summary",
        },
        "checksum": f"sha256:{_sha256_json(evaluation_summary)}",
        "source_run_id": result.run_id,
        "evaluation_summary": evaluation_summary,
        "source_field": "artifact_bundle.evaluation_summary",
    }
    bundle_ref = {
        "artifact_name": "artifact_bundle",
        "artifact_type": "qlib_artifact_bundle",
        "artifact_ref": f"artifact://qlib/{registry_id}/{version}/artifact_bundle",
        "registry_id": registry_id,
        "strategy_id": registry_entry["strategy_id"],
        "version": version,
        "artifact_state": artifact_state,
        "deployment_stage": deployment_stage,
        "storage_ref": {
            "backend": "inline",
            "path": "$.artifact_bundle",
        },
        "checksum": f"sha256:{_sha256_json(artifact_bundle)}",
        "source_run_id": result.run_id,
        "source_field": "artifact_bundle",
    }
    registry_entry_ref = {
        "artifact_name": "registry_entry",
        "artifact_type": "registry_entry_projection",
        "artifact_ref": f"artifact://qlib/{registry_id}/{version}/registry_entry",
        "registry_id": registry_id,
        "strategy_id": registry_entry["strategy_id"],
        "version": version,
        "artifact_state": artifact_state,
        "deployment_stage": deployment_stage,
        "storage_ref": {
            "backend": "inline",
            "path": "$.registry_entry",
        },
        "checksum": f"sha256:{_sha256_json(registry_entry)}",
        "source_run_id": result.run_id,
        "source_field": "registry_entry",
    }
    candidate_packet_ref = {
        "artifact_name": "candidate_packet",
        "artifact_type": "registry_candidate_handoff",
        "artifact_ref": f"artifact://qlib/{registry_id}/{version}/candidate_packet",
        "registry_id": registry_id,
        "strategy_id": registry_entry["strategy_id"],
        "version": version,
        "artifact_state": candidate_packet["requested_artifact_state"],
        "deployment_stage": candidate_packet["deployment_stage"],
        "storage_ref": {
            "backend": "inline",
            "path": "$.candidate_packet",
        },
        "checksum": f"sha256:{_sha256_json(candidate_packet)}",
        "source_run_id": result.run_id,
        "source_field": "candidate_packet",
    }

    refs = [
        model_ref,
        evaluation_ref,
        bundle_ref,
        registry_entry_ref,
        candidate_packet_ref,
    ]
    return {
        "schema_version": "1.0",
        "created_at": registry_entry["created_at"],
        "producer_run_id": result.run_id,
        "backend": result.backend,
        "registry_id": registry_id,
        "strategy_id": registry_entry["strategy_id"],
        "version": version,
        "artifact_state": artifact_state,
        "deployment_stage": deployment_stage,
        "registry_write_authority": "registry_service_only",
        "model_artifact_ref": model_ref,
        "evaluation_report_ref": evaluation_ref,
        "refs": refs,
        "lineage": {
            "source_run_ids": [result.run_id],
            "source_dataset_refs": list(dataset.source_dataset_refs),
            "source_strategy_spec_id": dataset.source_strategy_spec_id,
        },
        "safety_assertions": {
            "no_registry_write": True,
            "registry_write_authority": "registry_service_only",
            "deployment_stage_remains_none": deployment_stage == "none",
            "scoring_only_not_direct_action": True,
        },
    }


def _build_artifact_bundle(
    dataset: PreparedQlibDataset,
    result: BackendTrainingResult,
    config: TrainingConfig,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "artifact_family": "qlib_alpha",
        "model_family": "lightgbm",
        "framework": "qlib",
        "framework_version": QLIB_VERSION_PIN,
        "created_at": utc_now(),
        "created_by": config.requested_by,
        "dataset_summary": dataset.dataset_summary(),
        "training_config": {
            "version": config.version,
            "seed": config.seed,
            "n_estimators": config.n_estimators,
            "num_leaves": config.num_leaves,
            "max_depth": config.max_depth,
            "learning_rate": config.learning_rate,
            "requested_by": config.requested_by,
        },
        "model": copy.deepcopy(result.model_payload),
        "evaluation_summary": copy.deepcopy(result.metrics),
        "governance": {
            "required_ohlcv_fields": list(REQUIRED_OHLCV_FIELDS),
            "direct_live_influence": False,
            "output_type": "alpha_score",
            "lean_consumption": "scoring_only_not_direct_action",
            "notes": list(result.notes),
        },
        "registry_hints": {
            "artifact_type": "model_artifact",
            "model_family": "lightgbm",
            "artifact_state": "draft",
            "deployment_stage": "none",
            "source_dataset_refs": list(dataset.source_dataset_refs),
        },
    }


def _build_registry_entry(
    dataset: PreparedQlibDataset,
    result: BackendTrainingResult,
    artifact_bundle: Mapping[str, Any],
    config: TrainingConfig,
) -> dict[str, Any]:
    storage_path = config.storage_path_template.format(
        strategy_id=dataset.strategy_id,
        version=config.version,
    )
    lineage: dict[str, Any] = {
        "source_run_ids": [result.run_id],
        "source_dataset_refs": list(dataset.source_dataset_refs),
    }
    if dataset.source_strategy_spec_id:
        lineage["source_strategy_spec_id"] = dataset.source_strategy_spec_id

    return {
        "registry_id": f"qlib-alpha-{dataset.strategy_id}-{config.version}",
        "artifact_type": "model_artifact",
        "strategy_id": dataset.strategy_id,
        "version": config.version,
        "artifact_state": "draft",
        "deployment_summary": {"current_stage": "none"},
        "created_at": artifact_bundle["created_at"],
        "lineage": lineage,
        "storage_ref": {
            "backend": config.storage_backend,
            "path": storage_path,
        },
        "checksum": f"sha256:{_sha256_json(artifact_bundle)}",
        "producer_run_id": result.run_id,
        "evaluation_summary": copy.deepcopy(result.metrics),
        "metadata": {
            "framework": "qlib",
            "model_family": "lightgbm",
            "framework_version": QLIB_VERSION_PIN,
            "training_backend": result.backend,
            "feature_names": list(dataset.feature_names),
            "num_instruments": dataset.num_instruments,
            "num_samples": dataset.num_samples,
            "data_frequency": dataset.data_frequency,
            "entry_criteria_satisfied": {
                "version_pinned": True,
                "governed_data_adapter_present": True,
                "lightgbm_backend_present": True,
                "activation_ready_data_volume_met": (
                    dataset.num_instruments >= ACTIVATION_READY_MIN_INSTRUMENTS
                    and dataset.history_years >= ACTIVATION_READY_MIN_HISTORY_YEARS
                    and dataset.min_instrument_history_years >= ACTIVATION_READY_MIN_HISTORY_YEARS
                    and dataset.min_periods_per_instrument >= ACTIVATION_READY_MIN_DAILY_PERIODS
                    and dataset.data_frequency.lower() == "daily"
                ),
                "source_strategy_spec_bound": bool(dataset.source_strategy_spec_id),
            },
        },
    }


def _build_candidate_packet(
    registry_entry: Mapping[str, Any],
    dataset: PreparedQlibDataset,
    result: BackendTrainingResult,
) -> dict[str, Any]:
    candidate_projection = copy.deepcopy(dict(registry_entry))
    candidate_projection["artifact_state"] = "candidate"
    candidate_projection["deployment_summary"] = {"current_stage": "none"}
    candidate_projection.setdefault("metadata", {})[
        "candidate_promotion_scope"
    ] = "offline_alpha_model_review_only"
    return {
        "packet_id": f"qlib-candidate-{dataset.strategy_id}",
        "current_artifact_state": registry_entry["artifact_state"],
        "requested_artifact_state": "candidate",
        "deployment_stage": registry_entry["deployment_summary"]["current_stage"],
        "gate_state": "closed",
        "allowed_next_action": "offline_registry_review_only",
        "registry_write_authority": "registry_service_only",
        "required_checks": [
            "RS-003 candidate proof and StrategySpec binding must remain attached.",
            "Governed data floors must remain satisfied before candidate admission.",
            "Qlib alpha output is scoring-only and must not route to LEAN directly.",
            "Registry admission must happen through the registry service, never this adapter.",
        ],
        "source_run_id": result.run_id,
        "source_dataset_refs": list(dataset.source_dataset_refs),
        "dataset_floor_summary": {
            "num_instruments": dataset.num_instruments,
            "history_years": dataset.history_years,
            "min_instrument_history_years": dataset.min_instrument_history_years,
            "min_periods_per_instrument": dataset.min_periods_per_instrument,
            "data_frequency": dataset.data_frequency,
        },
        "candidate_registry_projection": candidate_projection,
        "evaluator_handoff": {
            "consumer": "EV-001",
            "model_family": "qlib_alpha",
            "candidate_use": "offline_review_only",
            "minimum_artifact_state_for_scoring": "approved",
        },
    }


def _parse_record_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _history_years(values: Sequence[date]) -> float:
    if len(values) < 2:
        return 0.0
    start = min(values)
    end = max(values)
    return max((end - start).days / 365.25, 0.0)

"""Governed Qlib data handler and LightGBM alpha adapter for Pantheon.

Governance boundary:
- Input: Pantheon-governed OHLCV dataset with lineage refs
- Output: registry-ready model_artifact (artifact_state=draft) + registry_entry
- Qlib or its dependencies never write directly to registry, runtime, or LEAN.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

QLIB_VERSION_PIN = "0.9.6"
PRIMARY_BACKEND = "qlib_lgbm"
STUB_BACKEND = "stub_lgbm"
REQUIRED_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
MIN_INSTRUMENTS = 2  # smoke-test floor; production requires >=50 (ACTIVATION_CRITERIA §1)
MIN_PERIODS = 5  # smoke-test floor; production requires 2+ years daily


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


class QlibWorkflowError(ValueError):
    """Raised when a governed Qlib workflow cannot be built safely."""


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

        for instrument in instruments:
            series = sorted(instrument_series[instrument], key=lambda r: r.get("date", ""))
            if len(series) < MIN_PERIODS:
                raise QlibWorkflowError(
                    f"instrument {instrument} has fewer than {MIN_PERIODS} periods"
                )
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

        # Split 80/20 for train/val
        split = max(1, int(len(y) * 0.8))
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]

        model = LGBModel(
            loss="mse",
            num_leaves=config.num_leaves,
            max_depth=config.max_depth,
            learning_rate=config.learning_rate,
            n_estimators=config.n_estimators,
            seed=config.seed,
        )
        model.fit(X_train, y_train, eval_set=[(X_val, y_val)])
        preds_val = model.predict(X_val)

        def pearson_r(a: Any, b: Any) -> float:
            n = len(a)
            if n < 2:
                return 0.0
            ma, mb = float(np.mean(a)), float(np.mean(b))
            num = float(np.sum((a - ma) * (b - mb)))
            denom = float(np.std(a) * np.std(b) * n)
            return num / denom if abs(denom) > 1e-12 else 0.0

        ic = pearson_r(preds_val, y_val) if len(y_val) >= 2 else 0.0
        mse = float(np.mean((preds_val - y_val) ** 2))
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
) -> QlibRunResult:
    prepared = GovernedQlibDataAdapter().prepare(dataset)
    training_config = config or TrainingConfig()
    trainer = backend or StubLightGBMBackend()
    training_result = trainer.train(prepared, training_config)
    artifact_bundle = _build_artifact_bundle(prepared, training_result, training_config)
    registry_entry = _build_registry_entry(prepared, training_result, artifact_bundle, training_config)
    return QlibRunResult(
        prepared_dataset=prepared,
        training_result=training_result,
        artifact_bundle=artifact_bundle,
        registry_entry=registry_entry,
    )


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
        },
        "approved_at": None,
        "approver": None,
        "rollback_target": None,
    }

"""Research adapter backend disclosure report.

This module is intentionally static and governance-facing. It records the
current repository truth for research adapters: whether the default path is a
real backend or a stub/mock path, which real backend is selectable, and which
gate keeps that path non-default.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "ResearchBackendDisclosureReport.v1"
REPORT_ID = "research-backend-disclosure-2026-05-19"
RESEARCH_OUTPUT_SCOPE = "research_artifact_only"

BACKEND_KINDS = frozenset(
    {
        "stub_mock",
        "real_local",
        "real_external",
        "real_service",
        "not_applicable",
    }
)
REAL_BACKEND_STATUSES = frozenset(
    {
        "default",
        "selectable_gated",
        "available_local",
        "not_implemented",
        "not_applicable",
    }
)
ACTIVATION_STATES = frozenset(
    {
        "default_stub_or_mock",
        "real_backend_gated",
        "real_backend_default",
        "deferred_no_real_backend",
    }
)


class DisclosureReportError(ValueError):
    """Raised when a disclosure report would overclaim backend readiness."""


@dataclass(frozen=True)
class DisclosureIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class DisclosureValidationResult:
    passed: bool
    errors: tuple[DisclosureIssue, ...] = ()
    warnings: tuple[DisclosureIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


@dataclass(frozen=True)
class BackendDisclosure:
    adapter_id: str
    adapter_kind: str
    default_backend: str
    default_backend_kind: str
    activation_state: str
    real_backend: str | None = None
    real_backend_status: str = "not_applicable"
    stub_backend: str | None = None
    stub_backend_available: bool = False
    selector: str | None = None
    activation_gates: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    output_scope: str = RESEARCH_OUTPUT_SCOPE
    can_route_orders: bool = False
    silent_stub_fallback: bool = False
    notes: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BackendDisclosure":
        return cls(
            adapter_id=_text(value.get("adapter_id")),
            adapter_kind=_text(value.get("adapter_kind")),
            default_backend=_text(value.get("default_backend")),
            default_backend_kind=_normalized_token(value.get("default_backend_kind")),
            activation_state=_normalized_token(value.get("activation_state")),
            real_backend=_optional_text(value.get("real_backend")),
            real_backend_status=_normalized_token(
                value.get("real_backend_status") or "not_applicable"
            ),
            stub_backend=_optional_text(value.get("stub_backend")),
            stub_backend_available=value.get("stub_backend_available") is True,
            selector=_optional_text(value.get("selector")),
            activation_gates=tuple(_strings(value.get("activation_gates"))),
            evidence_refs=tuple(_strings(value.get("evidence_refs"))),
            output_scope=_optional_text(value.get("output_scope")) or RESEARCH_OUTPUT_SCOPE,
            can_route_orders=value.get("can_route_orders") is True,
            silent_stub_fallback=value.get("silent_stub_fallback") is True,
            notes=tuple(_strings(value.get("notes"))),
        )

    @property
    def real_backend_available(self) -> bool:
        return self.real_backend_status in {"default", "selectable_gated", "available_local"}

    @property
    def uses_stub_or_mock_by_default(self) -> bool:
        return self.default_backend_kind == "stub_mock"

    @property
    def uses_real_backend_by_default(self) -> bool:
        return self.default_backend_kind in {"real_local", "real_external", "real_service"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_kind": self.adapter_kind,
            "default_backend": self.default_backend,
            "default_backend_kind": self.default_backend_kind,
            "uses_stub_or_mock_by_default": self.uses_stub_or_mock_by_default,
            "uses_real_backend_by_default": self.uses_real_backend_by_default,
            "activation_state": self.activation_state,
            "real_backend": self.real_backend,
            "real_backend_status": self.real_backend_status,
            "real_backend_available": self.real_backend_available,
            "stub_backend": self.stub_backend,
            "stub_backend_available": self.stub_backend_available,
            "selector": self.selector,
            "activation_gates": list(self.activation_gates),
            "evidence_refs": list(self.evidence_refs),
            "output_scope": self.output_scope,
            "can_route_orders": self.can_route_orders,
            "silent_stub_fallback": self.silent_stub_fallback,
            "notes": list(self.notes),
        }

    def validate(self, *, path: str) -> DisclosureValidationResult:
        errors: list[DisclosureIssue] = []
        warnings: list[DisclosureIssue] = []

        if not self.adapter_id:
            errors.append(_issue("missing_adapter_id", path, "adapter_id is required"))
        if not self.adapter_kind:
            errors.append(_issue("missing_adapter_kind", path, "adapter_kind is required"))
        if not self.default_backend:
            errors.append(_issue("missing_default_backend", path, "default_backend is required"))
        if self.default_backend_kind not in BACKEND_KINDS:
            errors.append(
                _issue(
                    "invalid_default_backend_kind",
                    f"{path}.default_backend_kind",
                    f"default_backend_kind must be one of {sorted(BACKEND_KINDS)}",
                )
            )
        if self.real_backend_status not in REAL_BACKEND_STATUSES:
            errors.append(
                _issue(
                    "invalid_real_backend_status",
                    f"{path}.real_backend_status",
                    f"real_backend_status must be one of {sorted(REAL_BACKEND_STATUSES)}",
                )
            )
        if self.activation_state not in ACTIVATION_STATES:
            errors.append(
                _issue(
                    "invalid_activation_state",
                    f"{path}.activation_state",
                    f"activation_state must be one of {sorted(ACTIVATION_STATES)}",
                )
            )
        if self.uses_stub_or_mock_by_default and not self.stub_backend_available:
            errors.append(
                _issue(
                    "stub_default_without_stub_backend",
                    path,
                    "stub/mock defaults must name an available stub backend",
                )
            )
        if self.uses_real_backend_by_default and not self.real_backend_available:
            errors.append(
                _issue(
                    "real_default_without_real_backend",
                    path,
                    "real defaults must name an available real backend",
                )
            )
        if self.real_backend_available and not self.real_backend:
            errors.append(
                _issue(
                    "missing_real_backend_name",
                    path,
                    "available real backends must name real_backend",
                )
            )
        if self.real_backend_status == "selectable_gated" and not self.activation_gates:
            errors.append(
                _issue(
                    "missing_real_backend_gate",
                    path,
                    "selectable real backends must list activation_gates",
                )
            )
        if self.silent_stub_fallback:
            errors.append(
                _issue(
                    "silent_stub_fallback",
                    path,
                    "silent fallback from real backend to stub/mock is not allowed",
                )
            )
        if self.can_route_orders:
            errors.append(
                _issue(
                    "order_route_not_allowed",
                    path,
                    "research backend disclosures must remain non-order-routing",
                )
            )
        if self.output_scope != RESEARCH_OUTPUT_SCOPE:
            errors.append(
                _issue(
                    "invalid_output_scope",
                    f"{path}.output_scope",
                    f"output_scope must be {RESEARCH_OUTPUT_SCOPE!r}",
                )
            )
        if not self.evidence_refs:
            errors.append(
                _issue("missing_evidence_refs", path, "each adapter disclosure needs evidence_refs")
            )
        if (
            self.activation_state == "real_backend_gated"
            and self.real_backend_status != "selectable_gated"
        ):
            warnings.append(
                _issue(
                    "activation_state_status_mismatch",
                    path,
                    "real_backend_gated normally pairs with real_backend_status=selectable_gated",
                )
            )

        return DisclosureValidationResult(
            passed=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )


@dataclass(frozen=True)
class ResearchBackendDisclosureReport:
    report_id: str
    generated_at: str
    adapters: tuple[BackendDisclosure, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ResearchBackendDisclosureReport":
        adapters = value.get("adapters")
        if not isinstance(adapters, Sequence) or isinstance(adapters, (str, bytes)):
            raise DisclosureReportError("adapters must be a sequence")
        return cls(
            report_id=_text(value.get("report_id")),
            generated_at=_text(value.get("generated_at")),
            adapters=tuple(BackendDisclosure.from_mapping(item) for item in adapters),
            schema_version=_text(value.get("schema_version") or SCHEMA_VERSION),
        )

    def adapter_by_id(self, adapter_id: str) -> BackendDisclosure:
        for adapter in self.adapters:
            if adapter.adapter_id == adapter_id:
                return adapter
        raise KeyError(adapter_id)

    def summary(self) -> dict[str, Any]:
        errors = self.validate().errors
        return {
            "adapter_count": len(self.adapters),
            "stub_or_mock_default_count": sum(
                1 for adapter in self.adapters if adapter.uses_stub_or_mock_by_default
            ),
            "real_default_count": sum(
                1 for adapter in self.adapters if adapter.uses_real_backend_by_default
            ),
            "real_selectable_gated_count": sum(
                1 for adapter in self.adapters if adapter.real_backend_status == "selectable_gated"
            ),
            "silent_stub_fallback_count": sum(
                1 for adapter in self.adapters if adapter.silent_stub_fallback
            ),
            "order_route_capable_count": sum(
                1 for adapter in self.adapters if adapter.can_route_orders
            ),
            "fail_closed": not errors,
        }

    def validate(self) -> DisclosureValidationResult:
        errors: list[DisclosureIssue] = []
        warnings: list[DisclosureIssue] = []
        seen: set[str] = set()

        if self.schema_version != SCHEMA_VERSION:
            errors.append(
                _issue(
                    "unsupported_schema_version",
                    "schema_version",
                    f"schema_version must be {SCHEMA_VERSION!r}",
                )
            )
        if not self.report_id:
            errors.append(_issue("missing_report_id", "report_id", "report_id is required"))
        if not self.generated_at:
            errors.append(
                _issue("missing_generated_at", "generated_at", "generated_at is required")
            )
        if not self.adapters:
            errors.append(_issue("missing_adapters", "adapters", "at least one adapter is required"))

        for index, adapter in enumerate(self.adapters):
            path = f"adapters[{index}]"
            if adapter.adapter_id in seen:
                errors.append(
                    _issue(
                        "duplicate_adapter_id",
                        f"{path}.adapter_id",
                        f"duplicate adapter_id {adapter.adapter_id!r}",
                    )
                )
            seen.add(adapter.adapter_id)
            result = adapter.validate(path=path)
            errors.extend(result.errors)
            warnings.extend(result.warnings)

        return DisclosureValidationResult(
            passed=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    def assert_valid(self) -> "ResearchBackendDisclosureReport":
        result = self.validate()
        if not result.passed:
            rendered = "; ".join(
                f"{issue.path}: {issue.code}" for issue in result.errors[:5]
            )
            raise DisclosureReportError(f"Research backend disclosure report invalid: {rendered}")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "summary": self.summary(),
            "validation": self.validate().to_dict(),
            "adapters": [adapter.to_dict() for adapter in self.adapters],
        }


def build_default_disclosure_report(
    *,
    generated_at: str | None = None,
) -> ResearchBackendDisclosureReport:
    """Return the default report for the current repository adapter surface."""

    return ResearchBackendDisclosureReport(
        report_id=REPORT_ID,
        generated_at=generated_at or utc_now(),
        adapters=tuple(_default_backend_disclosures()),
    ).assert_valid()


def validate_disclosure_report(
    value: ResearchBackendDisclosureReport | Mapping[str, Any],
) -> ResearchBackendDisclosureReport:
    """Parse and fail-closed validate a report."""

    report = value if isinstance(value, ResearchBackendDisclosureReport) else ResearchBackendDisclosureReport.from_mapping(value)
    return report.assert_valid()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_backend_disclosures() -> Iterable[BackendDisclosure]:
    return (
        BackendDisclosure(
            adapter_id="qlib",
            adapter_kind="qlib",
            default_backend="stub_lgbm",
            default_backend_kind="stub_mock",
            activation_state="real_backend_gated",
            real_backend="qlib_lgbm",
            real_backend_status="selectable_gated",
            stub_backend="stub_lgbm",
            stub_backend_available=True,
            selector="QLIB_BACKEND=real",
            activation_gates=("PANTHEON_QLIB_ACTIVATION_READY_ENABLED=1", "QLIB_BACKEND=real"),
            evidence_refs=(
                "services/research/qlib/worker.py",
                "services/research/qlib/adapter/qlib_adapter.py",
                "services/research/qlib/production_activation_smoke.py",
            ),
            notes=(
                "Default smoke/CI path is StubLightGBMBackend.",
                "Real upstream Qlib LightGBM is explicit-select only.",
            ),
        ),
        BackendDisclosure(
            adapter_id="trl",
            adapter_kind="trl",
            default_backend="stub_dpo",
            default_backend_kind="stub_mock",
            activation_state="real_backend_gated",
            real_backend="trl_dpo",
            real_backend_status="selectable_gated",
            stub_backend="stub_dpo",
            stub_backend_available=True,
            selector="TRL_BACKEND=real",
            activation_gates=("PANTHEON_TRL_ACTIVATION_READY_ENABLED=1", "TRL_BACKEND=real"),
            evidence_refs=(
                "services/learning/trl/worker.py",
                "services/learning/trl/adapter/trl_adapter.py",
                "services/learning/trl/activation_smoke.py",
            ),
            notes=(
                "Default path is StubDPOBackend.",
                "Real TRL DPO must be explicitly selected and dependency-validated.",
            ),
        ),
        BackendDisclosure(
            adapter_id="finrl",
            adapter_kind="finrl",
            default_backend="stub_finrl",
            default_backend_kind="stub_mock",
            activation_state="real_backend_gated",
            real_backend="finrl_ppo",
            real_backend_status="selectable_gated",
            stub_backend="stub_finrl",
            stub_backend_available=True,
            selector="PANTHEON_FINRL_BACKEND=finrl_ppo",
            activation_gates=("PANTHEON_FINRL_PREP_ENABLED=1", "PANTHEON_FINRL_BACKEND=finrl_ppo"),
            evidence_refs=(
                "services/research/finrl/config.py",
                "services/research/finrl/worker.py",
                "services/research/finrl/engine/finrl_adapter.py",
                "services/research/finrl/activation_smoke.py",
            ),
            notes=(
                "Default selector resolves to stub.",
                "PPO/DQN real paths remain bounded offline research outputs.",
            ),
        ),
        BackendDisclosure(
            adapter_id="rllib",
            adapter_kind="rllib",
            default_backend="stub_rllib",
            default_backend_kind="stub_mock",
            activation_state="real_backend_gated",
            real_backend="rllib_ppo",
            real_backend_status="selectable_gated",
            stub_backend="stub_rllib",
            stub_backend_available=True,
            selector="PANTHEON_RLLIB_BACKEND=rllib",
            activation_gates=("PANTHEON_RLLIB_PREP_ENABLED=1", "PANTHEON_RLLIB_BACKEND=rllib"),
            evidence_refs=(
                "services/research/rllib/config.py",
                "services/research/rllib/worker.py",
                "services/research/rllib/adapter/rllib_adapter.py",
                "services/research/rllib/activation_smoke.py",
            ),
            notes=(
                "Default selector token stub resolves to emitted backend stub_rllib.",
                "Selector token rllib emits rllib_ppo and remains research-only.",
            ),
        ),
        BackendDisclosure(
            adapter_id="ray_tune",
            adapter_kind="ray_tune",
            default_backend="stub_ray_tune",
            default_backend_kind="stub_mock",
            activation_state="real_backend_gated",
            real_backend="ray_tune_search",
            real_backend_status="selectable_gated",
            stub_backend="stub_ray_tune",
            stub_backend_available=True,
            selector="PANTHEON_RAYTUNE_BACKEND=tune",
            activation_gates=("PANTHEON_RAYTUNE_PREP_ENABLED=1", "PANTHEON_RAYTUNE_BACKEND=tune"),
            evidence_refs=(
                "services/research/rllib/config.py",
                "services/research/rllib/ray_tune_worker.py",
                "services/research/rllib/adapter/ray_tune_adapter.py",
                "services/research/rllib/ray_tune_activation_smoke.py",
            ),
            notes=(
                "Default selector resolves to StubRayTuneBackend.",
                "Real Ray Tune search is gated and non-order-routing.",
            ),
        ),
        BackendDisclosure(
            adapter_id="quantlib",
            adapter_kind="quantlib",
            default_backend="stub_quantlib",
            default_backend_kind="stub_mock",
            activation_state="real_backend_gated",
            real_backend="quantlib",
            real_backend_status="selectable_gated",
            stub_backend="stub_quantlib",
            stub_backend_available=True,
            selector="PANTHEON_QUANTLIB_BACKEND=real",
            activation_gates=("PANTHEON_QUANTLIB_BACKEND=real",),
            evidence_refs=(
                "services/research/quantlib/worker.py",
                "services/research/quantlib/adapter/quantlib_adapter.py",
                "services/research/quantlib/ACTIVATION_CRITERIA.md",
            ),
            notes=(
                "Default local verification uses StubQuantLibBackend.",
                "Real QuantLib backend is environment-gated.",
            ),
        ),
        BackendDisclosure(
            adapter_id="statsmodels",
            adapter_kind="statsmodels",
            default_backend="stub_statsmodels",
            default_backend_kind="stub_mock",
            activation_state="real_backend_gated",
            real_backend="statsmodels",
            real_backend_status="selectable_gated",
            stub_backend="stub_statsmodels",
            stub_backend_available=True,
            selector="PANTHEON_STATSMODELS_BACKEND=real",
            activation_gates=("PANTHEON_STATSMODELS_BACKEND=real",),
            evidence_refs=(
                "services/research/statsmodels/worker.py",
                "services/research/statsmodels/adapter/statsmodels_adapter.py",
                "services/research/statsmodels/ACTIVATION_CRITERIA.md",
            ),
            notes=(
                "Default local verification uses StubStatsmodelsBackend.",
                "Real statsmodels backend is environment-gated.",
            ),
        ),
        BackendDisclosure(
            adapter_id="vectorbt",
            adapter_kind="vectorbt",
            default_backend="stub_backtest",
            default_backend_kind="stub_mock",
            activation_state="real_backend_gated",
            real_backend="vectorbt_portfolio",
            real_backend_status="selectable_gated",
            stub_backend="stub_backtest",
            stub_backend_available=True,
            selector="PANTHEON_VECTORBT_BACKEND=real",
            activation_gates=("PANTHEON_VECTORBT_BACKEND=real",),
            evidence_refs=(
                "services/research/vectorbt/worker.py",
                "services/research/vectorbt/adapter/vectorbt_adapter.py",
                "services/research/vectorbt/ACTIVATION_CRITERIA.md",
            ),
            notes=(
                "Default local verification uses StubVectorbtBackend.",
                "Real vectorbt backend is environment-gated.",
            ),
        ),
        BackendDisclosure(
            adapter_id="imitation_bc",
            adapter_kind="imitation",
            default_backend="softmax_regression_cpu",
            default_backend_kind="real_local",
            activation_state="real_backend_default",
            real_backend="softmax_regression_cpu",
            real_backend_status="default",
            stub_backend=None,
            stub_backend_available=False,
            selector=None,
            activation_gates=(),
            evidence_refs=(
                "services/research/imitation/bc_trainer.py",
                "services/research/imitation/test_bc_trainer.py",
            ),
            notes=(
                "Behavior cloning trainer is a repo-local CPU implementation, not a mock.",
                "It emits behavior_policy artifacts only.",
            ),
        ),
        BackendDisclosure(
            adapter_id="wandb_experiment_tracking",
            adapter_kind="wandb",
            default_backend="not_selected",
            default_backend_kind="not_applicable",
            activation_state="real_backend_gated",
            real_backend="wandb_online",
            real_backend_status="selectable_gated",
            stub_backend="wandb_offline_local_store",
            stub_backend_available=True,
            selector="EXPERIMENT_BACKEND=wandb; PANTHEON_WANDB_MODE=online",
            activation_gates=(
                "PANTHEON_WANDB_ONLINE_SYNC_ENABLED=1",
                "WANDB_API_KEY",
                "PANTHEON_WANDB_PROJECT",
            ),
            evidence_refs=(
                "services/registry/experiments/config.py",
                "services/registry/experiments/adapter.py",
                "services/registry/experiments/WANDB_ACTIVATION.md",
            ),
            notes=(
                "W&B is not selected by default; the default experiment backend remains MLflow.",
                "W&B online sync is explicit-gated; offline local store is not online proof.",
            ),
        ),
    )


def _issue(code: str, path: str, message: str) -> DisclosureIssue:
    return DisclosureIssue(code=code, path=path, message=message)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _normalized_token(value: Any) -> str:
    return _text(value).lower().replace("-", "_").replace(" ", "_")


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, Iterable):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item)
        if text:
            result.append(text)
    return result


if __name__ == "__main__":
    print(json.dumps(build_default_disclosure_report().to_dict(), indent=2, sort_keys=True))

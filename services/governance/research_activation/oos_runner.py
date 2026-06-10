"""Adapter-neutral OOS no-order-route enforcement harness.

The harness is intentionally generic. Per-adapter activation tasks provide the
adapter-specific OOS callable and evidence, while this module enforces the
shared governance invariant that research adapters produce research artifacts
only and never touch broker/order routes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .no_order_route_scanner import (
    DEFAULT_RESEARCH_ADAPTER_PATHS,
    FORBIDDEN_IMPORT_PREFIXES,
    DynamicNoOrderRouteProof,
    NoOrderRouteViolationError,
    StaticScanResult,
    assert_no_order_route_after_training,
    scan_default_research_adapters,
    scan_paths,
)
from .production_data_proof import (
    ALLOWED_ADAPTER_OUTPUT_TYPES,
    FORBIDDEN_ADAPTER_OUTPUT_TYPES,
    ORDER_CAPABLE_TARGETS,
)


SCHEMA_VERSION = "ResearchActivationOOSNoOrderRouteHarness.v1"
DEFAULT_EXECUTION_TARGETS: tuple[str, ...] = ("research", "registry_review")


class OOSRunnerError(ValueError):
    """Raised when an OOS no-order-route harness check fails closed."""


@dataclass(frozen=True)
class OOSRunnerIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class OOSNoOrderRouteProof:
    """Serializable proof emitted by the generic OOS no-order-route harness."""

    adapter_id: str
    adapter_kind: str
    static_scan: StaticScanResult
    dynamic_probe: DynamicNoOrderRouteProof
    produced_artifact_types: tuple[str, ...]
    execution_targets: tuple[str, ...] = DEFAULT_EXECUTION_TARGETS
    attempted_mutation_types: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def no_order_route(self) -> bool:
        return self.static_scan.passed and self.dynamic_probe.passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "adapter_id": self.adapter_id,
            "adapter_kind": self.adapter_kind,
            "no_order_route": self.no_order_route,
            "produced_artifact_types": list(self.produced_artifact_types),
            "execution_targets": list(self.execution_targets),
            "attempted_mutation_types": list(self.attempted_mutation_types),
            "evidence_refs": list(self.evidence_refs),
            "static_scan": self.static_scan.to_dict(),
            "dynamic_probe": self.dynamic_probe.to_dict(),
            "metadata": dict(self.metadata),
        }


def assert_oos_no_order_route(
    oos_step: Callable[[], Any],
    *,
    adapter_id: str,
    adapter_kind: str,
    produced_artifact_types: Sequence[str],
    adapter_paths: Iterable[str | Path] | None = None,
    repo_root: str | Path | None = None,
    execution_targets: Sequence[str] = DEFAULT_EXECUTION_TARGETS,
    attempted_mutation_types: Sequence[str] = (),
    evidence_refs: Sequence[str] = (),
    label: str | None = None,
    forbidden_import_prefixes: Sequence[str] = FORBIDDEN_IMPORT_PREFIXES,
    metadata: Mapping[str, Any] | None = None,
) -> OOSNoOrderRouteProof:
    """Run one OOS step under static and dynamic no-order-route controls.

    ``adapter_paths`` may point at one adapter or many adapter roots. Passing
    ``None`` scans the repository's default research adapter roots, which keeps
    this harness generic for parent-level enforcement tests.
    """

    adapter_id_value = _required_text(adapter_id, "adapter_id")
    adapter_kind_value = _normalized_tokens([adapter_kind], path="adapter_kind")[0]
    produced = _normalized_tokens(produced_artifact_types, path="produced_artifact_types")
    targets = _normalized_tokens(execution_targets, path="execution_targets")
    mutations = _normalized_tokens(attempted_mutation_types, path="attempted_mutation_types")

    _raise_for_control_issues(
        _validate_research_only_controls(
            produced_artifact_types=produced,
            execution_targets=targets,
            attempted_mutation_types=mutations,
        )
    )

    static_scan = _scan_adapter_paths(adapter_paths, repo_root=repo_root)
    if not static_scan.passed:
        issues = [
            OOSRunnerIssue(
                code=f"static_{violation.kind}",
                path=f"static_scan.violations[{index}]",
                message=f"{violation.path}:{violation.line} {violation.reason}",
            )
            for index, violation in enumerate(static_scan.violations)
        ]
        _raise_for_control_issues(issues)

    try:
        dynamic_probe = assert_no_order_route_after_training(
            oos_step,
            label=label or f"{adapter_id_value}_oos_step",
            forbidden_import_prefixes=forbidden_import_prefixes,
        )
    except NoOrderRouteViolationError as exc:
        raise OOSRunnerError(f"dynamic_no_order_route_violation: {exc}") from exc

    proof = OOSNoOrderRouteProof(
        adapter_id=adapter_id_value,
        adapter_kind=adapter_kind_value,
        produced_artifact_types=produced,
        execution_targets=targets,
        attempted_mutation_types=mutations,
        evidence_refs=tuple(_required_text(item, "evidence_refs") for item in evidence_refs),
        static_scan=static_scan,
        dynamic_probe=dynamic_probe,
        metadata=dict(metadata or {}),
    )
    if not proof.no_order_route:
        raise OOSRunnerError("no_order_route_not_proven")
    return proof


def _scan_adapter_paths(
    adapter_paths: Iterable[str | Path] | None,
    *,
    repo_root: str | Path | None,
) -> StaticScanResult:
    if adapter_paths is None:
        return scan_default_research_adapters(repo_root=repo_root)
    return scan_paths(adapter_paths, repo_root=repo_root)


def _validate_research_only_controls(
    *,
    produced_artifact_types: Sequence[str],
    execution_targets: Sequence[str],
    attempted_mutation_types: Sequence[str],
) -> tuple[OOSRunnerIssue, ...]:
    issues: list[OOSRunnerIssue] = []
    produced_set = set(produced_artifact_types)
    if not produced_set:
        issues.append(
            OOSRunnerIssue(
                code="missing_produced_artifact_types",
                path="produced_artifact_types",
                message="OOS harness must declare produced research artifact types",
            )
        )

    forbidden_outputs = sorted(produced_set & FORBIDDEN_ADAPTER_OUTPUT_TYPES)
    if forbidden_outputs:
        issues.append(
            OOSRunnerIssue(
                code="forbidden_adapter_output",
                path="produced_artifact_types",
                message=f"forbidden order/runtime output type: {', '.join(forbidden_outputs)}",
            )
        )

    unknown_outputs = sorted(
        produced_set - ALLOWED_ADAPTER_OUTPUT_TYPES - FORBIDDEN_ADAPTER_OUTPUT_TYPES
    )
    if unknown_outputs:
        issues.append(
            OOSRunnerIssue(
                code="unknown_adapter_output",
                path="produced_artifact_types",
                message=f"unknown research output type: {', '.join(unknown_outputs)}",
            )
        )

    order_targets = sorted(set(execution_targets) & ORDER_CAPABLE_TARGETS)
    if order_targets:
        issues.append(
            OOSRunnerIssue(
                code="order_capable_execution_target",
                path="execution_targets",
                message=f"order-capable target is forbidden: {', '.join(order_targets)}",
            )
        )

    forbidden_mutations = sorted(set(attempted_mutation_types) & FORBIDDEN_ADAPTER_OUTPUT_TYPES)
    if forbidden_mutations:
        issues.append(
            OOSRunnerIssue(
                code="forbidden_mutation_type",
                path="attempted_mutation_types",
                message=f"forbidden mutation type: {', '.join(forbidden_mutations)}",
            )
        )

    return tuple(issues)


def _raise_for_control_issues(issues: Sequence[OOSRunnerIssue]) -> None:
    if not issues:
        return
    codes = ", ".join(issue.code for issue in issues)
    details = "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
    raise OOSRunnerError(f"{codes}: {details}")


def _normalized_tokens(values: Sequence[str] | Iterable[str], *, path: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for value in values:
        token = _required_text(value, path).lower().replace("-", "_").replace(" ", "_")
        if token:
            tokens.append(token)
    return tuple(tokens)


def _required_text(value: Any, path: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise OOSRunnerError(f"missing_required_text: {path} is required")
    return text


__all__ = [
    "DEFAULT_EXECUTION_TARGETS",
    "DEFAULT_RESEARCH_ADAPTER_PATHS",
    "OOSNoOrderRouteProof",
    "OOSRunnerError",
    "OOSRunnerIssue",
    "SCHEMA_VERSION",
    "assert_oos_no_order_route",
]

"""No-order-route scanner for research activation evidence.

The scanner is intentionally generic: it inspects research adapter code for
broker/order-route imports and call sites, then provides a dynamic import probe
that records any broker route attempt while an offline training step runs.
"""
from __future__ import annotations

import ast
import importlib.abc
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_RESEARCH_ADAPTER_PATHS: tuple[str, ...] = (
    "services/research/finrl",
    "services/research/rllib",
    "services/research/qlib",
    "services/research/quantlib",
    "services/research/statsmodels",
    "services/research/vectorbt",
    "services/research/imitation",
    "services/learning/trl",
)

FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "services.broker",
    "services.execution.ibkr_adapter",
    "services.execution.kraken_adapter",
    "services.execution.shioaji_adapter",
    "services.execution.sandbox_order_lifecycle",
    "scripts.run_broker_sandbox_order_smoke",
    "ib_insync",
    "shioaji",
    "krakenex",
    "ccxt",
)

FORBIDDEN_CALL_NAMES: frozenset[str] = frozenset(
    {
        "broker_submit",
        "cancel_order",
        "connect_broker",
        "create_order",
        "execute_order",
        "get_broker_client",
        "open_broker_session",
        "place_order",
        "replace_order",
        "route_order",
        "send_order",
        "submit_order",
        "submit_to_broker",
    }
)

FORBIDDEN_CONSTRUCTOR_NAMES: frozenset[str] = frozenset(
    {
        "brokerclient",
        "brokerorderclient",
        "brokersession",
        "livebrokeradapter",
        "orderrouter",
        "productionbrokerclient",
    }
)

FORBIDDEN_CALL_PATTERNS: tuple[str, ...] = (
    "broker.place_order",
    "broker.submit_order",
    "broker.cancel_order",
    "broker.replace_order",
    "broker.execute_order",
    "broker.route_order",
    "broker_order_route",
    "order_router",
    "submit_to_broker",
)

EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        "__pycache__",
        "examples",
        "node_modules",
    }
)


@dataclass(frozen=True)
class RouteScanViolation:
    """A static code-path violation discovered by the no-order-route scanner."""

    path: str
    line: int
    column: int
    kind: str
    target: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "kind": self.kind,
            "target": self.target,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StaticScanResult:
    """Serializable static scan result."""

    root_paths: tuple[str, ...]
    checked_files: tuple[str, ...]
    violations: tuple[RouteScanViolation, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanner": "research_no_order_route.v1",
            "passed": self.passed,
            "root_paths": list(self.root_paths),
            "checked_files": list(self.checked_files),
            "violation_count": len(self.violations),
            "violations": [violation.to_dict() for violation in self.violations],
        }

    def assert_passed(self) -> "StaticScanResult":
        if self.violations:
            rendered = "; ".join(
                f"{violation.path}:{violation.line} {violation.reason}"
                for violation in self.violations[:5]
            )
            raise NoOrderRouteViolationError(f"Research order-route scan failed: {rendered}")
        return self


@dataclass(frozen=True)
class DynamicNoOrderRouteProof:
    """Result from running a training step under the broker outbox probe."""

    label: str
    passed: bool
    broker_outbox_count: int
    broker_outbox: tuple[Mapping[str, Any], ...]
    training_result_summary: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanner": "research_no_order_route_dynamic.v1",
            "label": self.label,
            "passed": self.passed,
            "broker_outbox_count": self.broker_outbox_count,
            "broker_outbox": [dict(item) for item in self.broker_outbox],
            "training_result_summary": dict(self.training_result_summary),
        }


class NoOrderRouteViolationError(RuntimeError):
    """Raised when scanner or dynamic probe observes a broker order route."""


def scan_default_research_adapters(repo_root: str | Path | None = None) -> StaticScanResult:
    """Scan the standard research adapter roots for broker order-route code."""

    root = Path(repo_root).resolve() if repo_root is not None else REPO_ROOT
    return scan_paths((root / rel_path for rel_path in DEFAULT_RESEARCH_ADAPTER_PATHS), repo_root=root)


def scan_paths(paths: Iterable[str | Path], *, repo_root: str | Path | None = None) -> StaticScanResult:
    """Scan Python files under ``paths`` for forbidden broker/order-route code."""

    root = Path(repo_root).resolve() if repo_root is not None else REPO_ROOT
    root_paths: list[str] = []
    checked_files: list[str] = []
    violations: list[RouteScanViolation] = []

    for raw_path in paths:
        path = Path(raw_path).resolve()
        root_paths.append(_display_path(path, root))
        if not path.exists():
            continue
        for py_file in _iter_python_files(path):
            checked_files.append(_display_path(py_file, root))
            violations.extend(_scan_python_file(py_file, root))

    return StaticScanResult(
        root_paths=tuple(root_paths),
        checked_files=tuple(sorted(checked_files)),
        violations=tuple(sorted(violations, key=lambda item: (item.path, item.line, item.column, item.kind))),
    )


def assert_no_order_route_after_training(
    training_step: Callable[[], Any],
    *,
    label: str = "research_training_step",
    forbidden_import_prefixes: Sequence[str] = FORBIDDEN_IMPORT_PREFIXES,
) -> DynamicNoOrderRouteProof:
    """Run one offline training step and assert that the broker outbox is empty."""

    with BrokerOutboxProbe(forbidden_import_prefixes=forbidden_import_prefixes) as probe:
        result = training_step()

    proof = DynamicNoOrderRouteProof(
        label=label,
        passed=not probe.outbox,
        broker_outbox_count=len(probe.outbox),
        broker_outbox=tuple(probe.outbox),
        training_result_summary=_summarize_training_result(result),
    )
    if not proof.passed:
        raise NoOrderRouteViolationError(
            f"{label} touched broker order route(s): {list(proof.broker_outbox)!r}"
        )
    return proof


class BrokerOutboxProbe:
    """Import-time broker/order-route probe used by dynamic training tests."""

    def __init__(self, *, forbidden_import_prefixes: Sequence[str] = FORBIDDEN_IMPORT_PREFIXES) -> None:
        self.forbidden_import_prefixes = tuple(forbidden_import_prefixes)
        self.outbox: list[dict[str, Any]] = []
        self._finder = _ForbiddenImportFinder(self)

    def __enter__(self) -> "BrokerOutboxProbe":
        sys.meta_path.insert(0, self._finder)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        try:
            sys.meta_path.remove(self._finder)
        except ValueError:
            pass
        return False

    def record(self, *, event_type: str, target: str, reason: str) -> None:
        self.outbox.append(
            {
                "event_type": event_type,
                "target": target,
                "reason": reason,
            }
        )


class _ForbiddenImportFinder(importlib.abc.MetaPathFinder):
    def __init__(self, probe: BrokerOutboxProbe) -> None:
        self._probe = probe

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: Any | None = None,
    ) -> Any | None:
        del path, target
        if _is_forbidden_import(fullname, self._probe.forbidden_import_prefixes):
            reason = "research training step attempted to import a broker/order-route module"
            self._probe.record(event_type="forbidden_import", target=fullname, reason=reason)
            raise NoOrderRouteViolationError(f"{reason}: {fullname}")
        return None


def _iter_python_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        if path.suffix == ".py" and _should_scan_file(path):
            yield path
        return

    for candidate in path.rglob("*.py"):
        if _should_scan_file(candidate):
            yield candidate


def _should_scan_file(path: Path) -> bool:
    if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
        return False
    name = path.name
    if name.startswith("test_") or name.endswith("_test.py"):
        return False
    return True


def _scan_python_file(path: Path, repo_root: Path) -> list[RouteScanViolation]:
    display_path = _display_path(path, repo_root)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [
            RouteScanViolation(
                path=display_path,
                line=exc.lineno or 1,
                column=exc.offset or 0,
                kind="syntax_error",
                target=path.name,
                reason=f"cannot parse Python file: {exc.msg}",
            )
        ]

    scanner = _NoOrderRouteVisitor(display_path)
    scanner.visit(tree)
    return scanner.violations


class _NoOrderRouteVisitor(ast.NodeVisitor):
    def __init__(self, display_path: str) -> None:
        self.display_path = display_path
        self.violations: list[RouteScanViolation] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _is_forbidden_import(alias.name, FORBIDDEN_IMPORT_PREFIXES):
                self._add(
                    node,
                    kind="forbidden_import",
                    target=alias.name,
                    reason="research adapter imports a broker/order-route module",
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if _is_forbidden_import(module, FORBIDDEN_IMPORT_PREFIXES):
            imported = ", ".join(alias.name for alias in node.names)
            self._add(
                node,
                kind="forbidden_import",
                target=f"{module}.{imported}" if imported else module,
                reason="research adapter imports from a broker/order-route module",
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        segments = _call_segments(node.func)
        if segments and _is_forbidden_call(segments):
            self._add(
                node,
                kind="forbidden_call",
                target=".".join(segments),
                reason="research adapter calls a broker/order-route function",
            )
        self.generic_visit(node)

    def _add(
        self,
        node: ast.AST,
        *,
        kind: str,
        target: str,
        reason: str,
    ) -> None:
        self.violations.append(
            RouteScanViolation(
                path=self.display_path,
                line=getattr(node, "lineno", 1),
                column=getattr(node, "col_offset", 0),
                kind=kind,
                target=target,
                reason=reason,
            )
        )


def _is_forbidden_import(module_name: str, prefixes: Sequence[str]) -> bool:
    normalized = module_name.strip()
    if not normalized:
        return False
    return any(normalized == prefix or normalized.startswith(f"{prefix}.") for prefix in prefixes)


def _call_segments(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*_call_segments(node.value), node.attr)
    if isinstance(node, ast.Call):
        return _call_segments(node.func)
    return tuple()


def _is_forbidden_call(segments: Sequence[str]) -> bool:
    lowered = tuple(segment.lower() for segment in segments)
    if not lowered:
        return False

    final_name = lowered[-1]
    if final_name in FORBIDDEN_CALL_NAMES:
        return True
    if final_name in FORBIDDEN_CONSTRUCTOR_NAMES:
        return True

    dotted = ".".join(lowered)
    if any(pattern in dotted for pattern in FORBIDDEN_CALL_PATTERNS):
        return True

    return any(
        "broker" in segment
        and any(action in dotted for action in ("submit", "place", "cancel", "replace", "execute", "route"))
        for segment in lowered
    )


def _summarize_training_result(result: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {"result_type": type(result).__name__}
    registry_entry = getattr(result, "registry_entry", None)
    if isinstance(registry_entry, Mapping):
        summary["registry_id"] = registry_entry.get("registry_id")
        summary["artifact_state"] = registry_entry.get("artifact_state")
        deployment = registry_entry.get("deployment_summary")
        if isinstance(deployment, Mapping):
            summary["deployment_stage"] = deployment.get("current_stage")

    for attr_name in ("training_result", "train_eval_result"):
        training_result = getattr(result, attr_name, None)
        if training_result is not None:
            summary["training_backend"] = getattr(training_result, "backend", None)
            summary["training_run_id"] = getattr(training_result, "run_id", None)
            break

    return {key: value for key, value in summary.items() if value is not None}


def _display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


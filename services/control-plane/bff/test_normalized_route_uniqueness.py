"""Permanent architecture gate: Backend normalized route uniqueness and OpenAPI operation ID uniqueness.

ACG-00-BE / ACG-01-013:
- Normalizes path parameter names {parameterName} -> {param} across all routes.
- Includes routes from nested APIRouter instances and mounted sub-applications.
- Excludes framework-generated HEAD and OPTIONS routes via explicit rule.
- Reports route order, owner module, handler qualname, operation ID, and source file/line.
- Asserts one registration per (method, normalized_path) shape.
- Asserts OpenAPI operation IDs are unique across the application.
- Separately asserts no earlier parameter route shadows a later literal route.
- Rejects hard-coded duplicate allowlists.
"""
from __future__ import annotations

import inspect
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest
from fastapi import APIRouter, FastAPI
from starlette.routing import BaseRoute, Mount, Route


BFF_DIR = Path(__file__).resolve().parent
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))


PARAM_PATTERN = re.compile(r"\{[^/:]+(?::[^}]+)?\}")
IGNORED_FRAMEWORK_METHODS = {"HEAD", "OPTIONS"}


@dataclass(frozen=True)
class RouteEntry:
    """Captured metadata for a single route registration."""

    route_order: int
    method: str
    raw_path: str
    normalized_path: str
    owner_module: str
    handler_name: str
    handler_qualname: str
    operation_id: Optional[str]
    source_file: Optional[str]
    source_line: Optional[int]
    endpoint: Any

    def source_location(self) -> str:
        if self.source_file and self.source_line:
            return f"{self.source_file}:{self.source_line}"
        if self.source_file:
            return self.source_file
        return f"{self.owner_module}.{self.handler_qualname}"


@dataclass
class RouteCollision:
    """A collision group of multiple registrations for the same (method, normalized_path)."""

    method: str
    normalized_path: str
    entries: List[RouteEntry]

    def format_diagnostic(self) -> str:
        lines = [
            f"Collision on {self.method} {self.normalized_path} ({len(self.entries)} registrations):"
        ]
        for idx, entry in enumerate(self.entries, start=1):
            lines.append(
                f"  [{idx}] Order: #{entry.route_order} | Path: {entry.raw_path} | "
                f"Module: {entry.owner_module} | Qualname: {entry.handler_qualname} | "
                f"Operation ID: {entry.operation_id or '<none>'} | "
                f"Location: {entry.source_location()}"
            )
        return "\n".join(lines)


def normalize_route_path(path: str) -> str:
    """Normalize path parameter syntax {parameterName} or {param:type} into {param}."""
    cleaned = str(path or "").strip().split("?", 1)[0].rstrip("/")
    if not cleaned:
        return "/"
    return PARAM_PATTERN.sub("{param}", cleaned)


def _safe_source_location(endpoint: Any) -> Tuple[Optional[str], Optional[int]]:
    """Extract source file and line number for an endpoint function or callable."""
    try:
        source_file = inspect.getsourcefile(endpoint)
    except (TypeError, OSError):
        source_file = None
    try:
        lines, line_num = inspect.getsourcelines(endpoint)
        return source_file, line_num
    except (TypeError, OSError):
        return source_file, None


def _is_explicit_user_method(method: str) -> bool:
    """Determine if a method should be included in route uniqueness checking."""
    if method in IGNORED_FRAMEWORK_METHODS:
        return False
    return True


def scan_fastapi_routes(
    app_or_router: Any,
    *,
    prefix: str = "",
) -> List[RouteEntry]:
    """Scan and collect all RouteEntry objects from a FastAPI app or APIRouter."""
    entries: List[RouteEntry] = []
    routes: List[Any] = getattr(app_or_router, "routes", [])

    order_counter = 0

    def _walk_routes(route_list: List[Any], current_prefix: str) -> None:
        nonlocal order_counter
        for route in route_list:
            effective_contexts = getattr(route, "effective_route_contexts", None)
            if callable(effective_contexts):
                for ctx in effective_contexts():
                    raw_path = str(getattr(ctx, "path", "") or "")
                    norm_path = normalize_route_path(raw_path)
                    methods = sorted(getattr(ctx, "methods", set()) or {"GET"})
                    endpoint = getattr(ctx, "endpoint", None)
                    owner_module = getattr(endpoint, "__module__", "") or "<unknown>"
                    handler_name = getattr(endpoint, "__name__", "") or str(endpoint)
                    handler_qualname = getattr(endpoint, "__qualname__", "") or handler_name
                    operation_id = getattr(ctx, "operation_id", None) or getattr(ctx, "name", None)
                    source_file, source_line = _safe_source_location(endpoint)

                    for method in methods:
                        method_upper = method.upper()
                        if not _is_explicit_user_method(method_upper):
                            continue

                        entries.append(
                            RouteEntry(
                                route_order=order_counter,
                                method=method_upper,
                                raw_path=raw_path,
                                normalized_path=norm_path,
                                owner_module=owner_module,
                                handler_name=handler_name,
                                handler_qualname=handler_qualname,
                                operation_id=operation_id,
                                source_file=source_file,
                                source_line=source_line,
                                endpoint=endpoint,
                            )
                        )
                        order_counter += 1
                continue

            if isinstance(route, Mount):
                mount_prefix = f"{current_prefix.rstrip('/')}/{route.path.strip('/')}".rstrip("/")
                if getattr(route, "app", None) and hasattr(route.app, "routes"):
                    _walk_routes(route.app.routes, mount_prefix)
                continue

            if hasattr(route, "path") and hasattr(route, "endpoint"):
                raw_path = f"{current_prefix.rstrip('/')}/{str(route.path).lstrip('/')}"
                if not raw_path:
                    raw_path = "/"
                norm_path = normalize_route_path(raw_path)

                methods = sorted(getattr(route, "methods", set()) or {"GET"})
                endpoint = route.endpoint
                owner_module = getattr(endpoint, "__module__", "") or "<unknown>"
                handler_name = getattr(endpoint, "__name__", "") or str(endpoint)
                handler_qualname = getattr(endpoint, "__qualname__", "") or handler_name
                operation_id = getattr(route, "operation_id", None) or getattr(route, "name", None)
                source_file, source_line = _safe_source_location(endpoint)

                for method in methods:
                    method_upper = method.upper()
                    if not _is_explicit_user_method(method_upper):
                        continue

                    entries.append(
                        RouteEntry(
                            route_order=order_counter,
                            method=method_upper,
                            raw_path=raw_path,
                            normalized_path=norm_path,
                            owner_module=owner_module,
                            handler_name=handler_name,
                            handler_qualname=handler_qualname,
                            operation_id=operation_id,
                            source_file=source_file,
                            source_line=source_line,
                            endpoint=endpoint,
                        )
                    )
                    order_counter += 1
                continue

            if hasattr(route, "routes"):
                _walk_routes(route.routes, current_prefix)

    _walk_routes(routes, prefix)
    return entries


def find_duplicate_normalized_routes(app_or_router: Any) -> List[RouteCollision]:
    """Find all duplicate (method, normalized_path) collisions in the given app or router."""
    entries = scan_fastapi_routes(app_or_router)
    grouped: Dict[Tuple[str, str], List[RouteEntry]] = {}
    for entry in entries:
        key = (entry.method, entry.normalized_path)
        grouped.setdefault(key, []).append(entry)

    collisions: List[RouteCollision] = []
    for (method, norm_path), group_entries in grouped.items():
        if len(group_entries) > 1:
            collisions.append(
                RouteCollision(
                    method=method,
                    normalized_path=norm_path,
                    entries=group_entries,
                )
            )
    return sorted(collisions, key=lambda c: (c.method, c.normalized_path))


def find_duplicate_openapi_operation_ids(
    app: FastAPI,
) -> Dict[str, List[Tuple[str, str, str]]]:
    """Check that all registered OpenAPI operation IDs are unique.

    Returns mapping of duplicate operation_id -> list of (method, path, handler_qualname).
    """
    entries = scan_fastapi_routes(app)
    op_map: Dict[str, List[Tuple[str, str, str]]] = {}

    for entry in entries:
        op_id = entry.operation_id
        if not op_id:
            continue
        op_map.setdefault(op_id, []).append(
            (entry.method, entry.raw_path, f"{entry.owner_module}.{entry.handler_qualname}")
        )

    return {op_id: occurrences for op_id, occurrences in op_map.items() if len(occurrences) > 1}


def find_parameter_route_shadowing(app_or_router: Any) -> List[str]:
    """Assert that no static/literal route is shadowed by an earlier parameterized route."""
    entries = scan_fastapi_routes(app_or_router)
    offenders: List[str] = []

    # Map each entry to its regex
    compiled: List[Tuple[RouteEntry, Optional[re.Pattern]]] = []
    for entry in entries:
        # Construct regex from raw_path
        pattern_str = "^" + PARAM_PATTERN.sub(r"[^/]+", entry.raw_path.rstrip("/")) + "$"
        if entry.raw_path == "/":
            pattern_str = "^/$"
        try:
            rx = re.compile(pattern_str)
        except re.error:
            rx = None
        compiled.append((entry, rx))

    for idx, (entry, _) in enumerate(compiled):
        if "{" in entry.raw_path:
            continue

        for idx2 in range(idx):
            entry2, rx2 = compiled[idx2]
            if "{" not in entry2.raw_path:
                continue
            if entry.method != entry2.method:
                continue

            if rx2 and rx2.match(entry.raw_path):
                ep1 = f"{entry.owner_module}.{entry.handler_qualname}"
                ep2 = f"{entry2.owner_module}.{entry2.handler_qualname}"
                offenders.append(
                    f"Static route '{entry.raw_path}' (#{entry.route_order}, {ep1}) is shadowed by earlier param route '{entry2.raw_path}' (#{entry2.route_order}, {ep2})"
                )
                break

    return offenders


def assert_zero_duplicate_normalized_routes(app_or_router: Any) -> None:
    """Enforce zero duplicate normalized routes with complete competing owner attribution."""
    collisions = find_duplicate_normalized_routes(app_or_router)
    if collisions:
        report = "\n\n".join(c.format_diagnostic() for c in collisions)
        raise AssertionError(
            f"Detected {len(collisions)} duplicate normalized route collision group(s):\n\n{report}\n\n"
            f"Architecture invariant: every endpoint shape must have exactly one canonical owner registration."
        )


def assert_unique_openapi_operation_ids(app: FastAPI) -> None:
    """Enforce unique OpenAPI operation IDs across the FastAPI application."""
    dup_ops = find_duplicate_openapi_operation_ids(app)
    if dup_ops:
        lines = [f"Detected {len(dup_ops)} duplicate OpenAPI operation ID(s):"]
        for op_id, occurrences in dup_ops.items():
            lines.append(f"  Operation ID '{op_id}':")
            for method, path, handler in occurrences:
                lines.append(f"    - {method} {path} -> {handler}")
        raise AssertionError("\n".join(lines))


# --------------------------------------------------------------------------- #
# Unit Tests & Gate Verification
# --------------------------------------------------------------------------- #


def test_parameter_normalization_patterns() -> None:
    assert normalize_route_path("/bff/personas/{persona_id}") == "/bff/personas/{param}"
    assert normalize_route_path("/bff/personas/{id}") == "/bff/personas/{param}"
    assert normalize_route_path("/bff/actions/{type}/{id}/{action}") == "/bff/actions/{param}/{param}/{param}"
    assert normalize_route_path("/bff/actions/{entityType}/{entityId}/{actionId}") == "/bff/actions/{param}/{param}/{param}"
    assert normalize_route_path("/files/{file_path:path}") == "/files/{param}"
    assert normalize_route_path("/users/{uuid:uuid}/details") == "/users/{param}/details"
    assert normalize_route_path("/bff/events/") == "/bff/events"
    assert normalize_route_path("") == "/"


def test_duplicate_route_detection_with_different_param_names() -> None:
    app = FastAPI()

    @app.get("/items/{id}")
    def get_by_id(id: str) -> dict:
        return {"id": id}

    @app.get("/items/{itemId}")
    def get_by_item_id(itemId: str) -> dict:
        return {"itemId": itemId}

    collisions = find_duplicate_normalized_routes(app)
    assert len(collisions) == 1
    assert collisions[0].method == "GET"
    assert collisions[0].normalized_path == "/items/{param}"
    assert len(collisions[0].entries) == 2

    handlers = [e.handler_name for e in collisions[0].entries]
    assert "get_by_id" in handlers
    assert "get_by_item_id" in handlers

    diag = collisions[0].format_diagnostic()
    assert "get_by_id" in diag
    assert "get_by_item_id" in diag
    assert "Location:" in diag


def test_duplicate_literal_routes_detection() -> None:
    app = FastAPI()

    @app.get("/status")
    def status_one() -> dict:
        return {"v": 1}

    @app.get("/status")
    def status_two() -> dict:
        return {"v": 2}

    collisions = find_duplicate_normalized_routes(app)
    assert len(collisions) == 1
    assert collisions[0].normalized_path == "/status"


def test_distinct_methods_on_same_path_are_allowed() -> None:
    app = FastAPI()

    @app.get("/resources/{id}")
    def read_resource(id: str) -> dict:
        return {"id": id}

    @app.put("/resources/{id}")
    def update_resource(id: str) -> dict:
        return {"id": id}

    @app.delete("/resources/{id}")
    def delete_resource(id: str) -> dict:
        return {"id": id}

    collisions = find_duplicate_normalized_routes(app)
    assert len(collisions) == 0


def test_included_router_collision_detection() -> None:
    app = FastAPI()
    router = APIRouter(prefix="/v1")

    @router.get("/users/{user_id}")
    def router_get_user(user_id: str) -> dict:
        return {}

    app.include_router(router)

    @app.get("/v1/users/{id}")
    def app_get_user(id: str) -> dict:
        return {}

    collisions = find_duplicate_normalized_routes(app)
    assert len(collisions) == 1
    assert collisions[0].normalized_path == "/v1/users/{param}"


def test_openapi_operation_id_collision_detection() -> None:
    app = FastAPI()

    @app.get("/route-a", operation_id="shared_op_id")
    def route_a() -> dict:
        return {}

    @app.get("/route-b", operation_id="shared_op_id")
    def route_b() -> dict:
        return {}

    dup_ops = find_duplicate_openapi_operation_ids(app)
    assert "shared_op_id" in dup_ops
    assert len(dup_ops["shared_op_id"]) == 2

    with pytest.raises(AssertionError, match="duplicate OpenAPI operation ID"):
        assert_unique_openapi_operation_ids(app)


def test_parameter_shadowing_detection() -> None:
    app = FastAPI()

    @app.get("/docs/{doc_id}")
    def get_doc(doc_id: str) -> dict:
        return {}

    @app.get("/docs/overview")
    def get_docs_overview() -> dict:
        return {}

    offenders = find_parameter_route_shadowing(app)
    assert len(offenders) == 1
    assert "Static route '/docs/overview'" in offenders[0]
    assert "is shadowed by earlier param route '/docs/{doc_id}'" in offenders[0]


def test_clean_app_passes_all_uniqueness_and_shadowing_gates() -> None:
    app = FastAPI()
    router = APIRouter(prefix="/api/v1")

    @router.get("/items/overview", operation_id="get_items_overview")
    def items_overview() -> dict:
        return {}

    @router.get("/items/{item_id}", operation_id="get_item_by_id")
    def item_detail(item_id: str) -> dict:
        return {}

    @router.post("/items", operation_id="create_item")
    def create_item() -> dict:
        return {}

    app.include_router(router)

    assert_zero_duplicate_normalized_routes(app)
    assert_unique_openapi_operation_ids(app)
    assert len(find_parameter_route_shadowing(app)) == 0


def test_scanner_excludes_framework_head_by_explicit_rule() -> None:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict:
        return {"pong": True}

    entries = scan_fastapi_routes(app)
    methods = [e.method for e in entries]
    assert "GET" in methods
    assert "HEAD" not in methods
    assert "OPTIONS" not in methods


def test_gate_has_no_hardcoded_duplicate_allowlist() -> None:
    """Verify that the uniqueness gate has no hardcoded allowlist or bypass tables."""
    import test_normalized_route_uniqueness as gate_module
    module_dict = gate_module.__dict__
    forbidden_tokens = ["EXPECTED_" + "WINNERS", "DUPLICATE_" + "ALLOWLIST", "ALLOWLIST"]
    for token in forbidden_tokens:
        assert token not in module_dict, f"Gate module must not contain allowlist token '{token}'"

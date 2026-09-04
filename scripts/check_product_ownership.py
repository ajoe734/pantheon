#!/usr/bin/env python3
"""Enforce product aggregate ownership, mutation routing, worker ownership, and symbol dispositions.

Permanent architecture gate implementing SD §3 and STRUCT-OWNERSHIP-001:
- §3.1 Aggregate ownership registry: single command/store owner, store owner != router, forbidden writers.
- §3.2 Mutation-to-owner inventory: every mounted non-GET route mapped exactly once with 11 required columns.
- §3.3 Worker ownership inventory: Compose worker/scheduler classification, unique lease keys, partition policies.
- §3.4 Symbol disposition inventory: 208 duplicate-definition groups + 17 unreachable tails, allowed dispositions.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "docs" / "02-architecture" / "product-aggregate-ownership.yaml"
DEFAULT_COMPOSE = REPO_ROOT / "docker-compose.yml"
BFF_DIR = REPO_ROOT / "services" / "control-plane" / "bff"

ALLOWED_DISPOSITIONS = {
    "KEEP_OWNER",
    "MOVE_SHARED_VALUE",
    "DELETE_DUPLICATE",
    "DELETE_DEAD",
    "TEST_ONLY",
}
FORBIDDEN_DISPOSITIONS = {
    "COMPATIBILITY_FOREVER",
}

REQUIRED_ROUTE_COLUMNS = (
    "route",
    "command",
    "router",
    "application_owner",
    "store_owner",
    "table_or_stream",
    "outbox_subject",
    "idempotency_scope",
    "readback_projection",
    "legacy_path",
    "legacy_removal_wave",
)

REQUIRED_WORKER_COLUMNS = (
    "service",
    "profile",
    "input_subject",
    "durable_consumer",
    "lease_key",
    "partition_policy",
    "output_subject",
    "retry_policy",
    "dlq",
    "readiness_probe",
)

REQUIRED_SYMBOL_COLUMNS = (
    "symbol",
    "locations",
    "canonical_owner",
    "disposition",
    "production_callers_before",
    "production_callers_after",
    "deletion_wave",
)


class OwnershipValidationError(ValueError):
    """Validation error for product ownership inventories."""


def load_ownership_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    if not path.is_file():
        raise OwnershipValidationError(f"Ownership manifest file not found: {path}")
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise OwnershipValidationError(f"Failed to parse ownership manifest YAML: {exc}") from exc

    if not isinstance(content, dict):
        raise OwnershipValidationError("Ownership manifest root must be a YAML mapping")
    version = content.get("version") or content.get("schema_version")
    if version != 1:
        raise OwnershipValidationError(f"Unsupported manifest version: {version}; expected 1")
    return content


def validate_aggregates(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    aggregates = manifest.get("aggregates")
    if not isinstance(aggregates, dict) or not aggregates:
        return ["Manifest must contain a non-empty 'aggregates' mapping"]

    for name, agg in aggregates.items():
        if not isinstance(agg, dict):
            errors.append(f"Aggregate '{name}' must be an object")
            continue

        command_owner = agg.get("command_owner")
        store_owner = agg.get("store_owner")
        read_projection = agg.get("read_projection")
        event_subjects = agg.get("event_subjects")
        bff_routers = agg.get("bff_routers")
        forbidden_writers = agg.get("forbidden_writers")

        # Zero or multiple command/store owners check
        if not isinstance(command_owner, str) or not command_owner.strip():
            errors.append(f"Aggregate '{name}' must have exactly one non-empty string command_owner")
        if not isinstance(store_owner, str) or not store_owner.strip():
            errors.append(f"Aggregate '{name}' must have exactly one non-empty string store_owner")

        # BFF router must not be store owner
        if isinstance(store_owner, str):
            if store_owner.endswith("_router") or store_owner.endswith("router"):
                errors.append(f"Aggregate '{name}' declares router '{store_owner}' as store_owner (forbidden by SD §3.1)")
            if isinstance(bff_routers, list) and store_owner in bff_routers:
                errors.append(f"Aggregate '{name}' store_owner '{store_owner}' matches a BFF router (forbidden by SD §3.1)")

        if not isinstance(read_projection, str) or not read_projection.strip():
            errors.append(f"Aggregate '{name}' must have a valid read_projection")
        if not isinstance(event_subjects, list) or not event_subjects:
            errors.append(f"Aggregate '{name}' must have non-empty event_subjects list")
        if not isinstance(bff_routers, list) or not bff_routers:
            errors.append(f"Aggregate '{name}' must have non-empty bff_routers list")
        if not isinstance(forbidden_writers, list):
            errors.append(f"Aggregate '{name}' must have forbidden_writers list")

    return errors


def _get_live_mounted_routes(app: Any = None) -> list[str]:
    """Extract all non-GET, non-HEAD, non-OPTIONS routes from the live BFF app."""
    os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
    if str(BFF_DIR) not in sys.path:
        sys.path.insert(0, str(BFF_DIR))
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    if app is None:
        from scripts.bff_route_manifest_backend import _load_bff_app
        app = _load_bff_app()

    from scripts.bff_route_manifest_backend import _route_contexts, normalize_path, IGNORED_METHODS

    mounted_keys: list[str] = []
    seen: set[str] = set()
    for path, methods in _route_contexts(app):
        for method in sorted(methods):
            method = str(method).upper()
            if method in IGNORED_METHODS or method == "GET":
                continue
            key = f"{method} {normalize_path(path)}"
            if key not in seen:
                seen.add(key)
                mounted_keys.append(key)
    return mounted_keys


def validate_mutation_routes(manifest: dict[str, Any], live_app: Any = None) -> list[str]:
    errors: list[str] = []
    routes = manifest.get("mutation_routes")
    if not isinstance(routes, list) or not routes:
        return ["Manifest must contain a non-empty 'mutation_routes' list"]

    inventory_route_keys: list[str] = []
    seen_keys: set[str] = set()

    for idx, entry in enumerate(routes):
        if not isinstance(entry, dict):
            errors.append(f"mutation_routes[{idx}] must be a mapping")
            continue

        route_key = entry.get("route")
        if not isinstance(route_key, str) or not route_key.strip():
            errors.append(f"mutation_routes[{idx}] is missing a valid 'route' string")
            continue

        if route_key in seen_keys:
            errors.append(f"Duplicate route in mutation_routes inventory: '{route_key}'")
        seen_keys.add(route_key)
        inventory_route_keys.append(route_key)

        # Check required columns
        for col in REQUIRED_ROUTE_COLUMNS:
            if col not in entry:
                errors.append(f"mutation_routes[{idx}] ('{route_key}') is missing required column: '{col}'")

        # Verify store_owner is not a router
        store_owner = entry.get("store_owner")
        if isinstance(store_owner, str):
            if store_owner.endswith("_router") or store_owner.endswith("router"):
                errors.append(f"mutation_routes[{idx}] ('{route_key}') declares router '{store_owner}' as store_owner")

    # Compare with live mounted routes in FastAPI app
    try:
        live_mounted_keys = _get_live_mounted_routes(live_app)
        live_set = set(live_mounted_keys)
        inv_set = set(inventory_route_keys)

        omissions = sorted(live_set - inv_set)
        if omissions:
            errors.append(
                f"Mounted mutation routes missing from inventory ({len(omissions)} omissions):\n"
                + "\n".join(f"  - {r}" for r in omissions[:10])
                + (f"\n  ... and {len(omissions) - 10} more" if len(omissions) > 10 else "")
            )

        extra = sorted(inv_set - live_set)
        if extra:
            errors.append(
                f"Inventory declares mutation routes not mounted in live app ({len(extra)} extra):\n"
                + "\n".join(f"  - {r}" for r in extra[:10])
                + (f"\n  ... and {len(extra) - 10} more" if len(extra) > 10 else "")
            )
    except Exception as exc:
        errors.append(f"Failed to inspect live FastAPI route table: {exc}")

    return errors


def validate_worker_ownership(manifest: dict[str, Any], compose_path: Path = DEFAULT_COMPOSE) -> list[str]:
    errors: list[str] = []
    workers = manifest.get("workers")
    if not isinstance(workers, list) or not workers:
        return ["Manifest must contain a non-empty 'workers' list"]

    classified_services: set[str] = set()
    lease_keys: set[str] = set()
    subject_to_workers: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for idx, worker in enumerate(workers):
        if not isinstance(worker, dict):
            errors.append(f"workers[{idx}] must be a mapping")
            continue

        service = worker.get("service")
        if not isinstance(service, str) or not service.strip():
            errors.append(f"workers[{idx}] is missing a valid 'service' string")
            continue

        classified_services.add(service)

        for col in REQUIRED_WORKER_COLUMNS:
            if col not in worker:
                errors.append(f"workers[{idx}] ('{service}') is missing required column: '{col}'")

        # Lease key uniqueness
        lease_key = worker.get("lease_key")
        if lease_key:
            if lease_key in lease_keys:
                errors.append(f"Duplicate lease_key detected: '{lease_key}' used by service '{service}'")
            lease_keys.add(lease_key)

        # Partition policy and subject tracking
        input_sub = worker.get("input_subject")
        if input_sub and input_sub != "none":
            subject_to_workers[input_sub].append(worker)

    # Subject collision check without partition policy
    for subject, consumer_list in subject_to_workers.items():
        if len(consumer_list) > 1:
            for w in consumer_list:
                policy = str(w.get("partition_policy") or "").strip().lower()
                if not policy or policy == "singleton" or policy == "none":
                    errors.append(
                        f"Subject collision on '{subject}': consumer '{w.get('service')}' shares subject "
                        f"without explicit partition policy (policy is '{policy}')"
                    )

    # Compare against docker-compose.yml services
    if compose_path.is_file():
        try:
            dc = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
            services = dc.get("services", {}) if isinstance(dc, dict) else {}
            worker_keywords = ("worker", "scheduler", "consumer", "producer", "listener", "reconciler", "projector")
            for svc_name in services:
                if any(kw in svc_name for kw in worker_keywords):
                    if svc_name not in classified_services:
                        errors.append(f"Compose worker/scheduler service '{svc_name}' is not classified in workers inventory")
        except Exception as exc:
            errors.append(f"Failed to inspect docker-compose.yml: {exc}")

    return errors


def validate_symbol_dispositions(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    dispositions = manifest.get("symbol_dispositions")
    if not isinstance(dispositions, list) or not dispositions:
        return ["Manifest must contain a non-empty 'symbol_dispositions' list"]

    dead_count = 0
    duplicate_count = 0

    for idx, item in enumerate(dispositions):
        if not isinstance(item, dict):
            errors.append(f"symbol_dispositions[{idx}] must be a mapping")
            continue

        symbol = item.get("symbol")
        disp = item.get("disposition")

        for col in REQUIRED_SYMBOL_COLUMNS:
            if col not in item:
                errors.append(f"symbol_dispositions[{idx}] ('{symbol}') is missing required column: '{col}'")

        if disp in FORBIDDEN_DISPOSITIONS:
            errors.append(f"symbol_dispositions[{idx}] ('{symbol}') uses forbidden disposition: '{disp}'")
        elif disp not in ALLOWED_DISPOSITIONS:
            errors.append(f"symbol_dispositions[{idx}] ('{symbol}') uses unknown disposition: '{disp}'")

        if disp == "DELETE_DEAD":
            dead_count += 1
        else:
            duplicate_count += 1

    if dead_count != 17:
        errors.append(f"Expected exactly 17 unreachable tails in symbol_dispositions, got {dead_count}")

    if duplicate_count != 208:
        errors.append(f"Expected exactly 208 duplicate-definition groups in symbol_dispositions, got {duplicate_count}")

    total_expected = 225
    if len(dispositions) != total_expected:
        errors.append(f"Expected {total_expected} total symbol_dispositions (208 + 17), got {len(dispositions)}")

    return errors


def verify_product_ownership(
    manifest_path: Path = DEFAULT_MANIFEST,
    compose_path: Path = DEFAULT_COMPOSE,
    live_app: Any = None,
) -> dict[str, Any]:
    try:
        manifest = load_ownership_manifest(manifest_path)
    except OwnershipValidationError as exc:
        return {
            "valid": False,
            "errors": [str(exc)],
            "stats": {},
        }

    agg_errors = validate_aggregates(manifest)
    route_errors = validate_mutation_routes(manifest, live_app=live_app)
    worker_errors = validate_worker_ownership(manifest, compose_path=compose_path)
    sym_errors = validate_symbol_dispositions(manifest)

    all_errors = agg_errors + route_errors + worker_errors + sym_errors

    stats = {
        "aggregates_count": len(manifest.get("aggregates") or {}),
        "mutation_routes_count": len(manifest.get("mutation_routes") or []),
        "workers_count": len(manifest.get("workers") or []),
        "symbol_dispositions_count": len(manifest.get("symbol_dispositions") or []),
    }

    return {
        "valid": len(all_errors) == 0,
        "errors": all_errors,
        "stats": stats,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify product aggregate ownership, mutation routing, worker ownership, and symbol dispositions."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Path to product-aggregate-ownership.yaml",
    )
    parser.add_argument(
        "--compose",
        type=Path,
        default=DEFAULT_COMPOSE,
        help="Path to docker-compose.yml",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run verification and exit 0 if valid, non-zero if invalid.",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Print JSON verification results to stdout.",
    )

    args = parser.parse_args(argv)

    result = verify_product_ownership(manifest_path=args.manifest, compose_path=args.compose)

    if args.dump:
        print(json.dumps(result, indent=2))

    if not result["valid"]:
        print(f"FAILED: Product ownership verification failed with {len(result['errors'])} error(s):", file=sys.stderr)
        for err in result["errors"]:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("PASS: Product aggregate ownership, mutation routes, workers, and symbol dispositions verified.")
    print(f"  - Aggregates: {result['stats']['aggregates_count']}")
    print(f"  - Mutation routes: {result['stats']['mutation_routes_count']}")
    print(f"  - Workers: {result['stats']['workers_count']}")
    print(f"  - Symbol dispositions: {result['stats']['symbol_dispositions_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

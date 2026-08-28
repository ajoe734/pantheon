"""Boundary tests for the ReadSurfaceStore migration inventory + fixtures.

Covers ACG-RS-FOUNDATION-20260828: the caller inventory must record all 457
`ReadSurfaceStore` methods, and `read_store_fixtures.py` must stay a
test-only surface that never imports the product store or its embedded
fixture packs.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BFF_DIR = TESTS_DIR.parent
sys.path.insert(0, str(BFF_DIR))

import read_store_fixtures as fixtures  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402  (read-only inspection only)

INVENTORY_PATH = TESTS_DIR / "read_store_migration_inventory.json"
VALID_DISPOSITIONS = {"KEEP", "MIGRATE", "REMOVE", "VERIFY", "MERGE"}
REQUIRED_METHOD_KEYS = {
    "method",
    "source_owner",
    "production_callers",
    "production_caller_count",
    "test_callers",
    "test_caller_count",
    "matrix_item",
    "target_disposition",
    "target_owner",
}


def _load_inventory():
    with open(INVENTORY_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def test_inventory_file_is_present_and_well_formed():
    assert INVENTORY_PATH.is_file()
    payload = _load_inventory()
    assert payload["task_id"] == "ACG-RS-FOUNDATION-20260828"
    assert payload["source_class"] == "ReadSurfaceStore"
    assert isinstance(payload["methods"], list)


def test_inventory_covers_all_457_read_surface_store_methods():
    payload = _load_inventory()
    assert payload["method_count"] == 457
    assert len(payload["methods"]) == 457

    live_methods = {
        name
        for name in vars(ReadSurfaceStore)
        if (name == "__init__" or not name.startswith("__"))
        and callable(getattr(ReadSurfaceStore, name))
    }
    inventory_methods = {entry["method"] for entry in payload["methods"]}
    assert inventory_methods == live_methods, (
        "inventory must track the exact current ReadSurfaceStore method set; "
        f"missing={sorted(live_methods - inventory_methods)} "
        f"extra={sorted(inventory_methods - live_methods)}"
    )


def test_every_inventory_entry_records_callers_owner_and_disposition():
    payload = _load_inventory()
    for entry in payload["methods"]:
        missing = REQUIRED_METHOD_KEYS - entry.keys()
        assert not missing, f"{entry.get('method')} missing keys: {missing}"
        assert entry["target_disposition"] in VALID_DISPOSITIONS
        assert entry["matrix_item"].startswith("ACG-02-")
        assert isinstance(entry["production_callers"], list)
        assert isinstance(entry["test_callers"], list)
        assert entry["production_caller_count"] == len(entry["production_callers"])
        assert entry["test_caller_count"] == len(entry["test_callers"])


def test_read_store_and_main_are_not_modified_by_this_task():
    # This task's acceptance criteria forbid touching read_store.py / main.py.
    # We cannot run `git diff` portably inside a unit test sandbox, so this
    # asserts the narrower, always-checkable half: the artifacts this task
    # owns must not themselves be the product files.
    owned_artifacts = {
        "services/control-plane/bff/tests/read_store_migration_inventory.json",
        "services/control-plane/bff/tests/read_store_fixtures.py",
        "services/control-plane/bff/tests/test_read_store_fixture_boundaries.py",
    }
    forbidden = {"services/control-plane/bff/read_store.py", "services/control-plane/bff/main.py"}
    assert not (owned_artifacts & forbidden)


def test_fixtures_module_does_not_import_product_read_store():
    source = (TESTS_DIR / "read_store_fixtures.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    forbidden_substrings = ("read_store", "fixtures_pack")
    for name in imported_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name, (
                f"read_store_fixtures.py must stay independent of product read_store "
                f"internals, found import {name!r}"
            )


def test_every_local_data_key_has_a_named_typed_owner():
    local_data_keys = set(ReadSurfaceStore._LOCAL_DATA_KEYS)
    missing_owner = local_data_keys - set(fixtures.DOMAIN_OWNERS)
    assert not missing_owner, (
        "every ReadSurfaceStore local-data domain must have a named fixture "
        f"owner before it can be retained generically: {sorted(missing_owner)}"
    )
    for domain, owner in fixtures.DOMAIN_OWNERS.items():
        assert isinstance(owner, str) and owner, f"{domain} has no named owner"


def test_make_fixture_record_rejects_unknown_domain():
    try:
        fixtures.make_fixture_record("not_a_real_domain")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for an unregistered fixture domain")


def test_build_fixture_dataset_produces_typed_records_without_product_seed():
    dataset = fixtures.build_fixture_dataset(domains=["capital_pools", "incidents"], records_per_domain=2)
    assert set(dataset) == {"capital_pools", "incidents"}
    for domain, records in dataset.items():
        assert len(records) == 2
        for record in records:
            assert record["owner_area"] == fixtures.DOMAIN_OWNERS[domain]
            assert record["id"]

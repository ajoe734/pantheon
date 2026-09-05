"""Tests for OPGAP-BE-PORT-NAMESPACE-CONSOLIDATION-V2-20260830.

Validates that ``ports/`` is the sole public and implementation namespace for
BFF domain ports and that ``domain_ports`` has been fully retired:

1. ``services/control-plane/bff/domain_ports/`` no longer exists on disk.
2. No production or test Python source under ``services/`` imports from
   ``domain_ports`` (directly or via the
   ``services.control_plane.bff.domain_ports`` package path).
3. Every domain port module under ``ports/`` is importable standalone and
   exposes real implementations (not thin re-export shims) -- i.e. each
   module defines its own classes/functions rather than solely re-exporting
   names bound elsewhere.
4. ``ReadSurfacePorts`` and the unified ``ports`` package continue to resolve
   without any ``domain_ports`` fallback import path.
"""
from __future__ import annotations

import ast
from pathlib import Path
import sys
import unittest

BFF_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BFF_DIR.parents[2]

DOMAIN_PORT_MODULES = (
    "lifecycle_telemetry_governance",
    "ooda_management",
    "operations_consultation",
    "persona_capital_runtime",
    "persona_training",
    "research_knowledge_source",
)

# Historical audit tooling under docs/ narrates a prior program's inventory
# counts and is not live source that exercises the ports package.
EXCLUDED_DIRS = ("docs",)


class PortsNamespaceConsolidationTests(unittest.TestCase):
    def test_domain_ports_directory_removed(self) -> None:
        self.assertFalse(
            (BFF_DIR / "domain_ports").exists(),
            "services/control-plane/bff/domain_ports/ must be deleted",
        )

    def test_no_source_file_imports_domain_ports(self) -> None:
        offenders: list[str] = []
        for path in REPO_ROOT.rglob("*.py"):
            relative = path.relative_to(REPO_ROOT)
            if relative.parts and relative.parts[0] in EXCLUDED_DIRS:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module == "domain_ports" or node.module.startswith("domain_ports."):
                        offenders.append(f"{relative}: from {node.module} import ...")
                    if node.module.endswith(".domain_ports") or ".domain_ports." in node.module:
                        offenders.append(f"{relative}: from {node.module} import ...")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "domain_ports" or alias.name.startswith("domain_ports.") or ".domain_ports" in alias.name:
                            offenders.append(f"{relative}: import {alias.name}")
        self.assertEqual(
            offenders,
            [],
            f"Found live domain_ports imports outside docs/: {offenders}",
        )

    def test_ports_modules_are_standalone_implementations(self) -> None:
        """Each port module must define real symbols, not just re-export them."""
        for name in DOMAIN_PORT_MODULES:
            module_path = BFF_DIR / "ports" / f"{name}.py"
            self.assertTrue(module_path.exists(), f"ports/{name}.py must exist")
            tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
            own_defs = [
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.ClassDef, ast.FunctionDef))
            ]
            self.assertGreater(
                len(own_defs),
                0,
                f"ports/{name}.py must define its own classes/functions, "
                "not merely re-export names from elsewhere",
            )

    def test_ports_modules_import_without_domain_ports_fallback(self) -> None:
        for name in DOMAIN_PORT_MODULES:
            module_path = BFF_DIR / "ports" / f"{name}.py"
            tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        node.module == "domain_ports" or node.module.startswith("domain_ports."),
                        f"ports/{name}.py must not import from domain_ports",
                    )

    def test_read_surface_ports_resolves_via_ports_namespace_only(self) -> None:
        read_surface_path = BFF_DIR / "ports" / "read_surface_ports.py"
        tree = ast.parse(read_surface_path.read_text(encoding="utf-8"), filename=str(read_surface_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module == "domain_ports" or node.module.startswith("domain_ports."),
                    "ports/read_surface_ports.py must not fall back to domain_ports",
                )

    def test_ports_package_importable(self) -> None:
        import importlib

        for name in DOMAIN_PORT_MODULES:
            module = importlib.import_module(f"ports.{name}")
            self.assertIsNotNone(module)

        from services.control_plane.bff.ports import ReadSurfacePorts, create_in_memory_read_surface_ports

        instance = create_in_memory_read_surface_ports()
        self.assertIsInstance(instance, ReadSurfacePorts)


if __name__ == "__main__":
    unittest.main()

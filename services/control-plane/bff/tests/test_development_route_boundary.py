"""Keep product BFF physically independent from local development tooling."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


BFF_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BFF_DIR.parents[2]

from services.control_plane.bff.assistant.routes import create_assistant_router  # noqa: E402


RETIRED_PRODUCT_PATHS = {
    "/bff/assistant/orchestrator/status",
    "/bff/assistant/repair-worktrees/prepare",
    "/bff/assistant/dev-docs/generate",
    "/bff/assistant/dev-docs/{packet_id}",
    "/bff/assistant/dev-bridge/task-packet",
}


class _Identity:
    operator_id = "boundary-test"
    roles = ["operator"]
    claims = {"capabilities": []}


def _context_pack(_session_id: str, _request: Any, _actor: Any) -> dict[str, Any]:
    return {"sources": [], "backend": {}}


def test_product_router_has_no_development_endpoints_or_imports() -> None:
    router = create_assistant_router(
        build_context_pack=_context_pack,
        extract_identity=lambda _authorization: _Identity(),
        require_read_role=lambda _identity: None,
    )
    assert RETIRED_PRODUCT_PATHS.isdisjoint({route.path for route in router.routes})

    source = (BFF_DIR / "assistant" / "routes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(
        module.startswith(("dev_bridge", "dev_docs", "development_", "orchestrator_status", "repair_receipts"))
        for module in imported_modules
    )
    for path in RETIRED_PRODUCT_PATHS:
        assert path not in source


def test_product_main_does_not_mount_or_import_development_tooling() -> None:
    source = (BFF_DIR / "main.py").read_text(encoding="utf-8")
    assert "development_bridge" not in source
    assert "development_routes" not in source
    assert "development_repair" not in source
    assert "PANTHEON_DEVELOPMENT_TOOLING_ROUTES_ENABLED" not in source
    for path in RETIRED_PRODUCT_PATHS:
        assert path not in source


def test_product_image_excludes_local_development_tooling() -> None:
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".orchestrator/*" in dockerignore.splitlines()
    assert ".orchestrator/development_bridge" not in {
        line[1:].strip()
        for line in dockerignore.splitlines()
        if line.startswith("!")
    }


def test_product_bff_starts_without_development_tooling_source() -> None:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = f"{BFF_DIR}:{BFF_DIR.parents[2]}"
    command = """
import json
import sys
import services.control_plane.bff.main as main

development_modules = sorted(
    name for name in sys.modules
    if name.startswith((
        'assistant.development_',
        'assistant.dev_bridge',
        'assistant.dev_docs',
        'assistant.orchestrator_status',
        'assistant.repair_receipts',
        'development_bridge',
    ))
)
development_paths = sorted(
    path for route in main.app.routes
    if (path := getattr(route, 'path', None)) in %r
)
print(json.dumps({
    'development_modules': development_modules,
    'development_paths': development_paths,
}))
""" % RETIRED_PRODUCT_PATHS
    completed = subprocess.run(
        [sys.executable, "-c", command],
        cwd=BFF_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result == {"development_modules": [], "development_paths": []}


def test_product_adapter_has_no_source_write_or_development_task_surface() -> None:
    adapter_root = REPO_ROOT / "services" / "openclaw-gateway-adapter"
    source = "\n".join(
        (adapter_root / name).read_text(encoding="utf-8")
        for name in ("main.py", "assistant_codex_provider.py", "tool_workflow_bridge.py")
    )
    for retired in (
        "repair-worktrees/prepare",
        "workspace-write",
        "bff.route:POST /bff/assistant/dev-docs/generate",
    ):
        assert retired not in source

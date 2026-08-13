from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


BFF_DIR = Path(__file__).resolve().parents[1]
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

from assistant.development_routes import create_development_router  # noqa: E402
from assistant.routes import create_assistant_router  # noqa: E402


DEVELOPMENT_PATHS = {
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
    assert DEVELOPMENT_PATHS.isdisjoint({route.path for route in router.routes})

    source_path = BFF_DIR / "assistant" / "routes.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(
        module.startswith(
            (
                "dev_bridge",
                "dev_docs",
                "development_routes",
                "orchestrator_status",
                "repair_receipts",
            )
        )
        for module in imported_modules
    )
    for path in DEVELOPMENT_PATHS:
        assert path not in source


def test_development_router_owns_all_removable_endpoints() -> None:
    router = create_development_router(
        build_context_pack=_context_pack,
        extract_identity=lambda _authorization: _Identity(),
        require_read_role=lambda _identity: None,
    )
    assert DEVELOPMENT_PATHS <= {route.path for route in router.routes}


def test_product_router_can_load_without_development_module() -> None:
    source = (BFF_DIR / "assistant" / "routes.py").read_text(encoding="utf-8")
    assert "assistant.development_routes" not in source
    assert "from .development_routes" not in source
    assert "import development_routes" not in source


def test_product_only_bff_does_not_import_or_mount_development_tooling() -> None:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PANTHEON_DEVELOPMENT_TOOLING_ROUTES_ENABLED"] = "false"
    env["PYTHONPATH"] = f"{BFF_DIR}:{BFF_DIR.parents[2]}"
    command = """
import json
import sys
import main

development_modules = sorted(
    name for name in sys.modules
    if name.startswith((
        'assistant.development_',
        'assistant.dev_bridge',
        'assistant.dev_docs',
        'assistant.orchestrator_status',
        'assistant.repair_receipts',
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
""" % DEVELOPMENT_PATHS
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

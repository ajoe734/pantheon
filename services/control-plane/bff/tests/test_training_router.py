"""Ownership checks for the dedicated Training BFF router."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


BFF_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BFF_ROOT))

from training.router import create_training_router


def _page_slice(
    items: List[Dict[str, Any]],
    _page_token: Optional[str],
    _page_size: int,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    return items, None


def test_training_router_owns_all_trainer_session_routes() -> None:
    router = create_training_router(
        get_read_store=lambda: object(),
        extract_identity=lambda _authorization: object(),
        require_read_role=lambda _identity: None,
        bff_error=lambda *_args, **_kwargs: RuntimeError("unexpected test error"),
        utc_now=lambda: "2026-08-30T00:00:00Z",
        page_slice=_page_slice,
        dataset_surface_status=lambda *_args, **_kwargs: {"status": "available"},
    )

    expected = {
        ("POST", "/api/v1/trainer/sessions"),
        ("GET", "/api/v1/trainer/sessions"),
        ("GET", "/api/v1/trainer/sessions/{session_id}"),
        ("GET", "/api/v1/trainer/sessions/{session_id}/controls"),
        ("POST", "/api/v1/trainer/sessions/{session_id}/patch"),
        ("POST", "/api/v1/trainer/sessions/{session_id}/message"),
        ("GET", "/api/v1/trainer/sessions/{session_id}/preview"),
        ("POST", "/api/v1/trainer/sessions/{session_id}/preview"),
        ("GET", "/api/v1/trainer/replay"),
        ("GET", "/api/v1/trainer/replay/{session_id}"),
        ("POST", "/api/v1/trainer/sessions/{session_id}/commit"),
        ("POST", "/api/v1/trainer/sessions/{session_id}/discard"),
        ("POST", "/api/v1/trainer/sessions/{session_id}/rapid-eval"),
        ("GET", "/api/v1/trainer/sessions/{session_id}/rapid-eval/{eval_id}"),
    }
    actual = {
        (method, route.path)
        for route in router.routes
        for method in route.methods
        if method in {"GET", "POST"} and route.path.startswith("/api/v1/trainer/")
    }
    assert actual == expected


def test_main_composes_training_router_without_trainer_decorators() -> None:
    main_source = (BFF_ROOT / "main.py").read_text(encoding="utf-8")
    assert "from training.router import create_training_router" in main_source
    assert not re.search(
        r'@app\.(?:get|post|put|patch|delete)\([^\n]*"/api/v1/trainer/',
        main_source,
    )

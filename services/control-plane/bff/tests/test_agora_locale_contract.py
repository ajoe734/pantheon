"""AG-UIPOL-001 locale ownership contract for generated Trading Room proposals."""
from __future__ import annotations

import inspect
import re

from services.control_plane.bff.agora.trading_room import router


def test_workspace_proposal_emits_stable_i18n_keys_and_codes() -> None:
    views = router._build_winner_branch_views("strategy-1", "v1")

    assert views
    for view in views:
        prefix = f"agora.tradingRoom.views.{view['id']}"
        assert view["viewKind"] == view["id"]
        assert view["titleKey"] == f"{prefix}.title"
        assert view["purposeKey"] == f"{prefix}.purpose"
        assert view["rationaleKey"] == f"{prefix}.rationale"
        assert len(view["warningCodes"]) == len(view["warnings"])
        for widget in view["widgets"]:
            widget_prefix = f"agora.tradingRoom.widgets.{widget['id']}"
            assert widget["titleKey"] == f"{widget_prefix}.title"
            assert widget["purposeKey"] == f"{widget_prefix}.purpose"
            assert widget["whyIncludedKey"] == f"{widget_prefix}.whyIncluded"


def test_workspace_generator_source_contains_no_cjk_display_copy() -> None:
    source = inspect.getsource(router._build_winner_branch_views)
    assert re.search(r"[\u3400-\u9fff]", source) is None

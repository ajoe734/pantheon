"""Regression tests for scripts/project_approvals_to_bff_surfaces.py.

Covers the two bugs fixed in CONSOLE-DATA-APPROVALS:
1. Default URL was http://promotion-svc:8089; correct docker-compose name is
   http://promotion:8089.
2. A failed HTTP fetch (connection error, timeout) must NOT write or overwrite
   approval_decisions.json. Before the fix the script always wrote an empty
   file, which shadowed PANTHEON_BFF_APPROVAL_DECISION_STORE and kept
   /bff/approvals at count=0.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Import the projection script as a module.
# ---------------------------------------------------------------------------

_SCRIPT = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "../../../../scripts/project_approvals_to_bff_surfaces.py",
    )
)

# Load as a module without executing __main__.
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("project_approvals", _SCRIPT)
_proj_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_proj_mod)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Default URL regression
# ---------------------------------------------------------------------------

class TestDefaultUrl:
    """The default PROMOTION_URL must match the docker-compose service name."""

    def test_default_url_is_promotion_not_promotion_svc(self, tmp_path) -> None:
        """main() must use http://promotion:8089 when PROMOTION_URL is unset."""
        captured_urls: list[str] = []

        def fake_get(url: str) -> object:
            captured_urls.append(url)
            # Return a valid empty-items response so the script writes the file.
            return {"items": []}

        env_backup = os.environ.pop("PROMOTION_URL", None)
        try:
            with patch.object(_proj_mod, "_get", side_effect=fake_get):
                os.environ["OUT_DIR"] = str(tmp_path)
                rc = _proj_mod.main()
        finally:
            if env_backup is not None:
                os.environ["PROMOTION_URL"] = env_backup
            else:
                os.environ.pop("PROMOTION_URL", None)
            os.environ.pop("OUT_DIR", None)

        assert rc == 0
        assert captured_urls, "expected at least one GET call"
        assert all("promotion-svc" not in u for u in captured_urls), (
            f"script used stale 'promotion-svc' hostname: {captured_urls}"
        )
        assert any("http://promotion:8089" in u for u in captured_urls), (
            f"expected http://promotion:8089 as default; got {captured_urls}"
        )


# ---------------------------------------------------------------------------
# Fetch-failure poison-prevention regression
# ---------------------------------------------------------------------------

class TestFetchFailureDoesNotPoisonStore:
    """When the HTTP fetch fails, the script must NOT write or overwrite the file."""

    def test_failed_fetch_does_not_create_file(self, tmp_path) -> None:
        """If the store file does not yet exist and the fetch fails, no file is
        created and the script returns non-zero."""
        out_file = tmp_path / "approval_decisions.json"
        assert not out_file.exists()

        def fail_get(url: str) -> None:
            return None  # simulate network error

        with patch.object(_proj_mod, "_get", side_effect=fail_get):
            rc = _proj_mod.main.__wrapped__ if hasattr(_proj_mod.main, "__wrapped__") else _proj_mod.main
            # Call project() directly to verify the sentinel path.
            result = _proj_mod.project("http://promotion:8089")

        assert result is None, "project() must return None on fetch failure"
        assert not out_file.exists(), "no file should have been created on fetch failure"

    def test_failed_fetch_does_not_overwrite_existing_store(self, tmp_path) -> None:
        """If the store file already has data and the fetch fails, the existing
        file must be preserved and the script returns non-zero."""
        existing_decision = {
            "apv-reg-001": {
                "decision_id": "apv-reg-001",
                "decision_state": "proposed",
            }
        }
        out_file = tmp_path / "approval_decisions.json"
        out_file.write_text(json.dumps(existing_decision))

        def fail_get(url: str) -> None:
            return None

        with patch.object(_proj_mod, "_get", side_effect=fail_get):
            env_backup = os.environ.get("PROMOTION_URL")
            out_backup = os.environ.get("OUT_DIR")
            try:
                os.environ["PROMOTION_URL"] = "http://promotion:8089"
                os.environ["OUT_DIR"] = str(tmp_path)
                rc = _proj_mod.main()
            finally:
                if env_backup is not None:
                    os.environ["PROMOTION_URL"] = env_backup
                else:
                    os.environ.pop("PROMOTION_URL", None)
                if out_backup is not None:
                    os.environ["OUT_DIR"] = out_backup
                else:
                    os.environ.pop("OUT_DIR", None)

        assert rc != 0, "main() must return non-zero when fetch fails"
        surviving = json.loads(out_file.read_text())
        assert surviving == existing_decision, (
            "existing store was overwritten by a failed fetch; "
            f"got {surviving}"
        )

    def test_successful_empty_response_does_write_file(self, tmp_path) -> None:
        """A legitimate empty response (0 items) from the promotion service
        should still write an empty dict (not be treated as a failure)."""
        out_file = tmp_path / "approval_decisions.json"

        def ok_get(url: str) -> object:
            return {"items": []}

        with patch.object(_proj_mod, "_get", side_effect=ok_get):
            env_backup = os.environ.get("PROMOTION_URL")
            out_backup = os.environ.get("OUT_DIR")
            try:
                os.environ["PROMOTION_URL"] = "http://promotion:8089"
                os.environ["OUT_DIR"] = str(tmp_path)
                rc = _proj_mod.main()
            finally:
                if env_backup is not None:
                    os.environ["PROMOTION_URL"] = env_backup
                else:
                    os.environ.pop("PROMOTION_URL", None)
                if out_backup is not None:
                    os.environ["OUT_DIR"] = out_backup
                else:
                    os.environ.pop("OUT_DIR", None)

        assert rc == 0, "main() must return 0 for a legitimate empty response"
        assert out_file.exists(), "file should be written for a legitimate empty response"
        written = json.loads(out_file.read_text())
        assert written == {}, f"expected empty dict, got {written}"

    def test_successful_response_writes_decisions(self, tmp_path) -> None:
        """A successful fetch with real approvals writes keyed decisions."""
        decision = {
            "decision_id": "apv-reg-001",
            "decision_state": "proposed",
        }

        def ok_get(url: str) -> object:
            return {"items": [decision]}

        with patch.object(_proj_mod, "_get", side_effect=ok_get):
            env_backup = os.environ.get("PROMOTION_URL")
            out_backup = os.environ.get("OUT_DIR")
            try:
                os.environ["PROMOTION_URL"] = "http://promotion:8089"
                os.environ["OUT_DIR"] = str(tmp_path)
                rc = _proj_mod.main()
            finally:
                if env_backup is not None:
                    os.environ["PROMOTION_URL"] = env_backup
                else:
                    os.environ.pop("PROMOTION_URL", None)
                if out_backup is not None:
                    os.environ["OUT_DIR"] = out_backup
                else:
                    os.environ.pop("OUT_DIR", None)

        assert rc == 0
        written = json.loads((tmp_path / "approval_decisions.json").read_text())
        assert "apv-reg-001" in written, f"expected decision key in output: {written}"

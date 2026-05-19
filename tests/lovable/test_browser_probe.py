from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Callable


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lovable" / "browser_probe.py"
spec = importlib.util.spec_from_file_location("browser_probe", SCRIPT)
assert spec and spec.loader
browser_probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(browser_probe)

run_browser_probe = browser_probe.run_browser_probe
detect_mock_seed = browser_probe.detect_mock_seed


def _fake_get(mapping: dict[str, tuple[int, bytes]]) -> Callable[[str, float], tuple[int, bytes]]:
    def get(url: str, timeout: float) -> tuple[int, bytes]:
        del timeout
        if url not in mapping:
            raise KeyError(f"unexpected URL in test: {url}")
        return mapping[url]

    return get


BASE = "https://pantheon-dev.example.com"


def _responses(
    *,
    app_status: int = 200,
    health_status: int = 200,
    health_body: bytes = b'{"status": "ok"}',
    me_status: int = 401,
    me_body: bytes = b'{"error": "unauthorized"}',
) -> dict[str, tuple[int, bytes]]:
    return {
        f"{BASE}/": (app_status, b"<html><head></head><body>app</body></html>"),
        f"{BASE}/health": (health_status, health_body),
        f"{BASE}/bff/me": (me_status, me_body),
    }


# ---------------------------------------------------------------------------
# detect_mock_seed unit tests
# ---------------------------------------------------------------------------


def test_detect_mock_seed_clean_body() -> None:
    assert detect_mock_seed('{"data": {"user": {"id": "real-user-001"}}}') == []


def test_detect_mock_seed_double_mock_marker() -> None:
    matches = detect_mock_seed('"__mock__": true, "is_mock_data": false')
    assert '"__mock__"' in matches


def test_detect_mock_seed_seed_fallback_source() -> None:
    matches = detect_mock_seed('"fallback_source": "seed"')
    assert len(matches) == 1


def test_detect_mock_seed_mock_user_id() -> None:
    matches = detect_mock_seed('"id": "mock-user-0"')
    assert '"mock-user-0"' in " ".join(matches) or len(matches) >= 1


# ---------------------------------------------------------------------------
# run_browser_probe: passing scenarios
# ---------------------------------------------------------------------------


def test_probe_passes_health_200_and_me_401() -> None:
    """Health 200 + /bff/me 401 (real BFF gate) → all_passed=True."""
    result = run_browser_probe(BASE, http_get=_fake_get(_responses(me_status=401)))
    assert result["all_passed"] is True
    assert result["mock_seed_free"] is True
    assert result["health"]["ok"] is True
    assert result["bff_me"]["ok"] is True
    assert result["bff_me"]["status"] == 401
    assert result["errors"] == []


def test_probe_passes_health_200_and_me_403() -> None:
    """Health 200 + /bff/me 403 (real BFF gate) → all_passed=True."""
    result = run_browser_probe(
        BASE,
        http_get=_fake_get(_responses(me_status=403, me_body=b'{"error": "forbidden"}')),
    )
    assert result["all_passed"] is True
    assert result["bff_me"]["status"] == 403


def test_probe_passes_health_200_and_me_200_real_data() -> None:
    """/bff/me 200 with real (non-mock) data → all_passed=True."""
    real_body = b'{"data": {"user": {"id": "usr-001"}, "tenant": {}, "capabilities": []}}'
    result = run_browser_probe(
        BASE,
        http_get=_fake_get(_responses(me_status=200, me_body=real_body)),
    )
    assert result["all_passed"] is True
    assert result["bff_me"]["mock_seed_detected"] is False


def test_probe_passes_with_bearer_token_and_real_me() -> None:
    """Authenticated probe with valid token: /bff/me 200 + real data → pass."""
    real_body = b'{"data": {"user": {"id": "usr-002"}, "tenant": {}, "capabilities": []}}'
    result = run_browser_probe(
        BASE,
        http_get=_fake_get(_responses(me_status=200, me_body=real_body)),
        bearer_token="test-token-abc123",
    )
    assert result["all_passed"] is True


# ---------------------------------------------------------------------------
# run_browser_probe: failing scenarios
# ---------------------------------------------------------------------------


def test_probe_fails_when_health_returns_non_200() -> None:
    result = run_browser_probe(
        BASE,
        http_get=_fake_get(_responses(health_status=503, health_body=b'{"status": "down"}')),
    )
    assert result["all_passed"] is False
    assert result["health"]["ok"] is False
    assert any("/health" in e for e in result["errors"])


def test_probe_fails_when_bff_me_returns_mock_seed_on_200() -> None:
    """Silent fallback: /bff/me 200 with mock seed data → all_passed=False."""
    mock_body = b'{"data": {"user": {"id": "mock-user-001", "__mock__": true}}}'
    result = run_browser_probe(
        BASE,
        http_get=_fake_get(_responses(me_status=200, me_body=mock_body)),
    )
    assert result["all_passed"] is False
    assert result["mock_seed_free"] is False
    assert result["bff_me"]["mock_seed_detected"] is True
    assert any("/bff/me" in e for e in result["errors"])


def test_probe_fails_when_bff_me_seed_fallback_source() -> None:
    seed_body = b'{"fallback_source": "seed", "data": {"user": {}}}'
    result = run_browser_probe(
        BASE,
        http_get=_fake_get(_responses(me_status=200, me_body=seed_body)),
    )
    assert result["all_passed"] is False
    assert result["bff_me"]["mock_seed_detected"] is True


def test_probe_fails_when_health_body_contains_mock_markers() -> None:
    """Health endpoint responding with mock data → all_passed=False."""
    mock_health = b'{"status": "ok", "__mock__": true}'
    result = run_browser_probe(
        BASE,
        http_get=_fake_get(_responses(health_body=mock_health)),
    )
    assert result["all_passed"] is False
    assert result["health"]["mock_seed_detected"] is True


def test_probe_fails_authenticated_when_me_returns_401() -> None:
    """With bearer_token, /bff/me 401 means auth failed → all_passed=False."""
    result = run_browser_probe(
        BASE,
        http_get=_fake_get(_responses(me_status=401)),
        bearer_token="bad-token",
    )
    assert result["all_passed"] is False
    assert result["bff_me"]["ok"] is False


def test_probe_result_shape() -> None:
    """ProbeResult has all required keys and task_id is correct."""
    result = run_browser_probe(BASE, http_get=_fake_get(_responses()))
    assert result["task_id"] == "LSP-002-V2"
    assert "base_url" in result
    assert "checked_at" in result
    assert "app_page_reachable" in result
    assert "health" in result
    assert "bff_me" in result
    assert "mock_seed_free" in result
    assert "all_passed" in result
    assert "errors" in result

from __future__ import annotations

from scripts.verify_e2e_fe_serving import (
    AssetResult,
    FetchResult,
    referenced_asset_urls,
    validate_asset,
    verify_frontend,
)


def _result(
    body: bytes,
    *,
    content_type: str,
    status: int = 200,
    url: str = "https://fe.example/",
) -> FetchResult:
    return FetchResult(status, content_type, body, url)


def test_referenced_assets_include_relative_and_absolute_paths_once() -> None:
    html = """
    <script type="module" src="/assets/app.js"></script>
    <link rel="stylesheet" href="assets/app.css?v=1">
    <script src="/assets/app.js"></script>
    """
    assert referenced_asset_urls("https://fe.example/root/", html) == [
        "https://fe.example/assets/app.js",
        "https://fe.example/root/assets/app.css?v=1",
    ]


def test_spa_html_fallback_is_rejected_even_with_status_200() -> None:
    result = validate_asset(
        _result(b"<!doctype html><html></html>", content_type="text/html"),
        requested_url="https://fe.example/assets/app.js",
    )
    assert result.ok is False
    assert "SPA HTML fallback" in result.detail


def test_wrong_mime_is_rejected() -> None:
    result = validate_asset(
        _result(b"body{}", content_type="text/plain"),
        requested_url="https://fe.example/assets/app.css",
    )
    assert result.ok is False
    assert "content-type=text/plain" in result.detail


def test_verify_frontend_checks_every_referenced_asset() -> None:
    responses = {
        "https://fe.example/": _result(
            b'<html><script src="/assets/app.js"></script>'
            b'<link href="/assets/app.css" rel="stylesheet"></html>',
            content_type="text/html; charset=utf-8",
        ),
        "https://fe.example/assets/app.js": _result(
            b"export const ready = true;",
            content_type="text/javascript",
            url="https://fe.example/assets/app.js",
        ),
        "https://fe.example/assets/app.css": _result(
            b"body { color: black; }",
            content_type="text/css",
            url="https://fe.example/assets/app.css",
        ),
    }
    results = verify_frontend("https://fe.example", fetcher=responses.__getitem__)
    assert len(results) == 2
    assert all(result.ok for result in results)


def test_index_without_assets_fails_closed() -> None:
    results = verify_frontend(
        "https://fe.example",
        fetcher=lambda url: _result(b"<html><main>empty</main></html>", content_type="text/html"),
    )
    assert results == [
        AssetResult(
            url="https://fe.example/",
            ok=False,
            detail="index references no JS/CSS assets",
        )
    ]

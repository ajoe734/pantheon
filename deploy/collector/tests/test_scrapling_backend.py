"""Scrapling backend tests: loud failure, offline JS rendering, NDC dataset member, and in-image imports."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from collector_stack import fetchers
from collector_stack.fetchers import FetchBackendUnavailableError, FixtureTransport, LiveScraplingTransport

COLLECTOR_DIR = Path(__file__).resolve().parents[1]
IMPORT_CHECK = (
    "from scrapling.fetchers import Fetcher, PlayWrightFetcher; "
    "import browserforge, camoufox, playwright; "
    "print('fetchers-ok', Fetcher.__name__, PlayWrightFetcher.__name__)"
)


@pytest.mark.parametrize("kind", ["http", "browser"])
def test_missing_scrapling_fails_loudly_instead_of_urllib_fallback(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "scrapling.fetchers":
            raise ModuleNotFoundError("No module named 'browserforge'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(FetchBackendUnavailableError) as err:
        LiveScraplingTransport().request("https://example.invalid/", fetcher_kind=kind)
    assert "browserforge" in str(err.value)


def test_runner_reports_fetch_failed_when_backend_unavailable(tmp_path: Path, test_hasher) -> None:
    from collector_stack.config import load_collector_config
    from collector_stack.runner import CollectorRunner

    class Broken(fetchers.FetchTransport):
        def request(self, url, **kw):
            raise FetchBackendUnavailableError("scrapling.PlayWrightFetcher", "ModuleNotFoundError")

    config = load_collector_config(COLLECTOR_DIR / "fetch-policy.json")
    runner = CollectorRunner(config, root_dir=tmp_path, hasher=test_hasher, transport=Broken())
    with pytest.raises(FetchBackendUnavailableError):
        runner.run_source("collector-tw-threads", run_id="r1", allow_disabled=True)
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["sources"]["collector-tw-threads"]["reason"] == "fetch_failed"


def _chromium_ready() -> str | None:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            if not Path(p.chromium.executable_path).exists():
                return "playwright chromium is not installed (run: python -m playwright install chromium)"
    except Exception as exc:  # pragma: no cover - environment without playwright
        return f"playwright unavailable: {exc!r}"
    return None


def test_browser_path_renders_script_inserted_element(tmp_path: Path) -> None:
    """Same code path as production (LiveScraplingTransport -> PlayWrightFetcher) against a local page."""
    reason = _chromium_ready()
    if reason:
        pytest.skip(reason)
    (tmp_path / "page.html").write_text(
        "<!doctype html><html><body><div id='root'></div>"
        "<script>document.getElementById('root').innerHTML="
        "'<p class=\"late\">rendered-by-javascript-7f3a</p>';</script></body></html>",
        encoding="utf-8",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/page.html"
        response = LiveScraplingTransport().request(url, fetcher_kind="browser")
    finally:
        server.shutdown()
    assert response.http_status == 200
    assert "rendered-by-javascript-7f3a" in response.text
    # the raw source holds only the script, so a plain HTTP fetch could never have produced this text
    assert "<p class=\"late\">" in response.text


def test_ndc_selects_indicator_csv_not_schema_csv(tmp_path: Path, test_hasher) -> None:
    from collector_stack.config import load_collector_config
    from collector_stack.runner import CollectorRunner

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.csv", "a,b\n1,2\n")
        zf.writestr("schema-景氣指標與燈號.csv", "欄位,說明\nDate,期間\n")
        zf.writestr(
            "景氣指標與燈號.csv",
            "Date,領先指標綜合指數,同時指標綜合指數,景氣對策信號綜合分數,景氣對策信號\n202609,143.2,143.9,41,紅\n",
        )
    meta = {"result": {"distribution": [{"resourceFormat": "ZIP", "resourceDownloadUrl": "https://ws.ndc.gov.tw/Download.ashx?u=x"}]}}
    transport = FixtureTransport()
    transport.register("https://data.gov.tw/api/v2/rest/dataset/6099", json.dumps(meta))
    transport.register("https://ws.ndc.gov.tw/Download.ashx?u=x", buf.getvalue())
    config = load_collector_config(COLLECTOR_DIR / "fetch-policy.json")
    runner = CollectorRunner(config, root_dir=tmp_path, hasher=test_hasher, transport=transport)
    outcome = runner.run_source("collector-tw-macro-ndc", run_id="ndc1")
    assert outcome.status == "complete"
    assert [c["url"] for c in transport.calls] == [
        "https://data.gov.tw/api/v2/rest/dataset/6099",
        "https://ws.ndc.gov.tw/Download.ashx?u=x",
    ]


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")
def test_built_image_imports_scrapling_fetchers() -> None:
    """Build the real image and import the fetcher classes inside it (slow; needs network + docker)."""
    import os

    if os.environ.get("COLLECTOR_IMAGE_TEST") != "1":
        pytest.skip("set COLLECTOR_IMAGE_TEST=1 to build the image (several minutes)")
    tag = "collector-import-check:test"
    subprocess.run(["docker", "build", "-t", tag, str(COLLECTOR_DIR)], check=True, timeout=3000)
    out = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "python", tag, "-c", IMPORT_CHECK],
        check=True, capture_output=True, text=True, timeout=300,
    )
    assert "fetchers-ok Fetcher PlayWrightFetcher" in out.stdout
    exe = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "python", tag, "-c",
         "from playwright.sync_api import sync_playwright as s\nwith s() as p: import os;print(os.path.exists(p.chromium.executable_path))"],
        check=True, capture_output=True, text=True, timeout=300,
    )
    assert exe.stdout.strip() == "True"

"""Collector raw verification over the guarded kept-alive client (fake transport, no wall clock)."""

from __future__ import annotations

import hashlib
import http.client
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

from services import external_egress as egress
from services.source_ingestion.connectors import collector_bundle as cb
from services.source_ingestion.connectors.base import SourceEvidenceError

HOST = "collector.example.test"
BASE = "bundles/src/2026-11-11/run1"


class FakeSock:
    def settimeout(self, value) -> None:  # noqa: ANN001
        self.timeout = value

    def shutdown(self, how) -> None:  # noqa: ANN001
        pass

    def close(self) -> None:
        pass


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, location: str | None = None) -> None:
        self.status, self.reason, self._body, self._pos = status, "OK", body, 0
        self.msg = {}
        self.headers = {"Content-Length": str(len(body))}
        if location:
            self.headers["Location"] = location
        self.will_close = False
        self._closed = False

    def read(self, n: int = -1) -> bytes:
        chunk = self._body[self._pos:] if n is None or n < 0 else self._body[self._pos:self._pos + n]
        self._pos += len(chunk)
        if self._pos >= len(self._body):
            self._closed = True
        return chunk

    read1 = read

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def isclosed(self) -> bool:
        return self._closed or not self._body

    def close(self) -> None:
        self._closed = True


class Server:
    """Shared fake collector + connection bookkeeping."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.lock = threading.Lock()
        self.opened = 0
        self.connects: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.paths: list[str] = []
        self.reset_next = 0
        self.redirect: dict[str, str] = {}
        self.closed = 0

    def factory(self, host, *, policy, timeout):  # noqa: ANN001
        return FakeConnection(self, host, policy)


class FakeConnection:
    def __init__(self, server: Server, host: str, policy) -> None:  # noqa: ANN001
        self.server, self.host, self._egress_policy, self.timeout = server, host, policy, 5.0
        self.sock = None
        self._target = ""
        self._used = False

    def _connect(self) -> None:  # stands in for the guarded connect(): counted per connection
        with self.server.lock:
            self.server.opened += 1
            self.server.connects.append(self.host)
        self.sock = FakeSock()

    def request(self, method: str, target: str, headers=None) -> None:  # noqa: ANN001
        s = self.server
        with s.lock:
            if s.reset_next and self._used:
                s.reset_next -= 1
                self.sock = None
                raise http.client.RemoteDisconnected("reset")
        if self.sock is None:
            self._connect()
        self._target = target
        self._used = True
        with s.lock:
            s.paths.append(target)
            s.in_flight += 1
            s.max_in_flight = max(s.max_in_flight, s.in_flight)

    def getresponse(self):
        s = self.server
        try:
            time.sleep(0)  # yield so overlap is observable without any threshold
            if self._target in s.redirect:
                return FakeResponse(b"", 302, s.redirect[self._target])
            body = s.files.get(self._target.lstrip("/"))
            return FakeResponse(body, 200) if body is not None else FakeResponse(b"no", 404)
        finally:
            with s.lock:
                s.in_flight -= 1

    def close(self) -> None:
        with self.server.lock:
            self.server.closed += 1
        self.sock = None


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _bundle(n: int, *, corrupt: int | None = None):
    files, items, manifest_items = {}, [], {}
    for i in range(n):
        body = f"row-{i}".encode()
        rid = f"item-{i}"
        files[f"{BASE}/raw/{i}.json"] = body if i != corrupt else b"tampered"
        items.append({"id": rid})
        manifest_items[rid] = {"item_id": rid, "raw_path": f"raw/{i}.json",
                               "content_sha256": _sha(body), "bytes": len(body)}
    bundle = cb.VerifiedBundle("src", "2026-11-11", "run1", datetime(2026, 11, 11, tzinfo=timezone.utc),
                               {}, tuple(items), manifest_items, {}, "k", source_bytes=0)
    return bundle, files


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.setenv(cb.COLLECTOR_BUNDLE_TOKEN_ENV, "tok")
    monkeypatch.setattr(egress, "guard_external_url", lambda *a, **k: a[0])
    holder = {}

    def make(files):
        srv = Server(files)
        holder["srv"] = srv

        class Client(egress.GuardedKeepAliveClient):
            def __init__(self, *a, **k):
                super().__init__(*a, connection_factory=srv.factory, **k)

        monkeypatch.setattr(cb, "GuardedKeepAliveClient", Client)
        return srv

    return make


def _verify(bundle, *, width, deadline=None, window=None):
    t = cb.HttpBundleTransport(host=HOST)
    return cb._verify_raw_window(t, bundle, item_window=window or (0, len(bundle.items)),
                                 deadline=deadline, raw_read_concurrency=width)


def test_connections_and_in_flight_never_exceed_setting(server, monkeypatch) -> None:
    monkeypatch.setattr(cb, "COLLECTOR_RAW_READ_CONNECTIONS_PER_PULL", 3)
    bundle, files = _bundle(40)
    srv = server(files)
    out = _verify(bundle, width=16)
    assert len(out.raw_bytes) == 40 and out.verified_through is None
    assert 1 <= srv.opened <= 3
    assert srv.max_in_flight <= 3
    assert srv.closed == srv.opened  # every connection closed at the end of the window
    assert set(srv.connects) == {HOST}


def test_paths_stay_under_bundle_root_and_unsafe_raw_path_is_refused(server) -> None:
    bundle, files = _bundle(3)
    srv = server(files)
    _verify(bundle, width=4)
    assert all(p.startswith(f"/{BASE}/raw/") for p in srv.paths)
    bad = cb.VerifiedBundle("src", "2026-11-11", "run1", bundle.finished_at, {}, bundle.items,
                            {k: {**v, "raw_path": "../../etc/x"} for k, v in bundle.manifest_items.items()},
                            {}, "k", source_bytes=0)
    with pytest.raises(SourceEvidenceError):
        _verify(bad, width=4)


def test_changed_host_is_refused_by_the_client(monkeypatch) -> None:
    monkeypatch.setattr(egress, "guard_external_url", lambda *a, **k: a[0])
    client = egress.GuardedKeepAliveClient(HOST, caller="t", max_connections=2, timeout=1.0,
                                           connection_factory=Server({}).factory)
    for url in (f"https://evil.example.test/{BASE}/a", f"http://{HOST}/a", f"https://{HOST}:8443/a",
                f"https://user:pw@{HOST}/a"):
        with pytest.raises(egress.ExternalEgressBlocked) as err:
            client.open(url)
        assert err.value.reason_code == "keepalive_host_mismatch"
    client.close()


def test_hash_mismatch_fails_as_before(server) -> None:
    bundle, files = _bundle(6, corrupt=4)
    server(files)
    with pytest.raises(SourceEvidenceError, match="raw hash mismatch for item item-4"):
        _verify(bundle, width=4)


def test_budget_cut_leaves_same_prefix_as_sequential(server, monkeypatch) -> None:
    bundle, files = _bundle(12)
    order = [r["id"] for r in bundle.items]
    seen = {}
    for width in (1, 4):
        srv = server(files)
        tick = {"t": 0.0}

        # Logical clock: one unit per served raw read; no wall clock is consulted.
        real_get = FakeConnection.getresponse

        def getresponse(self, real_get=real_get, tick=tick):
            tick["t"] += 1.0
            return real_get(self)

        monkeypatch.setattr(FakeConnection, "getresponse", getresponse)
        monkeypatch.setattr(cb.time, "monotonic", lambda tick=tick: tick["t"])
        monkeypatch.setattr(egress.time, "monotonic", lambda tick=tick: tick["t"])
        out = _verify(bundle, width=width, deadline=4.0)
        monkeypatch.undo()
        monkeypatch.setenv(cb.COLLECTOR_BUNDLE_TOKEN_ENV, "tok")
        monkeypatch.setattr(egress, "guard_external_url", lambda *a, **k: a[0])
        through = out.verified_through
        assert through == 4
        # raw bodies verified are exactly the contiguous prefix: the cursor can never skip a hole.
        seen[width] = (through, sorted(out.raw_bytes))
    assert seen[1] == seen[4]
    assert len(seen[1][1]) == 4 and order[:4]


def test_reset_mid_pull_reconnects_through_the_guard(server) -> None:
    bundle, files = _bundle(10)
    srv = server(files)
    srv.reset_next = 2
    out = _verify(bundle, width=1)
    assert len(out.raw_bytes) == 10
    assert srv.opened == 3  # initial + one reconnect per reset (each connect() re-runs the guard)


def test_http_error_matches_urlopen_shape(server) -> None:
    bundle, files = _bundle(2)
    del files[f"{BASE}/raw/1.json"]
    server(files)
    with pytest.raises(urllib.error.HTTPError) as err:
        _verify(bundle, width=2)
    assert err.value.code == 404


def test_redirect_goes_through_existing_open_external_url(server, monkeypatch) -> None:
    bundle, files = _bundle(1)
    srv = server(files)
    srv.redirect[f"/{BASE}/raw/0.json"] = "https://elsewhere.example.test/x"
    seen = []

    def fake_open(request, **kw):
        seen.append((request.full_url, kw["max_redirects"]))
        return FakeResponse(b"row-0")

    monkeypatch.setattr(egress, "open_external_url", fake_open)
    out = _verify(bundle, width=1)
    assert seen and out.raw_bytes


def test_no_client_outlives_the_window(server) -> None:
    bundle, files = _bundle(5)
    server(files)
    _verify(bundle, width=4)
    assert cb._KEEPALIVE_CLIENT.get() is None

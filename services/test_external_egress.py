from __future__ import annotations

import socket
import ssl
import urllib.request

import pytest

from services.external_egress import (
    ExternalEgressBlocked,
    _GuardedRedirectHandler,
    _PinnedHTTPSConnection,
    _RedirectPolicy,
    allowed_hosts,
    external_egress_allowed,
    guard_external_url,
    is_internal_host,
    open_external_url,
    resolve_mode,
)


def _resolver(*addresses: str):
    def resolve(host: str, port: int, *, type: int):
        assert host
        assert port == 443
        assert type == socket.SOCK_STREAM
        family = socket.AF_INET6 if any(":" in address for address in addresses) else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (address, port)) for address in addresses]

    return resolve


def _allowlist(*hosts: str) -> dict[str, str]:
    return {
        "PANTHEON_EXTERNAL_EGRESS": "allowlist",
        "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS": ",".join(hosts),
    }


def test_every_environment_defaults_to_deny() -> None:
    assert resolve_mode({}) == "deny"
    assert resolve_mode({"PANTHEON_ENV": "dev"}) == "deny"
    assert resolve_mode({"PANTHEON_ENV": "production"}) == "deny"


def test_only_deny_and_allowlist_modes_are_valid() -> None:
    assert resolve_mode({"PANTHEON_EXTERNAL_EGRESS": "allowlist"}) == "allowlist"
    with pytest.raises(ValueError):
        resolve_mode({"PANTHEON_EXTERNAL_EGRESS": "allow"})


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "::1", "10.50.0.21", "192.168.1.4", "source-ingest", "vm.internal"],
)
def test_internal_hosts_are_recognized(host: str) -> None:
    assert is_internal_host(host) is True


@pytest.mark.parametrize("host", ["openapi.twse.com.tw", "api.coingecko.com", "8.8.8.8"])
def test_public_hosts_are_not_intrinsically_internal(host: str) -> None:
    assert is_internal_host(host) is False


def test_external_boundary_rejects_internal_http_and_private_targets() -> None:
    env = _allowlist("source-ingest", "private.example")
    with pytest.raises(ExternalEgressBlocked) as http_error:
        guard_external_url("http://source-ingest:8097/health", caller="test", env=env)
    assert http_error.value.reason_code == "https_required"

    with pytest.raises(ExternalEgressBlocked) as private_error:
        guard_external_url(
            "https://private.example/data",
            caller="test",
            env=env,
            resolver=_resolver("10.50.0.21"),
        )
    assert private_error.value.reason_code == "target_ip_forbidden"
    assert private_error.value.resolved_addresses == ("10.50.0.21",)


def test_allowlist_requires_exact_https_host_and_global_dns() -> None:
    env = _allowlist("openapi.twse.com.tw", "api.finmindtrade.com")
    assert allowed_hosts(env) == frozenset({"openapi.twse.com.tw", "api.finmindtrade.com"})
    assert external_egress_allowed("https://openapi.twse.com.tw/v1/exchangeReport", env)
    assert guard_external_url(
        "https://openapi.twse.com.tw/v1/exchangeReport",
        caller="test",
        env=env,
        resolver=_resolver("8.8.8.8"),
    ).startswith("https://")

    for url in (
        "https://twse.com.tw/data",
        "https://sub.openapi.twse.com.tw/data",
        "https://eviltwse.com.tw/data",
        "http://openapi.twse.com.tw/data",
    ):
        with pytest.raises(ExternalEgressBlocked):
            guard_external_url(url, caller="test", env=env, resolver=_resolver("8.8.8.8"))


def test_mixed_global_and_private_dns_answers_fail_closed() -> None:
    with pytest.raises(ExternalEgressBlocked) as excinfo:
        guard_external_url(
            "https://api.example.com/data",
            caller="test",
            env=_allowlist("api.example.com"),
            resolver=_resolver("8.8.8.8", "127.0.0.1"),
        )
    assert excinfo.value.reason_code == "target_ip_forbidden"
    assert set(excinfo.value.resolved_addresses) == {"8.8.8.8", "127.0.0.1"}


def test_dns_failure_is_a_typed_denial() -> None:
    def failed_resolver(*args, **kwargs):
        raise socket.gaierror("not found")

    with pytest.raises(ExternalEgressBlocked) as excinfo:
        guard_external_url(
            "https://api.example.com/data",
            caller="test",
            env=_allowlist("api.example.com"),
            resolver=failed_resolver,
        )
    assert excinfo.value.reason_code == "dns_resolution_failed"


def test_redirect_target_is_allowlisted_and_dns_revalidated() -> None:
    calls: list[str] = []

    def resolver(host: str, port: int, *, type: int):
        calls.append(host)
        address = "8.8.8.8" if host == "api.example.com" else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    policy = _RedirectPolicy(
        caller="test",
        env=_allowlist("api.example.com", "cdn.example.com"),
        resolver=resolver,
        max_redirects=3,
    )
    handler = _GuardedRedirectHandler(policy)
    request = urllib.request.Request("https://api.example.com/start")
    with pytest.raises(ExternalEgressBlocked) as excinfo:
        handler.redirect_request(request, None, 302, "Found", {}, "https://cdn.example.com/object")
    assert excinfo.value.reason_code == "target_ip_forbidden"
    assert calls == ["cdn.example.com"]


def test_same_origin_redirect_preserves_credential_headers() -> None:
    handler = _GuardedRedirectHandler(
        _RedirectPolicy(
            caller="test",
            env=_allowlist("api.example.com"),
            resolver=_resolver("8.8.8.8"),
            max_redirects=3,
        )
    )
    request = urllib.request.Request(
        "https://api.example.com/start",
        headers={"Authorization": "Bearer secret", "X-Api-Key": "api-secret"},
    )

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://api.example.com/next",
    )

    assert redirected is not None
    assert redirected.get_header("Authorization") == "Bearer secret"
    assert redirected.get_header("X-api-key") == "api-secret"


def test_cross_origin_redirect_strips_credential_headers() -> None:
    handler = _GuardedRedirectHandler(
        _RedirectPolicy(
            caller="test",
            env=_allowlist("api.example.com", "cdn.example.com"),
            resolver=_resolver("8.8.8.8"),
            max_redirects=3,
        )
    )
    request = urllib.request.Request(
        "https://api.example.com/start",
        headers={
            "Authorization": "Bearer secret",
            "Proxy-Authorization": "Basic secret",
            "Cookie": "session=secret",
            "X-Api-Key": "api-secret",
            "X-Access-Token": "token-secret",
            "Accept": "application/json",
        },
    )

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://cdn.example.com/object",
    )

    assert redirected is not None
    assert redirected.get_header("Authorization") is None
    assert redirected.get_header("Proxy-authorization") is None
    assert redirected.get_header("Cookie") is None
    assert redirected.get_header("X-api-key") is None
    assert redirected.get_header("X-access-token") is None
    assert redirected.get_header("Accept") == "application/json"


def test_redirect_escape_is_blocked_before_dns_or_secret_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    transport_called = False

    def redirect_transport(*args, **kwargs):
        nonlocal transport_called
        transport_called = True
        raise AssertionError("blocked redirect must not construct a secret-bearing request")

    monkeypatch.setattr(urllib.request.HTTPRedirectHandler, "redirect_request", redirect_transport)

    def resolver(host: str, port: int, *, type: int):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]

    handler = _GuardedRedirectHandler(
        _RedirectPolicy(
            caller="test",
            env=_allowlist("api.example.com"),
            resolver=resolver,
            max_redirects=3,
        )
    )
    with pytest.raises(ExternalEgressBlocked) as excinfo:
        handler.redirect_request(
            urllib.request.Request(
                "https://api.example.com/start",
                headers={"Authorization": "Bearer secret", "X-Api-Key": "api-secret"},
            ),
            None,
            302,
            "Found",
            {},
            "https://attacker.example/escape",
        )
    assert excinfo.value.reason_code == "host_not_allowlisted"
    assert calls == []
    assert transport_called is False


def test_open_external_url_denies_before_building_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    built = False

    def build_opener(*handlers):
        nonlocal built
        built = True
        raise AssertionError("transport must not be built")

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    with pytest.raises(ExternalEgressBlocked):
        open_external_url(
            "https://blocked.example/data?token=secret",
            caller="test",
            timeout=1,
            env=_allowlist("api.example.com"),
            resolver=_resolver("8.8.8.8"),
        )
    assert built is False


def test_https_transport_pins_connect_to_revalidated_global_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    connected: list[tuple[tuple[str, int], float, object]] = []
    wrapped: list[tuple[object, str]] = []
    raw_socket = object()

    def create_connection(target, timeout, source_address):
        connected.append((target, timeout, source_address))
        return raw_socket

    class FakeContext:
        check_hostname = False
        verify_mode = ssl.CERT_NONE

        def wrap_socket(self, sock, *, server_hostname):
            wrapped.append((sock, server_hostname))
            return sock

    monkeypatch.setattr(socket, "create_connection", create_connection)
    connection = _PinnedHTTPSConnection(
        "api.example.com",
        policy=_RedirectPolicy(
            caller="test",
            env=_allowlist("api.example.com"),
            resolver=_resolver("8.8.8.8"),
            max_redirects=3,
        ),
        timeout=2,
        context=FakeContext(),
    )

    connection.connect()

    assert connected == [(('8.8.8.8', 443), 2, None)]
    assert wrapped == [(raw_socket, "api.example.com")]


def test_denial_message_redacts_query_values() -> None:
    with pytest.raises(ExternalEgressBlocked) as excinfo:
        guard_external_url(
            "https://blocked.example/data?token=secret-value",
            caller="test",
            env={},
        )
    assert "secret-value" not in str(excinfo.value)
    assert excinfo.value.url.endswith("?<redacted>")


def test_every_production_connector_uses_guarded_transport() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    paths = [
        *sorted((root / "services/source_ingestion/connectors").glob("*.py")),
        *sorted((root / "services/research/adapters").glob("*_client.py")),
    ]
    offenders: list[str] = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        if "urlopen(" in source:
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
    configured = (root / "services/source_ingestion/configured.py").read_text(encoding="utf-8")
    assert "return open_external_url(request, caller=caller, timeout=timeout)" in configured
    assert 'network_scope == "internal_service"' in configured
    assert "_InternalServiceRedirectHandler" in configured


def test_connector_fetch_helpers_are_denied_before_outbound_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.source_ingestion import configured
    from services.source_ingestion.connectors import crypto_coingecko, us_public

    monkeypatch.delenv("PANTHEON_EXTERNAL_EGRESS", raising=False)
    monkeypatch.delenv("PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS", raising=False)

    with pytest.raises(ExternalEgressBlocked):
        crypto_coingecko._request_json("https://api.coingecko.com/api/v3/coins/markets")
    with pytest.raises(ExternalEgressBlocked):
        us_public._request_json("https://data.sec.gov/submissions/CIK0000320193.json", user_agent="test")
    with pytest.raises(ExternalEgressBlocked):
        us_public._request_text("https://stooq.com/q/d/l/?s=spy.us")
    with pytest.raises(ExternalEgressBlocked):
        configured._assert_robots_allowed("https://stooq.com/q/d/l/", ("https://stooq.com/",), 5.0)


def _compose_services() -> dict:
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1]
    return yaml.safe_load((root / "docker-compose.yml").read_text())["services"]


def test_only_source_ingest_may_use_collector_host_steady_egress() -> None:
    """DEV-EGRESS-COLLECTOR-HOST-GATE-20261110: every other service keeps deny."""

    services = _compose_services()
    steady = services["source-ingest"]["environment"]
    assert steady["PANTHEON_EXTERNAL_EGRESS"] == "${PANTHEON_EXTERNAL_EGRESS:-${PANTHEON_COLLECTOR_HOST:+allowlist}}"
    assert steady["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == (
        "${PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS:-${PANTHEON_COLLECTOR_HOST:-}}"
    )
    for name, service in services.items():
        if name == "source-ingest":
            continue
        env = service.get("environment") or {}
        if isinstance(env, list):
            env = dict(item.split("=", 1) for item in env if "=" in item)
        if "PANTHEON_EXTERNAL_EGRESS" in env:
            assert env["PANTHEON_EXTERNAL_EGRESS"] == "deny", name
            assert not env.get("PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"), name


def test_unset_collector_host_leaves_steady_mode_deny() -> None:
    assert resolve_mode({"PANTHEON_EXTERNAL_EGRESS": ""}) == "deny"


def _stalled_listener():
    import threading

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    held: list[socket.socket] = []

    def accept() -> None:
        try:
            while True:
                conn, _ = server.accept()
                held.append(conn)
        except OSError:
            return

    threading.Thread(target=accept, daemon=True).start()
    return server, held


def test_operation_deadline_bounds_a_stalled_tls_open(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    server, held = _stalled_listener()
    real_connect = socket.create_connection
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda target, timeout, source_address=None: real_connect(
            ("127.0.0.1", server.getsockname()[1]), timeout, source_address
        ),
    )
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            # per-recv timeout (30s) alone would wait; the whole-operation deadline must not.
            open_external_url(
                "https://api.example.com/x",
                caller="t",
                timeout=30.0,
                env=_allowlist("api.example.com"),
                resolver=_resolver("8.8.8.8"),
                deadline=time.monotonic() + 0.4,
            )
    finally:
        server.close()
        for conn in held:
            conn.close()
    assert time.monotonic() - started < 5.0


def test_deadline_watchdog_cuts_dribbled_headers_and_chunk_framing() -> None:
    import http.client
    import threading
    import time

    from services.external_egress import _DeadlineWatchdog

    for payload in (
        b"HTTP/1.1 200 OK\r\nX-A: 1\r\n",  # headers never finish
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nab",  # chunk body stalls
    ):
        client, peer = socket.socketpair()
        peer.sendall(payload)
        watchdog = _DeadlineWatchdog(time.monotonic() + 0.3)
        watchdog.track(client)
        watchdog.start()
        started = time.monotonic()
        # keep dribbling bytes so a per-recv timeout would never fire
        stop = threading.Event()

        def dribble() -> None:
            while not stop.is_set():
                try:
                    peer.sendall(b" ")
                except OSError:
                    return
                time.sleep(0.05)

        threading.Thread(target=dribble, daemon=True).start()
        response = http.client.HTTPResponse(client)
        try:
            from services.external_egress import _WatchedResponse

            with pytest.raises(TimeoutError):
                # a deadline close may read as clean EOF; it must still never be data
                response.begin()
                _WatchedResponse(response, watchdog).read()
        finally:
            stop.set()
            watchdog.stop()
            peer.close()
        assert watchdog.expired
        assert time.monotonic() - started < 5.0

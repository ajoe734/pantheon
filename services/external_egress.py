"""Fail-closed HTTPS egress for third-party Pantheon connectors.

Connectors must use :func:`open_external_url` instead of calling ``urlopen``
directly.  The policy is deliberately small:

* every environment defaults to ``deny``;
* the only enabled mode is ``allowlist`` with exact HTTPS host names;
* loopback, private, link-local, reserved, and otherwise non-global addresses
  are rejected, including addresses returned by DNS;
* every redirect is allowlist-checked and DNS-resolved again before it is
  followed.

Internal service-to-service HTTP is outside this module.  It continues to use
the service's ordinary HTTP client and must never be routed through the
external connector boundary.
"""

from __future__ import annotations

import ipaddress
import http.client
import os
import socket
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit


MODE_ENV_VAR = "PANTHEON_EXTERNAL_EGRESS"
ALLOWED_HOSTS_ENV_VAR = "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"

MODE_ALLOWLIST = "allowlist"
MODE_DENY = "deny"
VALID_MODES = (MODE_ALLOWLIST, MODE_DENY)
_INTERNAL_HOST_SUFFIXES = (".internal", ".local", ".localdomain", ".svc", ".cluster.local")
_SENSITIVE_REDIRECT_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "cookie2",
        "token",
    }
)

DNSResolver = Callable[..., Sequence[tuple[Any, ...]]]


def _clean(value: object) -> str:
    return str(value or "").strip()


def _safe_url(url: str) -> str:
    """Return a log-safe URL without userinfo, query values, or fragment."""

    parsed = urlsplit(str(url or ""))
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    query = "<redacted>" if parsed.query else ""
    return urlunsplit((parsed.scheme, host, parsed.path, query, ""))


class ExternalEgressBlocked(RuntimeError):
    """Typed refusal raised before a forbidden outbound request is sent."""

    def __init__(
        self,
        *,
        url: str,
        host: str,
        mode: str,
        caller: str,
        reason_code: str,
        detail: str,
        resolved_addresses: Sequence[str] = (),
    ) -> None:
        self.url = _safe_url(url)
        self.host = host
        self.mode = mode
        self.caller = caller
        self.reason_code = reason_code
        self.resolved_addresses = tuple(resolved_addresses)
        super().__init__(
            f"external egress denied: code={reason_code} host={host!r} "
            f"mode={mode} caller={caller} detail={detail} url={self.url}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "external_egress_denial.v1",
            "code": self.reason_code,
            "host": self.host,
            "mode": self.mode,
            "caller": self.caller,
            "url": self.url,
            "resolved_addresses": list(self.resolved_addresses),
        }


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def resolve_mode(env: Mapping[str, str] | None = None) -> str:
    """Return the configured mode; unset means deny in every environment."""

    configured = _clean(_env(env).get(MODE_ENV_VAR)).lower() or MODE_DENY
    if configured not in VALID_MODES:
        raise ValueError(f"{MODE_ENV_VAR} must be one of {', '.join(VALID_MODES)}; got {configured!r}")
    return configured


def _normalize_host(value: str) -> str:
    candidate = _clean(value).lower().rstrip(".")
    if not candidate:
        raise ValueError(f"{ALLOWED_HOSTS_ENV_VAR} contains an empty host")
    if any(token in candidate for token in ("://", "/", "?", "#", "@", ":", "*")):
        raise ValueError(f"{ALLOWED_HOSTS_ENV_VAR} entries must be exact host names: {candidate!r}")
    try:
        ipaddress.ip_address(candidate.strip("[]"))
    except ValueError:
        pass
    else:
        raise ValueError(f"{ALLOWED_HOSTS_ENV_VAR} must not contain IP literals: {candidate!r}")
    try:
        return candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"{ALLOWED_HOSTS_ENV_VAR} contains an invalid host: {candidate!r}") from exc


def allowed_hosts(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """Return the validated exact-host allowlist."""

    raw = _clean(_env(env).get(ALLOWED_HOSTS_ENV_VAR))
    if not raw:
        return frozenset()
    return frozenset(_normalize_host(entry) for entry in raw.split(","))


def is_internal_host(host: str) -> bool:
    """Return whether a host is intrinsically unsafe for external egress."""

    candidate = _clean(host).lower().strip("[]").rstrip(".")
    if not candidate:
        return True
    if candidate == "localhost" or candidate.endswith(".localhost") or "." not in candidate:
        return True
    if candidate.endswith(_INTERNAL_HOST_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return not address.is_global


def _syntactic_policy_allows(url: str, env: Mapping[str, str] | None) -> bool:
    try:
        parsed = urlsplit(url)
        host = _normalize_host(parsed.hostname or "")
    except (TypeError, ValueError):
        return False
    if parsed.scheme.lower() != "https" or parsed.username or parsed.password:
        return False
    if parsed.port not in (None, 443) or is_internal_host(host):
        return False
    return resolve_mode(env) == MODE_ALLOWLIST and host in allowed_hosts(env)


def external_egress_allowed(url: str, env: Mapping[str, str] | None = None) -> bool:
    """Return the host/scheme policy decision without performing DNS I/O."""

    return _syntactic_policy_allows(url, env)


def _blocked(
    *,
    url: str,
    caller: str,
    env: Mapping[str, str] | None,
    reason_code: str,
    detail: str,
    resolved_addresses: Sequence[str] = (),
) -> ExternalEgressBlocked:
    parsed = urlsplit(str(url or ""))
    return ExternalEgressBlocked(
        url=url,
        host=_clean(parsed.hostname).lower(),
        mode=resolve_mode(env),
        caller=caller,
        reason_code=reason_code,
        detail=detail,
        resolved_addresses=resolved_addresses,
    )


def _resolve_global_addresses(host: str, *, resolver: DNSResolver) -> tuple[str, ...]:
    try:
        answers = resolver(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise _DNSResolutionError(str(exc)) from exc
    addresses = sorted({_clean(answer[4][0]).split("%", 1)[0] for answer in answers if len(answer) >= 5})
    if not addresses:
        raise _DNSResolutionError("resolver returned no addresses")
    invalid: list[str] = []
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            invalid.append(address)
            continue
        if not parsed.is_global:
            invalid.append(address)
    if invalid:
        raise _UnsafeAddressError(tuple(addresses), tuple(invalid))
    return tuple(addresses)


class _DNSResolutionError(RuntimeError):
    pass


class _UnsafeAddressError(RuntimeError):
    def __init__(self, addresses: tuple[str, ...], invalid: tuple[str, ...]) -> None:
        self.addresses = addresses
        self.invalid = invalid
        super().__init__(f"DNS returned non-global addresses: {', '.join(invalid)}")


def guard_external_url(
    url: str,
    *,
    caller: str,
    env: Mapping[str, str] | None = None,
    resolver: DNSResolver = socket.getaddrinfo,
) -> str:
    """Validate scheme, exact host allowlist, and all resolved addresses."""

    parsed = urlsplit(str(url or ""))
    host = _clean(parsed.hostname).lower().rstrip(".")
    if parsed.scheme.lower() != "https":
        raise _blocked(
            url=url,
            caller=caller,
            env=env,
            reason_code="https_required",
            detail="external connectors require HTTPS",
        )
    if parsed.username or parsed.password:
        raise _blocked(
            url=url,
            caller=caller,
            env=env,
            reason_code="inline_credentials_forbidden",
            detail="URL userinfo is forbidden",
        )
    if parsed.port not in (None, 443):
        raise _blocked(
            url=url,
            caller=caller,
            env=env,
            reason_code="port_forbidden",
            detail="only HTTPS port 443 is permitted",
        )
    try:
        normalized_host = _normalize_host(host)
    except ValueError as exc:
        raise _blocked(
            url=url,
            caller=caller,
            env=env,
            reason_code="host_invalid",
            detail=str(exc),
        ) from exc
    mode = resolve_mode(env)
    if mode != MODE_ALLOWLIST or normalized_host not in allowed_hosts(env):
        raise _blocked(
            url=url,
            caller=caller,
            env=env,
            reason_code="host_not_allowlisted",
            detail=f"set {MODE_ENV_VAR}=allowlist and add the exact host to {ALLOWED_HOSTS_ENV_VAR}",
        )
    if is_internal_host(normalized_host):
        raise _blocked(
            url=url,
            caller=caller,
            env=env,
            reason_code="target_host_forbidden",
            detail="loopback, single-label, and IP-literal targets are forbidden",
        )
    try:
        _resolve_global_addresses(normalized_host, resolver=resolver)
    except _UnsafeAddressError as exc:
        raise _blocked(
            url=url,
            caller=caller,
            env=env,
            reason_code="target_ip_forbidden",
            detail="all DNS answers must be globally routable",
            resolved_addresses=exc.addresses,
        ) from exc
    except _DNSResolutionError as exc:
        raise _blocked(
            url=url,
            caller=caller,
            env=env,
            reason_code="dns_resolution_failed",
            detail=str(exc),
        ) from exc
    return url


@dataclass(frozen=True)
class _RedirectPolicy:
    caller: str
    env: Mapping[str, str] | None
    resolver: DNSResolver
    max_redirects: int


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Revalidate the absolute target before urllib follows a redirect."""

    def __init__(self, policy: _RedirectPolicy) -> None:
        super().__init__()
        self.policy = policy
        self.redirect_count = 0

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> urllib.request.Request | None:
        self.redirect_count += 1
        absolute_url = urljoin(req.full_url, newurl)
        if self.redirect_count > self.policy.max_redirects:
            raise _blocked(
                url=absolute_url,
                caller=self.policy.caller,
                env=self.policy.env,
                reason_code="redirect_limit_exceeded",
                detail=f"more than {self.policy.max_redirects} redirects",
            )
        guard_external_url(
            absolute_url,
            caller=self.policy.caller,
            env=self.policy.env,
            resolver=self.policy.resolver,
        )
        redirected = super().redirect_request(req, fp, code, msg, headers, absolute_url)
        if redirected is not None and _request_origin(req.full_url) != _request_origin(absolute_url):
            _strip_cross_origin_credentials(redirected)
        return redirected


def _request_origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    host = _clean(parsed.hostname).lower().rstrip(".")
    port = parsed.port if parsed.port is not None else (443 if scheme == "https" else None)
    return scheme, host, port


def _is_sensitive_redirect_header(name: str) -> bool:
    normalized = str(name or "").strip().lower().replace("_", "-")
    return (
        normalized in _SENSITIVE_REDIRECT_HEADERS
        or "api-key" in normalized
        or "apikey" in normalized
        or normalized.endswith("-token")
    )


def _strip_cross_origin_credentials(request: urllib.request.Request) -> None:
    header_names = set(request.headers) | set(request.unredirected_hdrs)
    for header_name in header_names:
        if _is_sensitive_redirect_header(header_name):
            request.remove_header(header_name)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect TLS to a policy-validated IP while preserving hostname SNI."""

    def __init__(self, host: str, *, policy: _RedirectPolicy, **kwargs: Any) -> None:
        self._egress_policy = policy
        super().__init__(host, **kwargs)

    def connect(self) -> None:
        if self._tunnel_host:
            raise _blocked(
                url=f"https://{self.host}/",
                caller=self._egress_policy.caller,
                env=self._egress_policy.env,
                reason_code="proxy_tunnel_forbidden",
                detail="external connector proxy tunnels are not supported by the pinned transport",
            )
        url = f"https://{self.host}/"
        # Revalidate immediately before opening the socket. The socket then
        # targets the returned IP literal, so the transport cannot perform an
        # unguarded DNS lookup after this decision.
        try:
            addresses = _resolve_global_addresses(self.host, resolver=self._egress_policy.resolver)
        except _UnsafeAddressError as exc:
            raise _blocked(
                url=url,
                caller=self._egress_policy.caller,
                env=self._egress_policy.env,
                reason_code="target_ip_forbidden",
                detail="all DNS answers must be globally routable at connect time",
                resolved_addresses=exc.addresses,
            ) from exc
        except _DNSResolutionError as exc:
            raise _blocked(
                url=url,
                caller=self._egress_policy.caller,
                env=self._egress_policy.env,
                reason_code="dns_resolution_failed",
                detail=str(exc),
            ) from exc

        last_error: OSError | None = None
        for address in addresses:
            try:
                self.sock = socket.create_connection(
                    (address, self.port),
                    self.timeout,
                    self.source_address,
                )
                break
            except OSError as exc:
                last_error = exc
        else:
            assert last_error is not None
            raise last_error
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, policy: _RedirectPolicy) -> None:
        super().__init__()
        self.policy = policy

    def https_open(self, req: urllib.request.Request) -> Any:
        def connection_factory(host: str, **kwargs: Any) -> _PinnedHTTPSConnection:
            return _PinnedHTTPSConnection(host, policy=self.policy, **kwargs)

        return self.do_open(connection_factory, req)


def open_external_url(
    request: str | urllib.request.Request,
    *,
    caller: str,
    timeout: float,
    env: Mapping[str, str] | None = None,
    resolver: DNSResolver = socket.getaddrinfo,
    max_redirects: int = 3,
) -> Any:
    """Open an external HTTPS request through the guarded redirect client."""

    if max_redirects < 0 or max_redirects > 10:
        raise ValueError("max_redirects must be between 0 and 10")
    url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
    guard_external_url(url, caller=caller, env=env, resolver=resolver)
    policy = _RedirectPolicy(caller=caller, env=env, resolver=resolver, max_redirects=max_redirects)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _GuardedRedirectHandler(policy),
        _PinnedHTTPSHandler(policy),
    )
    return opener.open(request, timeout=timeout)

"""Shared helpers for Pantheon runtime-to-runtime bearer auth."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from services.runtime_auth_inbound import _looks_like_jwt, encode_jwt_hs256


_TOKEN_ENV = "PANTHEON_RUNTIME_MANAGER_TOKEN"
_TOKEN_FILE_ENV = "PANTHEON_RUNTIME_MANAGER_TOKEN_FILE"
_DEFAULT_TOKEN = "runtime-control-internal"
_DEFAULT_ROLE = "operator"
_DEFAULT_JWT_TTL_SECONDS = 120
_MAX_JWT_TTL_SECONDS = 604800

_JWT_SECRET_ENV_KEYS = (
    "PANTHEON_RUNTIME_MANAGER_JWT_SECRET",
    "PANTHEON_RUNTIME_JWT_SECRET",
)
_JWT_ISSUER_ENV_KEYS = (
    "PANTHEON_RUNTIME_MANAGER_JWT_ISSUER",
    "PANTHEON_RUNTIME_JWT_ISSUER",
)
_JWT_AUDIENCE_ENV_KEYS = (
    "PANTHEON_RUNTIME_MANAGER_JWT_AUDIENCE",
    "PANTHEON_RUNTIME_JWT_AUDIENCE",
)
_ACTOR_ENV_KEYS = (
    "PANTHEON_RUNTIME_MANAGER_ACTOR_ID",
    "PANTHEON_RUNTIME_MANAGER_SUBJECT",
    "PANTHEON_RUNTIME_ACTOR_ID",
    "PANTHEON_SERVICE_ACTOR_ID",
)
_ROLE_ENV_KEYS = (
    "PANTHEON_RUNTIME_MANAGER_ROLES",
    "PANTHEON_RUNTIME_MANAGER_ROLE",
    "PANTHEON_RUNTIME_ROLES",
)
_TENANT_ENV_KEYS = (
    "PANTHEON_RUNTIME_MANAGER_TENANT_ID",
    "PANTHEON_DEPLOYMENT_TENANT_ID",
    "PANTHEON_TENANT_ID",
    "PANTHEON_BFF_TENANT_ID",
    "PANTHEON_DEV_BFF_TENANT_ID",
)
_TTL_ENV_KEYS = (
    "PANTHEON_RUNTIME_MANAGER_JWT_TTL_SECONDS",
    "PANTHEON_RUNTIME_JWT_TTL_SECONDS",
)


class _BoundAuthToken(str):
    """Bearer token string retaining a back-reference to its dynamic config."""

    _auth_config: "RuntimeManagerAuthConfig | None" = None

    def __new__(
        cls,
        value: str,
        auth_config: "RuntimeManagerAuthConfig | None" = None,
    ) -> "_BoundAuthToken":
        obj = super().__new__(cls, value)
        obj._auth_config = auth_config
        return obj


def _clean_env_value(raw: object) -> str:
    val = str(raw or "").strip()
    if val.startswith("${") and val.endswith("}"):
        return ""
    return val


def _first_env(env_map: Mapping[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        val = _clean_env_value(env_map.get(key, ""))
        if val:
            return val
    return ""


def _split_roles(raw: str) -> tuple[str, ...]:
    parts = tuple(part.strip() for part in raw.split(",") if part.strip())
    return parts


def _decode_jwt_payload_unverified(token: str) -> dict[str, Any]:
    if not token or not _looks_like_jwt(token):
        return {}
    try:
        payload_b64 = token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    except Exception:
        return {}


def _extract_jwt_exp(token: str) -> float | None:
    payload = _decode_jwt_payload_unverified(token)
    exp = payload.get("exp")
    if exp is None or isinstance(exp, bool):
        return None
    try:
        return float(exp)
    except (TypeError, ValueError):
        return None


def _parse_structured_actor_roles(raw_token: str) -> tuple[str, tuple[str, ...]]:
    if not raw_token or ":" not in raw_token or _looks_like_jwt(raw_token):
        return ("", ())
    parts = raw_token.split(":")
    actor = parts[0].strip()
    roles = _split_roles(parts[1]) if len(parts) >= 2 else ()
    return (actor, roles)


def _parse_ttl_seconds(raw: str) -> int:
    if not raw:
        return _DEFAULT_JWT_TTL_SECONDS
    try:
        parsed = int(float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_JWT_TTL_SECONDS
    return max(1, min(parsed, _MAX_JWT_TTL_SECONDS))


def _read_token_file(raw_path: str) -> str:
    path = Path(raw_path).expanduser()
    return path.read_text(encoding="utf-8").strip()


class RuntimeManagerAuthConfig:
    """Resolved runtime-manager bearer auth config with dynamic JWT/file refresh."""

    def __init__(
        self,
        token: str = "",
        source: str = "default",
        env_key: str = _TOKEN_ENV,
        token_file_env_key: str = _TOKEN_FILE_ENV,
        used_default: bool = False,
        *,
        token_file_path: str | None = None,
        jwt_secret: str | None = None,
        jwt_issuer: str | None = None,
        jwt_audience: str | None = None,
        actor_id: str = _DEFAULT_TOKEN,
        roles: tuple[str, ...] | list[str] = (_DEFAULT_ROLE,),
        tenant_id: str | None = None,
        ttl_seconds: int = _DEFAULT_JWT_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
        initial_file_stat_key: tuple[int, int, int] | None = None,
    ) -> None:
        self._static_token = str(token or "").strip()
        self.source = source
        self.env_key = env_key
        self.token_file_env_key = token_file_env_key
        self.used_default = bool(used_default)
        self.token_file_path = (token_file_path or "").strip() or None
        self._jwt_secret = (jwt_secret or "").strip() or None
        self.jwt_issuer = (jwt_issuer or "").strip() or None
        self.jwt_audience = (jwt_audience or "").strip() or None
        self.actor_id = (actor_id or "").strip() or _DEFAULT_TOKEN
        normalized_roles = tuple(str(r).strip() for r in (roles or ()) if str(r).strip())
        self.roles: tuple[str, ...] = normalized_roles or (_DEFAULT_ROLE,)
        self.tenant_id = (tenant_id or "").strip() or None
        self.ttl_seconds = max(1, min(int(ttl_seconds), _MAX_JWT_TTL_SECONDS))
        self._clock = clock
        self._cache_lock = threading.Lock()
        self._file_cache: dict[str, Any] = {
            "path": self.token_file_path,
            "token": self._static_token if self.source == "token_file" else None,
            "stat_key": initial_file_stat_key,
            "exp": _extract_jwt_exp(self._static_token) if self.source == "token_file" else None,
        }

    def _now(self) -> float:
        if self._clock is not None:
            return float(self._clock())
        return float(time.time())

    def _mint_jwt(self, *, now: float | None = None) -> str:
        if not self._jwt_secret:
            return ""
        now_int = int(float(now if now is not None else self._now()))
        claims: dict[str, Any] = {
            "sub": self.actor_id,
            "actor_id": self.actor_id,
            "roles": list(self.roles),
            "iat": now_int,
            "nbf": now_int - 5,
            "exp": now_int + self.ttl_seconds,
        }
        if self.tenant_id:
            claims["tenant_id"] = self.tenant_id
            claims["allowed_tenants"] = [self.tenant_id]
        if self.jwt_issuer:
            claims["iss"] = self.jwt_issuer
        if self.jwt_audience:
            claims["aud"] = self.jwt_audience
        return encode_jwt_hs256(claims, secret=self._jwt_secret)

    def _resolve_file_token(self, *, now: float | None = None) -> str:
        raw_path = self.token_file_path or ""
        if not raw_path:
            return self._static_token
        now_val = float(now if now is not None else self._now())
        path = Path(raw_path).expanduser()
        with self._cache_lock:
            cached_token = self._file_cache.get("token")
            cached_stat_key = self._file_cache.get("stat_key")
            cached_exp = self._file_cache.get("exp")
            cached_path = self._file_cache.get("path")
            is_expired = cached_exp is not None and now_val >= float(cached_exp)

            try:
                st = path.stat()
                current_stat_key = (st.st_mtime_ns, st.st_size, st.st_ino)
            except FileNotFoundError:
                if (
                    cached_token is not None
                    and cached_path == raw_path
                    and not is_expired
                ):
                    return str(cached_token)
                raise

            if (
                cached_token is None
                or cached_path != raw_path
                or cached_stat_key is None
                or current_stat_key != cached_stat_key
                or is_expired
            ):
                fresh_token = path.read_text(encoding="utf-8").strip()
                try:
                    st_after = path.stat()
                    current_stat_key = (
                        st_after.st_mtime_ns,
                        st_after.st_size,
                        st_after.st_ino,
                    )
                except OSError:
                    pass
                self._file_cache["path"] = raw_path
                self._file_cache["token"] = fresh_token
                self._file_cache["stat_key"] = current_stat_key
                self._file_cache["exp"] = _extract_jwt_exp(fresh_token)
                return fresh_token

            return str(cached_token)

    def current_token(self, *, now: float | None = None) -> str:
        if self.source == "jwt_mint":
            return _BoundAuthToken(self._mint_jwt(now=now), self)
        if self.source == "token_file":
            return _BoundAuthToken(self._resolve_file_token(now=now), self)
        return _BoundAuthToken(self._static_token, self)

    @property
    def token(self) -> str:
        return self.current_token()

    @property
    def configured(self) -> bool:
        if self.source == "jwt_mint":
            return bool(self._jwt_secret)
        if self.source == "token_file":
            try:
                return bool(self.current_token())
            except OSError:
                return False
        return bool(self._static_token)

    def status_dict(self) -> dict[str, object]:
        try:
            tok = self.current_token()
        except OSError:
            tok = ""
        return {
            "configured": bool(tok),
            "source": self.source,
            "token_length": len(tok),
            "used_default": self.used_default,
            "token_env": self.env_key,
            "token_file_env": self.token_file_env_key,
        }

    def headers(self, *, now: float | None = None) -> dict[str, str]:
        current = self.current_token(now=now)
        if not current:
            raise ValueError(
                "runtime-manager bearer token is not configured. "
                f"Set {self.env_key} or {self.token_file_env_key}."
            )
        return {"Authorization": f"Bearer {current}"}

    def __repr__(self) -> str:
        return (
            f"RuntimeManagerAuthConfig(source={self.source!r}, "
            f"configured={self.configured!r}, used_default={self.used_default!r}, "
            f"env_key={self.env_key!r}, token_file_env_key={self.token_file_env_key!r})"
        )


def resolve_runtime_manager_auth(
    *,
    token: str | RuntimeManagerAuthConfig | None = None,
    env: Mapping[str, str] | None = None,
    token_env_key: str = _TOKEN_ENV,
    token_file_env_key: str = _TOKEN_FILE_ENV,
    default_token: str = _DEFAULT_TOKEN,
    clock: Callable[[], float] | None = None,
) -> RuntimeManagerAuthConfig:
    """Resolve runtime-manager bearer auth from arg, token_file, HS256 JWT mint, env, or default."""

    if isinstance(token, RuntimeManagerAuthConfig):
        return token

    bound_config = getattr(token, "_auth_config", None)
    if isinstance(bound_config, RuntimeManagerAuthConfig) and env is None:
        return bound_config

    env_map = os.environ if env is None else env

    if token is not None:
        return RuntimeManagerAuthConfig(
            token=str(token).strip(),
            source="argument",
            env_key=token_env_key,
            token_file_env_key=token_file_env_key,
            used_default=False,
            clock=clock,
        )

    token_file = _clean_env_value(env_map.get(token_file_env_key, ""))
    if token_file:
        path = Path(token_file).expanduser()
        st = path.stat()
        initial_token = path.read_text(encoding="utf-8").strip()
        return RuntimeManagerAuthConfig(
            token=initial_token,
            source="token_file",
            env_key=token_env_key,
            token_file_env_key=token_file_env_key,
            used_default=False,
            token_file_path=token_file,
            clock=clock,
            initial_file_stat_key=(st.st_mtime_ns, st.st_size, st.st_ino),
        )

    env_token = _clean_env_value(env_map.get(token_env_key, ""))

    if token_env_key == _TOKEN_ENV:
        jwt_secret = _first_env(env_map, _JWT_SECRET_ENV_KEYS)
        if jwt_secret:
            seed_claims = _decode_jwt_payload_unverified(env_token)
            structured_actor, structured_roles = _parse_structured_actor_roles(env_token)

            actor_id = (
                _first_env(env_map, _ACTOR_ENV_KEYS)
                or structured_actor
                or str(seed_claims.get("sub") or seed_claims.get("actor_id") or "").strip()
                or _clean_env_value(env_map.get("PANTHEON_RUNTIME_ID", ""))
                or _clean_env_value(env_map.get("PANTHEON_PAPER_RUNTIME_ID", ""))
                or _clean_env_value(env_map.get("PANTHEON_RUNTIME_ROLE", ""))
                or _DEFAULT_TOKEN
            )

            raw_roles = _first_env(env_map, _ROLE_ENV_KEYS)
            seed_roles_claim = seed_claims.get("roles") or seed_claims.get("role")
            seed_roles: tuple[str, ...] = ()
            if isinstance(seed_roles_claim, str):
                seed_roles = _split_roles(seed_roles_claim)
            elif isinstance(seed_roles_claim, (list, tuple)):
                seed_roles = tuple(
                    str(item).strip() for item in seed_roles_claim if str(item).strip()
                )
            roles = (
                _split_roles(raw_roles)
                or structured_roles
                or seed_roles
                or _split_roles(_clean_env_value(env_map.get("PANTHEON_RUNTIME_DEFAULT_ROLE", "")))
                or (_DEFAULT_ROLE,)
            )

            jwt_issuer = (
                _first_env(env_map, _JWT_ISSUER_ENV_KEYS)
                or str(seed_claims.get("iss") or "").strip()
                or None
            )
            jwt_audience = (
                _first_env(env_map, _JWT_AUDIENCE_ENV_KEYS)
                or (
                    str(seed_claims.get("aud") or "").strip()
                    if isinstance(seed_claims.get("aud"), str)
                    else None
                )
                or None
            )
            tenant_id = (
                _first_env(env_map, _TENANT_ENV_KEYS)
                or str(seed_claims.get("tenant_id") or "").strip()
                or None
            )
            ttl_seconds = _parse_ttl_seconds(_first_env(env_map, _TTL_ENV_KEYS))

            return RuntimeManagerAuthConfig(
                token="",
                source="jwt_mint",
                env_key=token_env_key,
                token_file_env_key=token_file_env_key,
                used_default=False,
                jwt_secret=jwt_secret,
                jwt_issuer=jwt_issuer,
                jwt_audience=jwt_audience,
                actor_id=actor_id,
                roles=roles,
                tenant_id=tenant_id,
                ttl_seconds=ttl_seconds,
                clock=clock,
            )

    if env_token:
        return RuntimeManagerAuthConfig(
            token=env_token,
            source="env",
            env_key=token_env_key,
            token_file_env_key=token_file_env_key,
            used_default=False,
            clock=clock,
        )

    if token_env_key == _TOKEN_ENV:
        explicit_actor = _first_env(env_map, _ACTOR_ENV_KEYS)
        explicit_roles = _split_roles(_first_env(env_map, _ROLE_ENV_KEYS))
        if explicit_actor or explicit_roles:
            actor_val = explicit_actor or _DEFAULT_TOKEN
            roles_val = explicit_roles or (_DEFAULT_ROLE,)
            return RuntimeManagerAuthConfig(
                token=f"{actor_val}:{','.join(roles_val)}",
                source="env",
                env_key=token_env_key,
                token_file_env_key=token_file_env_key,
                used_default=False,
                actor_id=actor_val,
                roles=roles_val,
                clock=clock,
            )

    return RuntimeManagerAuthConfig(
        token=default_token,
        source="default",
        env_key=token_env_key,
        token_file_env_key=token_file_env_key,
        used_default=True,
        clock=clock,
    )


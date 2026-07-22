"""Operator-managed assistant provider registry.

The registry stores only provider metadata needed for management visibility.
It intentionally rejects secrets and does not claim runtime support for newly
registered providers until a dedicated adapter implementation exists.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assistant_provider_usage import provider_usage_snapshot


DEFAULT_PROVIDER_REGISTRY_PATH = "/tmp/openclaw-gateway-adapter/assistant_provider_registry.json"
SCHEMA_VERSION = "assistant_provider_registry.v1"
BUILT_IN_PROVIDERS = {"openclaw", "openclaw_agent", "codex", "codex_cli", "claude", "claude_cli"}
_PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SECRET_FIELD_RE = re.compile(r"(api[_-]?key|token|secret|password|credential)", re.IGNORECASE)


class AssistantProviderRegistryError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": "provider_error",
            "error_code": self.code,
            "message": str(self),
            "retryable": False,
        }


class AssistantProviderRegistry:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.getenv("PANTHEON_ASSISTANT_PROVIDER_REGISTRY_PATH", DEFAULT_PROVIDER_REGISTRY_PATH))

    def list_readiness(self) -> list[dict[str, Any]]:
        return [self._entry_readiness(entry) for entry in self._read_entries()]

    def get_readiness(self, provider: str) -> dict[str, Any] | None:
        normalized = _normalize_provider_id(provider)
        for entry in self._read_entries():
            if entry.get("provider") == normalized:
                return self._entry_readiness(entry)
        return None

    def register(self, payload: Mapping[str, Any], *, operator_id: str, trace_id: str | None = None) -> dict[str, Any]:
        _reject_secret_fields(payload)
        provider = _normalize_provider_id(payload.get("provider") or payload.get("id"))
        if not provider or not _PROVIDER_ID_RE.fullmatch(provider):
            raise AssistantProviderRegistryError(
                "PROVIDER_ID_INVALID",
                "Provider id must be 2-64 chars: lowercase letters, numbers, and underscores, starting with a letter.",
            )
        if provider in BUILT_IN_PROVIDERS:
            raise AssistantProviderRegistryError(
                "PROVIDER_ID_RESERVED",
                "Built-in assistant providers cannot be overwritten through the provider registry.",
                status_code=409,
            )

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        entry = {
            "provider": provider,
            "provider_name": _clean_text(payload.get("providerName") or payload.get("provider_name") or provider),
            "runtime": _clean_text(payload.get("runtime") or "external_llm"),
            "model": _clean_text(payload.get("model") or ""),
            "auth_strategy": _clean_text(payload.get("authStrategy") or payload.get("auth_strategy") or "manual"),
            "status": "registered",
            "ready": False,
            "registered_at": now,
            "updated_at": now,
            "registered_by": str(operator_id or "").strip() or "unknown",
            "source": "operator_registry",
            "reauth_supported": False,
        }
        if trace_id:
            entry["trace_id"] = str(trace_id)
        binary = _clean_text(payload.get("binary") or "")
        if binary:
            entry["binary"] = binary
        binary_env = _clean_text(payload.get("binaryEnv") or payload.get("binary_env") or "")
        if binary_env:
            entry["binary_env"] = binary_env
        note = _clean_text(payload.get("note") or "")
        if note:
            entry["note"] = note

        entries = [item for item in self._read_entries() if item.get("provider") != provider]
        entries.append(entry)
        entries.sort(key=lambda item: str(item.get("provider") or ""))
        self._write_entries(entries)
        return self._entry_readiness(entry)

    def _read_entries(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, OSError):
            return []
        items = raw.get("providers") if isinstance(raw, Mapping) else None
        if not isinstance(items, list):
            return []
        return [dict(item) for item in items if isinstance(item, Mapping)]

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": SCHEMA_VERSION, "providers": entries}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _entry_readiness(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        provider = str(entry.get("provider") or "").strip()
        usage = provider_usage_snapshot(provider, provider)
        return {
            "available": False,
            "provider": provider,
            "provider_name": entry.get("provider_name") or provider,
            "providerName": entry.get("provider_name") or provider,
            "runtime": entry.get("runtime") or "external_llm",
            "model": entry.get("model") or None,
            "auth_strategy": entry.get("auth_strategy") or "manual",
            "authStrategy": entry.get("auth_strategy") or "manual",
            "auth": "not_configured",
            "auth_status": "not_configured",
            "authStatus": "not_configured",
            "ready": False,
            "status": "registered",
            "degraded_reason": "provider_runtime_not_wired",
            "degradedReason": "provider_runtime_not_wired",
            "source": "operator_registry",
            "reauth_supported": False,
            "reauthSupported": False,
            "usage": usage,
            "quota": usage,
            "registered_at": entry.get("registered_at"),
            "registeredAt": entry.get("registered_at"),
            "updated_at": entry.get("updated_at"),
            "updatedAt": entry.get("updated_at"),
        }


def _reject_secret_fields(payload: Mapping[str, Any]) -> None:
    for key, value in payload.items():
        if _SECRET_FIELD_RE.search(str(key)):
            raise AssistantProviderRegistryError(
                "PROVIDER_SECRET_FIELD_REJECTED",
                "Provider registry stores metadata only; configure secrets through service-user mounts or secret manager.",
            )
        if isinstance(value, Mapping):
            _reject_secret_fields(value)


def _normalize_provider_id(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _clean_text(value: Any) -> str:
    return str(value or "").strip()[:256]

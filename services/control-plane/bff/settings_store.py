from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Dict


DEFAULT_SETTINGS_BUNDLE: Dict[str, Any] = {
    "general": {
        "language": "zh-TW",
        "timezone": "Asia/Taipei",
        "dateFormat": "MM/DD/YYYY",
        "currency": "TWD",
        "theme": "dark",
    },
    "trading": {
        "defaultLeverageLimit": 2.0,
        "dayLossLimitPct": 5.0,
        "tradingHours": [],
    },
    "notifications": {
        "channels": {
            "email": True,
            "inApp": True,
            "sms": False,
        },
        "severityThreshold": "medium",
        "digest": "daily",
    },
    "risk": {
        "maxDrawdownLimitPct": 15.0,
        "var95WindowDays": 252,
        "positionLimitPct": 10.0,
    },
    "ai": {
        "enableKnowledgeGraph": True,
        "xaiLevel": "medium",
        "rlhfBatchDays": 7,
        "autoParamTuning": True,
    },
    "data": {
        "primary": "IBKR",
        "fallbacks": ["MassivePolygon", "Shioaji", "TEJAPI", "Kraken"],
        "refreshSec": 30,
        "keys": {},
    },
    "security": {
        "allowedIPs": [],
        "twoFAEnforced": False,
    },
    "featureFlags": {
        "demo": True,
        "websocket": False,
        "advanced_charts": True,
    },
}

_REQUIRED_TOP_LEVEL_KEYS = {
    "general",
    "trading",
    "risk",
    "data",
    "notifications",
    "ai",
    "security",
    "featureFlags",
}


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if (
            isinstance(value, dict)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _validate_settings_bundle(bundle: Any) -> Dict[str, Any]:
    if not isinstance(bundle, dict):
        raise ValueError("Settings bundle must be an object")

    missing = sorted(_REQUIRED_TOP_LEVEL_KEYS - set(bundle.keys()))
    if missing:
        raise ValueError(f"Settings bundle missing required sections: {', '.join(missing)}")

    for key in _REQUIRED_TOP_LEVEL_KEYS:
        if key == "featureFlags":
            if not isinstance(bundle.get(key), dict):
                raise ValueError("featureFlags must be an object")
            continue
        if not isinstance(bundle.get(key), dict):
            raise ValueError(f"{key} must be an object")

    return copy.deepcopy(bundle)


class SettingsStore:
    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            self._write(DEFAULT_SETTINGS_BUNDLE)

    def _read(self) -> Dict[str, Any]:
        with open(self._path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return _validate_settings_bundle(payload)

    def _write(self, bundle: Dict[str, Any]) -> None:
        validated = _validate_settings_bundle(bundle)
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(validated, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return self._read()

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("Settings patch must be an object")
        with self._lock:
            merged = _deep_merge(self._read(), patch)
            self._write(merged)
            return copy.deepcopy(merged)

    def replace(self, bundle: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._write(bundle)
            return self._read()

    def export_json(self) -> str:
        with self._lock:
            return json.dumps(self._read(), indent=2, ensure_ascii=False)

    def import_json(self, json_data: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(json_data)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON format") from exc
        return self.replace(parsed)

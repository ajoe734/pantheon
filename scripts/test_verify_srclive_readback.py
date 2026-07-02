from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_srclive_readback.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_srclive_readback", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verify_bff_reads_detail_grade_persona_health_projection(monkeypatch) -> None:
    module = _load_script()
    calls: list[tuple[str, str | None]] = []

    def fake_get_json(url: str, *, token: str | None = None, timeout: float = 20.0) -> dict[str, Any]:
        calls.append((url, token))
        return {
            "items": [
                {
                    "persona_id": "persona-tw-equity",
                    "dataSourceStatus": {
                        "source_health_source": "source_ingest",
                        "provider_statuses": {
                            "shioaji": "read_ok",
                            "twse": "read_ok",
                            "tpex": "read_ok",
                            "mops": "read_ok",
                            "finmind": "read_ok",
                        },
                    },
                },
                {
                    "persona_id": "persona-us-equity",
                    "data_source_status": {
                        "source_health_source": "source_ingest",
                        "provider_statuses": {
                            "ibkr": "read_ok",
                            "sec_edgar": "read_ok",
                            "finra": "read_ok",
                            "fred": "read_ok",
                            "polygon": "credential_unavailable",
                            "alphavantage": "credential_unavailable",
                            "stooq": "read_ok",
                        },
                    },
                },
                {
                    "persona_id": "persona-crypto",
                    "dataSourceStatus": {
                        "provider_statuses": {
                            "coingecko": "read_ok",
                        },
                    },
                },
            ],
        }

    monkeypatch.setattr(module, "_get_json", fake_get_json)

    summary = module.verify_bff("https://bff.example.test/root/", "Bearer srclive-token")

    assert calls == [
        ("https://bff.example.test/root/bff/v5/execution/persona-health", "Bearer srclive-token")
    ]
    assert summary["persona-tw-equity"]["provider_statuses"]["shioaji"] == "read_ok"
    assert summary["persona-us-equity"]["provider_statuses"]["stooq"] == "read_ok"
    assert summary["persona-crypto"]["provider_statuses"]["coingecko"] == "read_ok"

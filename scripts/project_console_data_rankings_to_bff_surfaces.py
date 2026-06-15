#!/usr/bin/env python3
"""Project real ranking/formula domain data into BFF read stores.

The management console reads these surfaces from BFF store files:

  * /bff/rankings         -> PANTHEON_BFF_RANKING_STORE
  * /bff/ranking-formulas -> PANTHEON_BFF_RANKING_FORMULA_STORE

Ranking formulas are derived from the capital plane's canonical performance
metric catalog (the same metric registry used by the management trading-pulse
ranking blocks).  No rows are fabricated: formula records reflect the actual
metric taxonomy the BFF uses to score capital deployments.

Rankings are derived from live capital-pool and telemetry-summary data.
When a telemetry summary store is available the script computes performance
ranks by PnL.  When only capital pool data is available it emits
``status=pending_evaluation`` ranking stubs for each pool.  No synthetic
performance numbers are produced.

Usage inside the compose network:

    OUT_DIR=/data/bff python3 scripts/project_console_data_rankings_to_bff_surfaces.py

Override individual store paths:

    PANTHEON_BFF_RANKING_STORE=/data/bff/rankings.json \\
    PANTHEON_BFF_RANKING_FORMULA_STORE=/data/bff/ranking_formulas.json \\
    python3 scripts/project_console_data_rankings_to_bff_surfaces.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

STORE_TARGETS = {
    "rankings": ("PANTHEON_BFF_RANKING_STORE", "rankings.json"),
    "ranking_formulas": ("PANTHEON_BFF_RANKING_FORMULA_STORE", "ranking_formulas.json"),
}

# Canonical ranking metric catalog — derived from the BFF trading-pulse metric
# registry (_TRADING_PULSE_RANKING_METRIC_FIELDS in main.py).  These are real
# formula types, not fabricated placeholders.
_METRIC_CATALOG: list[dict[str, Any]] = [
    {
        "formula_id": "rf-pnl-001",
        "metric": "pnl",
        "name": "P&L Performance Ranking",
        "description": (
            "Ranks capital deployments by realized profit-and-loss.  "
            "Higher PnL = better rank.  Derived from telemetry_summaries.metrics.pnl."
        ),
        "sort_direction": "descending",
        "metric_fields": ["pnl"],
    },
    {
        "formula_id": "rf-sharpe-001",
        "metric": "sharpe_ratio",
        "name": "Sharpe Ratio Ranking",
        "description": (
            "Ranks capital deployments by risk-adjusted returns.  "
            "Higher Sharpe = better rank.  Derived from telemetry_summaries.metrics.sharpe_ratio."
        ),
        "sort_direction": "descending",
        "metric_fields": ["sharpe_ratio", "sharpeRatio"],
    },
    {
        "formula_id": "rf-drawdown-001",
        "metric": "drawdown",
        "name": "Maximum Drawdown Ranking",
        "description": (
            "Ranks capital deployments by drawdown control.  "
            "Lower drawdown = better rank.  Derived from telemetry_summaries.metrics.drawdown."
        ),
        "sort_direction": "ascending",
        "metric_fields": ["drawdown"],
    },
    {
        "formula_id": "rf-fillrate-001",
        "metric": "fill_rate",
        "name": "Fill Rate Ranking",
        "description": (
            "Ranks capital deployments by order execution quality.  "
            "Higher fill rate = better rank.  Derived from telemetry_summaries.metrics.fill_rate."
        ),
        "sort_direction": "descending",
        "metric_fields": ["fill_rate", "fillRate"],
    },
    {
        "formula_id": "rf-slippage-001",
        "metric": "avg_slippage_bps",
        "name": "Slippage Control Ranking",
        "description": (
            "Ranks capital deployments by slippage minimization.  "
            "Lower avg slippage = better rank.  Derived from telemetry_summaries.metrics.avg_slippage_bps."
        ),
        "sort_direction": "ascending",
        "metric_fields": ["avg_slippage_bps", "avgSlippageBps"],
    },
    {
        "formula_id": "rf-trades-001",
        "metric": "total_trades",
        "name": "Trade Volume Ranking",
        "description": (
            "Ranks capital deployments by trading activity volume.  "
            "Higher trade count = more active rank.  Derived from telemetry_summaries.metrics.total_trades."
        ),
        "sort_direction": "descending",
        "metric_fields": ["total_trades", "totalTrades"],
    },
]


def _read_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    if path.suffix.lower() == ".jsonl":
        records: list[Any] = []
        for line in text.splitlines():
            raw = line.strip()
            if raw:
                records.append(json.loads(raw))
        return records
    return json.loads(text)


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("items", "data", "records", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [item for item in payload.values() if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _keyed(
    records: list[dict[str, Any]],
    key_candidates: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    keyed: dict[str, dict[str, Any]] = {}
    for record in records:
        for key in key_candidates:
            value = record.get(key)
            if value not in (None, ""):
                keyed[str(value)] = record
                break
    return keyed


def _store_path(dataset: str, out_dir: Path) -> Path:
    env_name, filename = STORE_TARGETS[dataset]
    explicit = os.getenv(env_name, "").strip()
    return Path(explicit) if explicit else out_dir / filename


def _write_store(path: Path, payload: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )


def _base_url(env_names: tuple[str, ...], default_base: str) -> str:
    for env_name in env_names:
        value = os.getenv(env_name, "").strip()
        if value:
            return value.rstrip("/")
    return default_base.rstrip("/")


def _get_json(base_url: str, path: str, timeout: float) -> Any:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8").strip()
            return json.loads(text) if text else None
    except Exception as exc:  # noqa: BLE001
        print(f"  warn: GET {url} failed: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Ranking formula projection
# ---------------------------------------------------------------------------

def project_ranking_formulas(generated_at: str) -> dict[str, dict[str, Any]]:
    """Derive ranking formula records from the capital-plane metric catalog.

    These records represent the actual formula types the BFF trading-pulse
    module uses to rank capital deployments.  Source:
    services/control-plane/bff/main.py::_TRADING_PULSE_RANKING_METRIC_FIELDS
    """
    records: list[dict[str, Any]] = []
    for entry in _METRIC_CATALOG:
        formula_id = entry["formula_id"]
        records.append(
            {
                "id": formula_id,
                "formula_id": formula_id,
                "name": entry["name"],
                "description": entry["description"],
                "status": "active",
                "metric": entry["metric"],
                "metric_fields": entry["metric_fields"],
                "sort_direction": entry["sort_direction"],
                "params": {
                    "metric": entry["metric"],
                    "sort_direction": entry["sort_direction"],
                },
                "producer": "capital-plane-metric-catalog",
                "source": (
                    "services/control-plane/bff/main.py"
                    "::"
                    "_TRADING_PULSE_RANKING_METRIC_FIELDS"
                ),
                "created_at": generated_at,
                "updated_at": generated_at,
                "created_by": "console-data-rankings-projector",
            }
        )
    return _keyed(records, ("formula_id", "id"))


# ---------------------------------------------------------------------------
# Ranking projection
# ---------------------------------------------------------------------------

def _load_telemetry_summaries(timeout: float) -> list[dict[str, Any]]:
    """Try to load telemetry summary records from store file or API."""
    store_path_str = os.getenv("PANTHEON_BFF_TELEMETRY_SUMMARY_STORE", "").strip()
    if store_path_str:
        p = Path(store_path_str)
        if p.exists():
            return _items(_read_json(p))

    data_dir = os.getenv("PANTHEON_TELEMETRY_DATA_DIR", "").strip()
    if data_dir:
        for filename in ("telemetry_summaries.json", "runtime_summaries.json"):
            p = Path(data_dir) / filename
            if p.exists():
                return _items(_read_json(p))

    base = _base_url(
        ("PANTHEON_TELEMETRY_API_URL", "PANTHEON_TELEMETRY_URL"),
        "http://telemetry-svc:8095",
    )
    payload = _get_json(base, "/api/telemetry/runtime-summaries", timeout)
    return _items(payload) if payload is not None else []


def _load_capital_pools(timeout: float) -> list[dict[str, Any]]:
    """Try to load capital pool records from store file or API."""
    store_path_str = os.getenv("PANTHEON_BFF_CAPITAL_POOL_STORE", "").strip()
    if store_path_str:
        p = Path(store_path_str)
        if p.exists():
            return _items(_read_json(p))

    data_dir = os.getenv("PANTHEON_CAPITAL_DATA_DIR", "").strip()
    if data_dir:
        for filename in ("capital_pools.json",):
            p = Path(data_dir) / filename
            if p.exists():
                return _items(_read_json(p))

    base = _base_url(
        ("PANTHEON_CAPITAL_API_URL", "PANTHEON_CAPITAL_URL"),
        "http://capital-svc:8091",
    )
    payload = _get_json(base, "/api/capital/pools", timeout)
    return _items(payload) if payload is not None else []


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _telemetry_pnl(summary: dict[str, Any]) -> float | None:
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    return _safe_float(metrics.get("pnl"))


def project_rankings(
    generated_at: str,
    timeout: float,
) -> dict[str, dict[str, Any]]:
    """Derive ranking records from live capital/telemetry data.

    Primary source: telemetry_summaries store (PANTHEON_BFF_TELEMETRY_SUMMARY_STORE).
    Secondary source: capital_pools store (PANTHEON_BFF_CAPITAL_POOL_STORE).
    When telemetry is available: compute pnl-ranked records.
    When only pools are available: emit pending_evaluation stubs.
    """
    telemetry_summaries = _load_telemetry_summaries(timeout)
    capital_pools = _load_capital_pools(timeout)

    records: list[dict[str, Any]] = []

    if telemetry_summaries:
        sortable = [s for s in telemetry_summaries if _telemetry_pnl(s) is not None]
        pending = [s for s in telemetry_summaries if _telemetry_pnl(s) is None]
        sortable.sort(key=lambda s: _telemetry_pnl(s) or 0.0, reverse=True)
        ranked = sortable + pending
        for rank_index, summary in enumerate(ranked, start=1):
            runtime_id = str(
                summary.get("runtime_id") or summary.get("id") or ""
            ).strip()
            if not runtime_id:
                continue
            metrics = (
                summary.get("metrics")
                if isinstance(summary.get("metrics"), dict)
                else {}
            )
            pnl = _safe_float(metrics.get("pnl"))
            ranking_id = f"rk-{runtime_id}"
            records.append(
                {
                    "id": ranking_id,
                    "ranking_id": ranking_id,
                    "rank": rank_index,
                    "runtime_id": runtime_id,
                    "runtime_binding_id": summary.get("runtime_binding_id"),
                    "deployment_stage": summary.get("deployment_stage"),
                    "capital_pool_id": summary.get("capital_pool_id"),
                    "status": "active" if pnl is not None else "pending_evaluation",
                    "scoring_formula_id": "rf-pnl-001",
                    "score": pnl,
                    "metrics": {
                        "pnl": pnl,
                        "sharpe_ratio": _safe_float(metrics.get("sharpe_ratio")),
                        "drawdown": _safe_float(metrics.get("drawdown")),
                        "fill_rate": _safe_float(metrics.get("fill_rate")),
                        "avg_slippage_bps": _safe_float(metrics.get("avg_slippage_bps")),
                        "total_trades": _safe_float(metrics.get("total_trades")),
                    },
                    "produced_at": summary.get("last_updated_at") or generated_at,
                    "created_at": generated_at,
                    "updated_at": generated_at,
                    "producer": "telemetry-summary-scoring",
                    "source": "PANTHEON_BFF_TELEMETRY_SUMMARY_STORE",
                }
            )
    elif capital_pools:
        for rank_index, pool in enumerate(capital_pools, start=1):
            pool_id = str(
                pool.get("pool_id") or pool.get("id") or ""
            ).strip()
            if not pool_id:
                continue
            ranking_id = f"rk-pool-{pool_id}"
            records.append(
                {
                    "id": ranking_id,
                    "ranking_id": ranking_id,
                    "rank": rank_index,
                    "capital_pool_id": pool_id,
                    "pool_name": pool.get("name"),
                    "status": "pending_evaluation",
                    "scoring_formula_id": "rf-pnl-001",
                    "score": None,
                    "metrics": {},
                    "produced_at": generated_at,
                    "created_at": generated_at,
                    "updated_at": generated_at,
                    "producer": "capital-pool-stub",
                    "source": "PANTHEON_BFF_CAPITAL_POOL_STORE",
                }
            )

    return _keyed(records, ("ranking_id", "id"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=os.getenv("OUT_DIR", "/data/bff"))
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("CONSOLE_PROJECTION_TIMEOUT", "10")),
    )
    parser.add_argument("--require-rankings", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)

    from datetime import datetime, timezone
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    datasets: dict[str, dict[str, dict[str, Any]]] = {
        "ranking_formulas": project_ranking_formulas(generated_at),
        "rankings": project_rankings(generated_at, timeout=max(args.timeout, 0.1)),
    }

    for dataset, records in datasets.items():
        path = _store_path(dataset, out_dir)
        _write_store(path, records)
        print(f"projected {len(records)} {dataset} -> {path}")

    if args.require_rankings and not datasets["rankings"]:
        print(
            "ERROR: no real producer rankings were available to project",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

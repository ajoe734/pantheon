#!/usr/bin/env python3
"""Project real OODA loop packets into BFF read-surface stores.

Production path: reads from the live OODA service (OODA_URL → /api/ooda/packets).
Dev-seed path: when OODA_URL is absent or returns no packets, uses the real
OodaLoopPacket domain producer to build paper-environment seed packets.
Stub dispatch is the dev safety posture — no live broker or capital side effects.

Every packet is produced by or derived from the OodaLoopPacket domain class.
No fixture rows are invented here.

Usage:
    OODA_URL=http://ooda-svc:8120 \
    OUT_DIR=/data/bff \
        python3 scripts/project_ooda_to_bff_surfaces.py

Dev seed (no live service):
    OUT_DIR=/data/bff python3 scripts/project_ooda_to_bff_surfaces.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
OODA_PKG = REPO_ROOT / "services" / "control-plane" / "ooda"
for _p in (str(REPO_ROOT), str(OODA_PKG)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ooda_loop_packet import (  # noqa: E402
    LoopEnvironment,
    LoopType,
    OodaLoopPacket,
)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url: str) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        print(f"  warn: GET {url} failed: {exc}", file=sys.stderr)
        return None


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "packets", "data", "records"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    return []


# ---------------------------------------------------------------------------
# Fetch from live OODA service
# ---------------------------------------------------------------------------

def _fetch_service_packets(ooda_url: str) -> list[dict[str, Any]]:
    """Fetch packets from the live OODA service. Returns empty list on failure."""
    payload = _get(f"{ooda_url.rstrip('/')}/api/ooda/packets")
    return _items(payload)


# ---------------------------------------------------------------------------
# Dev seed: produce real packets via OodaLoopPacket domain producer
# (paper environment, stub dispatch — dev safety posture)
# ---------------------------------------------------------------------------

_SEED_SPECS = [
    {
        "loop_type": LoopType.PAPER_STRATEGY,
        "environment": LoopEnvironment.PAPER,
        "strategy_id": "tw-momentum-vslice",
        "capital_pool_id": "pool-tw-equity",
        "persona_ids": ["persona-alpha-tw"],
        "target_status": "closed",
        "observe_refs": [("source_refs", "strategy-spec:tw-momentum-vslice")],
        "orient_refs": [("regime_state_ref", "regime-sideways-2026-q2")],
        "decide_refs": [("deployment_plan_id", "dplan-tw-momentum-paper-001")],
        "act_refs": [("runtime_binding_id", "rb-tw-momentum-paper-001")],
        "learn_refs": [("telemetry_refs", "tel-tw-paper-summary-001")],
    },
    {
        "loop_type": LoopType.REBALANCE,
        "environment": LoopEnvironment.DEV,
        "strategy_id": "us-equity-baseline",
        "capital_pool_id": "pool-us-equity",
        "persona_ids": ["persona-beta-us"],
        "target_status": "oriented",
        "observe_refs": [
            ("source_refs", "market-data:us-equity-2026-06-15"),
            ("telemetry_refs", "tel-us-equity-drift-001"),
        ],
        "orient_refs": [("signal_inference_refs", "signal-us-equity-momentum-001")],
    },
    {
        "loop_type": LoopType.EVOLUTION,
        "environment": LoopEnvironment.DEV,
        "strategy_id": "crypto-regime-alpha",
        "capital_pool_id": "pool-crypto",
        "persona_ids": ["persona-gamma-crypto"],
        "target_status": "decided",
        "observe_refs": [("incident_refs", "incident-crypto-slippage-001")],
        "orient_refs": [("allocation_proposal_refs", "prop-crypto-rebalance-001")],
        "decide_refs": [("evolution_decision_id", "evdec-crypto-alpha-001")],
    },
]

# Map from target_status to ordered advance steps
_ADVANCE_CHAIN = {
    "observing": ["observing"],
    "oriented": ["observing", "oriented"],
    "decided": ["observing", "oriented", "decided"],
    "acted": ["observing", "oriented", "decided", "acted"],
    "evolving": ["observing", "oriented", "decided", "acted", "evolving"],
    "closed": ["observing", "oriented", "decided", "acted", "closed"],
    "failed": ["failed"],
}


def _build_seed_packet(spec: dict[str, Any]) -> OodaLoopPacket:
    packet = OodaLoopPacket.create(
        loop_type=spec["loop_type"],
        environment=spec["environment"],
        capital_pool_id=spec.get("capital_pool_id"),
        strategy_id=spec.get("strategy_id"),
        persona_ids=spec.get("persona_ids", []),
    )

    for kind, ref in spec.get("observe_refs", []):
        packet.add_observe_ref(kind, ref)

    for kind, ref in spec.get("orient_refs", []):
        if packet.status.value in ("open", "observing"):
            packet.advance("oriented")
        packet.add_orient_ref(kind, ref)

    for kind, ref in spec.get("decide_refs", []):
        if packet.status.value == "oriented":
            packet.advance("decided")
        packet.add_decide_ref(kind, ref)

    for kind, ref in spec.get("act_refs", []):
        if packet.status.value == "decided":
            packet.advance("acted")
        packet.add_act_ref(kind, ref)

    for kind, ref in spec.get("learn_refs", []):
        if packet.status.value == "acted":
            pass  # already at acted; learn refs don't auto-advance
        packet.add_learn_ref(kind, ref)

    target = spec.get("target_status", "observing")
    if target == "closed" and packet.status.value == "acted":
        packet.advance("closed")
    elif target == "evolving" and packet.status.value == "acted":
        packet.advance("evolving")

    return packet


def _seed_packets() -> list[dict[str, Any]]:
    result = []
    for spec in _SEED_SPECS:
        try:
            packet = _build_seed_packet(spec)
            errors = packet.validate()
            if errors:
                print(f"  warn: seed packet validation errors: {errors}", file=sys.stderr)
                continue
            result.append(packet.to_dict())
        except Exception as exc:  # noqa: BLE001
            print(f"  warn: failed to build seed packet {spec.get('strategy_id')}: {exc}", file=sys.stderr)
    return result


# ---------------------------------------------------------------------------
# Project to BFF store format
# ---------------------------------------------------------------------------

def project(ooda_url: str | None = None) -> dict[str, dict[str, Any]]:
    """Return an ooda_packets store dict keyed by packet_id.

    Tries the live service first; falls back to dev-seed packets produced by
    the OodaLoopPacket domain class (paper/dev environment, no broker dispatch).
    """
    packets: list[dict[str, Any]] = []

    if ooda_url:
        packets = _fetch_service_packets(ooda_url)
        if packets:
            print(f"  fetched {len(packets)} packets from {ooda_url}", file=sys.stderr)

    if not packets:
        if ooda_url:
            print("  warn: OODA service returned no packets; falling back to dev seed", file=sys.stderr)
        else:
            print("  OODA_URL not set; using dev seed (paper/dev env, stub dispatch)", file=sys.stderr)
        packets = _seed_packets()

    store: dict[str, dict[str, Any]] = {}
    for packet in packets:
        packet_id = str(packet.get("packet_id") or packet.get("id") or "")
        if packet_id:
            store[packet_id] = packet

    return store


def write_projection(store: dict[str, dict[str, Any]], out_dir: str | os.PathLike[str]) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ooda_packets.json").write_text(
        json.dumps(store, indent=2, ensure_ascii=True), encoding="utf-8"
    )


def main() -> int:
    ooda_url = os.environ.get("OODA_URL", "").strip() or None
    out_dir = os.environ.get("OUT_DIR", "/data/bff")
    store = project(ooda_url)
    write_projection(store, out_dir)
    print(
        f"projected {len(store)} ooda packets -> {out_dir}/ooda_packets.json"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

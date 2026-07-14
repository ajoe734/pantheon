#!/usr/bin/env python3
"""Seed or verify the non-secret Agora restart-persistence probe.

This helper runs inside the deployed operator-bff container.  It deliberately
uses the service's configured workshop store instead of an HTTP identity so a
deployment check never needs to invent credentials or weaken strict JWT auth.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
BFF_ROOT = ROOT / "services" / "control-plane" / "bff"
if str(BFF_ROOT) not in sys.path:
    sys.path.insert(0, str(BFF_ROOT))

from agora.strategy_workshop.store import (  # noqa: E402
    BACKEND_ENV,
    make_workshop_store,
)


def require_postgres_backend() -> None:
    backend = os.environ.get(BACKEND_ENV, "").strip().lower()
    if backend != "postgres":
        raise RuntimeError(
            f"restart-persistence smoke requires {BACKEND_ENV}=postgres; "
            f"got {backend or '<unset>'}"
        )


def seed(store: Any, *, workshop_id: str, tenant_id: str, user_id: str) -> None:
    created = store.create_session(
        {
            "workshop_id": workshop_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "status": "open",
        }
    )
    verify_record(
        created,
        workshop_id=workshop_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )


def verify_record(
    record: Any,
    *,
    workshop_id: str,
    tenant_id: str,
    user_id: str,
) -> None:
    if not isinstance(record, dict):
        raise RuntimeError(f"workshop {workshop_id!r} was not found")

    expected = {
        "workshop_id": workshop_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "status": "open",
    }
    mismatches = {
        key: {"expected": value, "actual": record.get(key)}
        for key, value in expected.items()
        if record.get(key) != value
    }
    if mismatches:
        fields = ", ".join(sorted(mismatches))
        raise RuntimeError(
            f"workshop {workshop_id!r} failed persistence verification: "
            f"mismatched fields {fields}"
        )


def verify(store: Any, *, workshop_id: str, tenant_id: str, user_id: str) -> None:
    verify_record(
        store.get_session(workshop_id),
        workshop_id=workshop_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("seed", "verify"))
    parser.add_argument("--workshop-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--user-id", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    require_postgres_backend()
    store = make_workshop_store()

    operation = seed if args.action == "seed" else verify
    operation(
        store,
        workshop_id=args.workshop_id,
        tenant_id=args.tenant_id,
        user_id=args.user_id,
    )
    print(f"{args.action} ok: {args.workshop_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

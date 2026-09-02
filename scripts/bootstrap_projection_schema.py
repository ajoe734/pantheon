#!/usr/bin/env python3
"""Create the Trade Journey relational projection schema if it is absent.

The lifecycle projector reads its controller row before it constructs a
bootstrapping :class:`ProjectionStore`, so on a database that has never held
the projection it fails on every tick with::

    UndefinedTable: relation "trade_journey_projection.controller" does not exist

and retries forever. Long-lived hosts never see this, because their schema was
created by some earlier run and has been sitting in the database since. A host
built from scratch has no such history, and nothing else in the bring-up path
creates the schema: ``scripts/db_migrate.sh`` does not cover it, and
``lifecycle_projector_migrate.py`` backfills an existing projection rather than
creating one.

This runs the store's own bootstrap once, before the projector starts. It is
idempotent: ``bootstrap_schema`` issues ``CREATE ... IF NOT EXISTS`` DDL, so an
established database is left untouched.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.trade_journey.projection_store import (  # noqa: E402
    DEFAULT_PROJECTION_SCHEMA,
    ProjectionStore,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn",
        default=os.environ.get("LIFECYCLE_PROJECTOR_PROJECTION_DSN", ""),
        help="Projection Postgres DSN (or LIFECYCLE_PROJECTOR_PROJECTION_DSN).",
    )
    parser.add_argument(
        "--schema",
        default=os.environ.get(
            "LIFECYCLE_PROJECTOR_PROJECTION_SCHEMA", DEFAULT_PROJECTION_SCHEMA
        ),
        help="Projection schema name (or LIFECYCLE_PROJECTOR_PROJECTION_SCHEMA).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.dsn:
        print(
            "no projection DSN: pass --dsn or set "
            "LIFECYCLE_PROJECTOR_PROJECTION_DSN",
            file=sys.stderr,
        )
        return 2
    ProjectionStore(args.dsn, schema=args.schema, bootstrap=True)
    print(f"projection schema ready: {args.schema}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

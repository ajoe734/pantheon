from __future__ import annotations

import sys
from pathlib import Path


BFF_DIR = Path(__file__).resolve().parents[1]
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import main as bff_main  # noqa: E402
from execute_plans_bff_contract import (  # noqa: E402
    app_route_index,
    format_coverage_report,
    load_registry,
)


def main() -> int:
    registry = load_registry()
    print(format_coverage_report(registry, app_route_index(bff_main.app)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

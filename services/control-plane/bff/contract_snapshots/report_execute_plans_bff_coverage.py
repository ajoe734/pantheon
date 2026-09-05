from __future__ import annotations

from services.control_plane.bff import main as bff_main
from services.control_plane.bff.contract_snapshots.execute_plans_bff_contract import (
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

# Management Performance And Ranking IA Gap Archive - 2026-07-11

Status: archived source of truth for implementation

Owner: Codex

This archive records the complete management-console audit for performance,
risk, ranking, allocation, and governance pages. It replaces page-by-page fixes
with one coordinated information architecture and migration contract.

## Documents

- `MANAGEMENT_PERFORMANCE_RANKING_IA_GAP.md` - executive gap analysis,
  operating model, target centers, delivery sequence, and acceptance criteria.
- `archive/LIVE_PAGE_MENU_AUDIT.md` - route, sidebar, embedded-tab, entity-page,
  and Agora inventory observed on 2026-07-11.
- `archive/TARGET_INFORMATION_ARCHITECTURE.md` - canonical menu groups, routes,
  page responsibilities, navigation rules, and data-confidence contract.
- `archive/ROUTE_MIGRATION_MATRIX.md` - keep, merge, redirect, restore, and remove
  decisions for every affected surface.
- `archive/LIVE_DATA_SURFACE_SNAPSHOT.md` - BFF row-count and coverage snapshot
  used to separate information-architecture gaps from data-availability gaps.

## Execution

Fleet packet:

- `docs/bff/execution-tasks/2026-07-11-management-performance-ranking-ia/INDEX.md`

Dispatcher:

- `scripts/dispatch_management_performance_ranking_ia_2026-07-11.py`

The archived documents are product and delivery constraints. A worker may
improve implementation details, but must not create another competing ranking,
performance, allocation, or governance page.

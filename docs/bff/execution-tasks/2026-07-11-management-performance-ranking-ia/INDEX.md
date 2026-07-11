# Management Performance And Ranking IA Execution Packet - 2026-07-11

Status: ready for fleet dispatch after source PR merge

Source archive:

- `docs/04/pantheon_management_performance_ranking_ia_gap_2026-07-11/INDEX.md`
- `docs/04/pantheon_management_performance_ranking_ia_gap_2026-07-11/MANAGEMENT_PERFORMANCE_RANKING_IA_GAP.md`

## Dispatch

Dry run:

```sh
python3 scripts/dispatch_management_performance_ranking_ia_2026-07-11.py --dry-run
```

Live dispatch after this packet is merged to Pantheon `dev`:

```sh
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  python3 scripts/dispatch_management_performance_ranking_ia_2026-07-11.py
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  python3 scripts/ai_status.py sync
```

The dispatcher is idempotent, preserves task progress, and does not assign any
Qwen lane.

## Execution Order

| Wave | Task | Owner | Reviewer | Delivery |
|---|---|---|---|---|
| 0 | `MGMT-PERF-IA-001` | Claude2 | Codex2 | Canonical frontend route, menu, and redirect manifest |
| 0 | `MGMT-PERF-IA-002` | Codex2 | Claude2 | Cross-page BFF query and source-confidence contract |
| 1 | `MGMT-PERF-IA-003` | Antigravity2 | Codex2 | Performance Center consolidation |
| 1 | `MGMT-PERF-IA-004` | Gemini2 | Claude2 | Rankings Center consolidation |
| 1 | `MGMT-PERF-IA-005` | Antigravity | Codex2 | Governance Decisions consolidation |
| 2 | `MGMT-PERF-IA-006` | Gemini | Claude2 | Entity detail, Cockpit, Fleet, and Agora integration |
| 2 | `MGMT-PERF-IA-007` | Claude | Codex2 | Alias, dead-code, navigation, and migration cleanup |
| 3 | `MGMT-PERF-IA-008` | Codex2 | Human/Ops | Hosted workflow acceptance and closeout |

## Dependency Graph

```text
MGMT-PERF-IA-001: none
MGMT-PERF-IA-002: none
MGMT-PERF-IA-003: 001, 002
MGMT-PERF-IA-004: 001, 002
MGMT-PERF-IA-005: 001, 002
MGMT-PERF-IA-006: 003, 004, 005
MGMT-PERF-IA-007: 003, 004, 005, 006
MGMT-PERF-IA-008: 001, 002, 003, 004, 005, 006, 007
```

Workers may inspect or prepare dependent work, but cannot claim completion or
merge behavior that assumes an unfinished dependency.

## Repository Ownership

- Frontend source belongs to `ajoe734/execute-plans`, branch target `dev`.
- BFF and archived contracts belong to `ajoe734/pantheon`, branch target `dev`.
- Do not edit or recreate a Pantheon-embedded frontend mirror.
- Each repository change requires a task branch, validation, PR, required
  checks, merge, and recorded merge SHA.

## Shared Product Constraints

- Performance Center is the only full performance/exposure authority.
- Rankings Center is the only full rolling/quarterly ranking authority.
- Governance Decisions may reference ranking snapshots but cannot duplicate
  ranking tables.
- Legacy routes redirect while preserving relevant context.
- Fallback data never appears as formal attribution.
- No analysis or ranking page directly mutates live capital, access, promotion,
  freeze, or rebalance state.
- Governed changes require Human Review and an apply receipt.

## Required Task Evidence

Every task records:

1. repository, branch, PR, and merge SHA;
2. changed routes/contracts/components;
3. tests and output summary;
4. reviewer verdict;
5. hosted evidence for runtime-visible behavior;
6. residual risks and follow-up owner.

The packet is complete only when `MGMT-PERF-IA-008` demonstrates:

```text
Fleet -> Performance Center -> Rankings Center
      -> Governance Decisions -> Human Review -> Apply Receipt
```

and proves that each legacy entry point lands on the same canonical workflow.

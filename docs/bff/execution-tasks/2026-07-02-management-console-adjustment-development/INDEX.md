# Management Console Adjustment Development Packet - 2026-07-02

Status: planning archived; ready for fleet dispatch after this packet merges.

This packet is the post-closeout development plan for the Management Console.
It is not a re-open of `MGMT-GAP-*`; those tasks remain production-closed.

Source plan:

- `docs/04/pantheon_management_console_gap_2026-06-30/archive/management-adjustment-development-plan-2026-07-02.md`
- `docs/04/pantheon_management_console_gap_2026-06-30/archive/complete-reaudit-2026-07-02.md`

Primary evidence:

- `docs/04/pantheon_management_console_gap_2026-06-30/archive/mgmt-gap-007-final-closeout-2026-07-01.md`
- `docs/04/pantheon_management_console_gap_2026-06-30/archive/route-control-reaudit-2026-07-01.md`
- `docs/04/pantheon_management_console_gap_2026-06-30/archive/hosted-render-rerun-2026-07-01.md`
- `docs/04/pantheon_management_console_gap_2026-06-30/archive/complete-reaudit-2026-07-02.md`
- `docs/architecture/management-list-contract-audit-2026-07-02.md`
- `docs/architecture/management-list-api-contract.md`

## Execution Order

| Wave | Task | Owner | Reviewer | Summary |
|---|---|---|---|---|
| 0 | `MGMT-ADJ-000` | Codex | Claude | Archive the plan and preserve the completed `MGMT-GAP-*` closeout state. |
| 1 | `MGMT-ADJ-001` | Claude2 | Codex | Slim `/bff/management/persona-fleet` and migrated list envelopes. |
| 1 | `MGMT-ADJ-002` | Claude | Codex2 | Replace board-pack full child payloads with summary contracts. |
| 2 | `MGMT-ADJ-003` | Codex2 | Claude | Differentiate registry/list UI pages with domain-specific table contracts. |
| 2 | `MGMT-ADJ-004` | Gemini | Claude2 | Recluster decision and operations queues into coherent workbench flows. |
| 3 | `MGMT-ADJ-005` | Codex | Claude2 | Burn down write-CTA source-scan warnings to receipts or explicit disablement. |
| 3 | `MGMT-ADJ-006` | Gemini2 | Codex | Decide runner-vs-demotion for Formula Studio, Skill Sandbox, Tools, MCP, and Skills. |
| 4 | `MGMT-ADJ-007` | Claude | Codex | Deepen ranking, alpha factory, lineage, workflow, hook, and knowledge flows. |
| 5 | `MGMT-ADJ-008` | Codex | Claude | Refresh hosted acceptance, list-contract audit, load gate, and final closeout evidence. |

## Dependency Order

```text
MGMT-ADJ-000: none
MGMT-ADJ-001: MGMT-ADJ-000
MGMT-ADJ-002: MGMT-ADJ-000
MGMT-ADJ-003: MGMT-ADJ-001
MGMT-ADJ-004: MGMT-ADJ-001, MGMT-ADJ-002
MGMT-ADJ-005: MGMT-ADJ-000
MGMT-ADJ-006: MGMT-ADJ-005
MGMT-ADJ-007: MGMT-ADJ-003, MGMT-ADJ-005, MGMT-ADJ-006
MGMT-ADJ-008: MGMT-ADJ-001, MGMT-ADJ-002, MGMT-ADJ-003,
              MGMT-ADJ-004, MGMT-ADJ-005, MGMT-ADJ-006,
              MGMT-ADJ-007
```

## Global Acceptance

Every `MGMT-ADJ-*` task must record:

1. branch and PR target;
2. exact local validation commands and output summary;
3. reviewer approval;
4. merge commit SHA;
5. hosted FE/BFF evidence when runtime behavior changes;
6. list-contract and payload evidence when a management read contract changes;
7. hosted route/control evidence when a visible UI surface changes;
8. residual risks with owner and expiry.

## Guardrails

- Do not mark any adjustment task done solely on local render success.
- Do not weaken `MGMT-GAP-006` hosted acceptance or `MGMT-GAP-010` release load
  gates.
- Do not add a new Management list endpoint smell. Run:

```sh
python3 scripts/audit_management_list_contract.py \
  --baseline docs/architecture/management-list-contract-baseline.json \
  --fail-on-new
```

- Do not enable mock runners or local-only success toasts as production actions.
- Keep route aliases redirect-only unless a task explicitly proves canonical
  rendering and updates the hosted route harness.

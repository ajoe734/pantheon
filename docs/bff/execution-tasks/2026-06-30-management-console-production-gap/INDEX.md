# Management Console Production Gap Execution Packet - 2026-06-30

Status: production closeout complete; post-closeout adjustment packet linked

Source gap spec:

- `docs/04/pantheon_management_console_gap_2026-06-30/README.md`

Evidence archive:

- `docs/04/pantheon_management_console_gap_2026-06-30/archive/live-audit-2026-06-30.md`
- `docs/04/pantheon_management_console_gap_2026-06-30/archive/full-reaudit-addendum-2026-07-01.md`
- `docs/04/pantheon_management_console_gap_2026-06-30/archive/route-control-reaudit-2026-07-01.md`
- `docs/04/pantheon_management_console_gap_2026-06-30/archive/route-control-reaudit-2026-07-01.json`
- `docs/04/pantheon_management_console_gap_2026-06-30/archive/management-adjustment-development-plan-2026-07-02.md`

Post-closeout adjustment packet:

- `docs/bff/execution-tasks/2026-07-02-management-console-adjustment-development/INDEX.md`

Active tracking:

- `docs/bff/execution-tasks/2026-06-30-management-console-production-gap/DISPATCH_TRACKING.md`

Frontend artifact note:

- `frontend-checkout:...` means the active FE checkout audited at
  `/home/lupin/code/pantheon/.fe-ep`, not a literal path under this repository.

## Dispatch Command

```sh
python3 scripts/dispatch_management_console_gap_2026-06-30.py
```

## Execution Order

| Batch | Task | Owner | Reviewer | Summary |
|---|---|---|---|---|
| 1 | `MGMT-GAP-001` | Codex2 | Claude | Remove hidden legacy routes and reduce first-level management nav overload. |
| 2 | `MGMT-GAP-003` | Claude2 | Codex | Harden canonical BFF management endpoint DTOs and tests. |
| 2 | `MGMT-GAP-002` | Claude | Codex | Rewire FE reads to canonical management endpoints. |
| 2.5 | `MGMT-GAP-008` | Claude | Codex | Fix live-id detail DTO/render honesty: no undefined, blank h1/owner/update, `NaN%`, or seed-id leaks. |
| 2.5 | `MGMT-GAP-009` | Claude2 | Codex | Align `/bff/me`, tenant, roles, and management reads so session/RBAC state is coherent. |
| 3 | `MGMT-GAP-004` | Codex | Claude2 | Replace toast/mock/local write flows with governed command receipts or disabled states. |
| 4 | `MGMT-GAP-005` | Gemini | Claude | Make studios/capabilities real via runtime-backed runners or demote them. |
| 5 | `MGMT-GAP-010` | Gemini2 | Codex | Turn the management load-gap follow-up into bundle, shell-fanout, and hosted route-ready release gates. |
| 5 | `MGMT-GAP-006` | Gemini2 | Codex | Add hosted management production acceptance harness. |
| 5 | `MGMT-GAP-007` | Codex | Claude | Track closeout to production level, archive final proof, and verify deployment. |

## Second-Pass Route/Control Inputs

The supplemental 2026-07-01 route/control re-audit must be treated as execution
input, not just background reading.

| Evidence | Task impact |
|---|---|
| 93 route samples: 53 visible nav, 40 detail/hidden/alias | `MGMT-GAP-006` harness must cover the same route classes on hosted FE. |
| 510 buttons, 42 disabled controls | `MGMT-GAP-004` must burn down enabled write-like CTAs and prove disabled reasons. |
| 10 mock-visible routes | `MGMT-GAP-005` and `MGMT-GAP-006` must demote, gate, or fail mock-as-live behavior. |
| Direct-render detail aliases for capital pools, ranking formulas, rebalances, and research | `MGMT-GAP-008` must redirect or canonicalize these detail routes. |
| Cockpit/LLM Provider Auth localhost CORS noise | `MGMT-GAP-006` must run on hosted origin and classify expected degraded auth separately from real console failure. |
| Large management build and chunk warnings | `MGMT-GAP-010` and `MGMT-LOAD-*` must enforce bundle/load release gates. |
| Source scan: `runActionSafe`, `bffWrites`, `toast.success`, `writeOverlay` | `MGMT-GAP-004` must prove command receipts or explicit non-production disablement. |

## Dependencies

```text
MGMT-GAP-001: none
MGMT-GAP-003: none
MGMT-GAP-002: MGMT-GAP-003
MGMT-GAP-004: MGMT-GAP-002, MGMT-GAP-003
MGMT-GAP-005: MGMT-GAP-003
MGMT-GAP-008: MGMT-GAP-002, MGMT-GAP-003
MGMT-GAP-009: MGMT-GAP-003
MGMT-GAP-010: MGMT-GAP-001, MGMT-GAP-002
MGMT-GAP-006: MGMT-GAP-001, MGMT-GAP-002, MGMT-GAP-004, MGMT-GAP-005, MGMT-GAP-008, MGMT-GAP-009, MGMT-GAP-010
MGMT-GAP-007: MGMT-GAP-006
```

## Global Acceptance

Every task must record:

1. branch and PR target;
2. local validation command output;
3. reviewer approval;
4. merge commit SHA;
5. hosted FE/BFF evidence when the task affects runtime behavior;
6. residual risks with owners and expiry.

The full packet is not production-level until `MGMT-GAP-007` closes with a final
archive and dev FE `/deployment.json` points at the merged `dev` commit.

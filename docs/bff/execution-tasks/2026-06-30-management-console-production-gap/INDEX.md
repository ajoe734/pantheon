# Management Console Production Gap Execution Packet - 2026-06-30

Status: ready for fleet dispatch

Source gap spec:

- `docs/04/pantheon_management_console_gap_2026-06-30/README.md`

Evidence archive:

- `docs/04/pantheon_management_console_gap_2026-06-30/archive/live-audit-2026-06-30.md`

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
| 3 | `MGMT-GAP-004` | Codex | Claude2 | Replace toast/mock/local write flows with governed command receipts or disabled states. |
| 4 | `MGMT-GAP-005` | Gemini | Claude | Make studios/capabilities real via runtime-backed runners or demote them. |
| 5 | `MGMT-GAP-006` | Gemini2 | Codex | Add hosted management production acceptance harness. |
| 5 | `MGMT-GAP-007` | Codex | Claude | Track closeout to production level, archive final proof, and verify deployment. |

## Dependencies

```text
MGMT-GAP-001: none
MGMT-GAP-003: none
MGMT-GAP-002: MGMT-GAP-003
MGMT-GAP-004: MGMT-GAP-002, MGMT-GAP-003
MGMT-GAP-005: MGMT-GAP-003
MGMT-GAP-006: MGMT-GAP-001, MGMT-GAP-002, MGMT-GAP-004, MGMT-GAP-005
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

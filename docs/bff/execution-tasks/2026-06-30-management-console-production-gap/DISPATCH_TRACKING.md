# Management Console Gap Dispatch Tracking - 2026-07-01

Status: active production tracking

This file ties the 2026-06-30 gap packet and 2026-07-01 full re-audit to the
fleet execution queue. It exists so the work is tracked by production evidence,
not by route-render impressions.

Supplemental evidence added after the second-pass route/control audit:

- `docs/04/pantheon_management_console_gap_2026-06-30/archive/route-control-reaudit-2026-07-01.md`
- `docs/04/pantheon_management_console_gap_2026-06-30/archive/route-control-reaudit-2026-07-01.json`

## Current Batch State

| Task | Fleet | Owner | Reviewer | State | Production evidence required |
|---|---|---|---|---|---|
| `MGMT-GAP-001` | frontend route/IA | Codex2 | Claude | Done | PR #120, deployed FE commit, hosted route probe |
| `MGMT-GAP-002` | frontend BFF reads | Claude | Codex | Done | execute-plans PR #124/#126, deployed FE commit, endpoint capture |
| `MGMT-GAP-003` | BFF DTO contracts | Claude2 | Codex | Done | Pantheon PR #2649, dev BFF deploy, OpenAPI/curl evidence |
| `MGMT-GAP-004` | command truth | Codex | Claude2 | Todo | all in-scope CTAs return command/audit receipt or disabled state; second-pass hotspots and `toast.success`/`writeOverlay` scan burned down |
| `MGMT-GAP-005` | studios/capabilities | Gemini | Claude | Todo | runner-backed traces/jobs or nav demotion/disable evidence; 10 mock-visible route findings resolved |
| `MGMT-GAP-008` | detail render honesty | Claude | Codex | Todo | live-id detail probe with no undefined/blank/NaN/seed-id leak; direct-render detail aliases redirected/canonicalized |
| `MGMT-GAP-009` | session/RBAC contract | Claude2 | Codex | Todo | `/bff/me`, provider auth, tenant, roles, and management reads agree for documented token/tenant |
| `MGMT-GAP-010` | load/release gate | Gemini2 | Codex | Todo | bundle budget, route-ready probe, shell request-count evidence; build warnings and large chunks are gated |
| `MGMT-GAP-006` | acceptance harness | Gemini2 | Codex | Waiting | hosted strict-live harness covering all remaining gap detectors plus the 93-route/510-button route-control crawl shape |
| `MGMT-GAP-007` | final closeout | Codex | Claude | Waiting | final archive with PRs, SHAs, deploy, BFF, harness, residual risks |

## Dependency Order

```text
MGMT-GAP-001: done
MGMT-GAP-002: done after MGMT-GAP-003
MGMT-GAP-003: done
MGMT-GAP-004: unblocked by MGMT-GAP-002, MGMT-GAP-003
MGMT-GAP-005: unblocked by MGMT-GAP-003
MGMT-GAP-008: unblocked by MGMT-GAP-002, MGMT-GAP-003
MGMT-GAP-009: unblocked by MGMT-GAP-003
MGMT-GAP-010: unblocked by MGMT-GAP-001, MGMT-GAP-002
MGMT-GAP-006: waits for MGMT-GAP-004, MGMT-GAP-005, MGMT-GAP-008,
              MGMT-GAP-009, MGMT-GAP-010
MGMT-GAP-007: waits for MGMT-GAP-006
```

## Watch Rules

1. A task is not production-level until its branch is merged, its target deploy
   is verified, and its evidence is archived.
2. Local route render, local Playwright, and localhost strict-live probes are
   supporting evidence only. Hosted FE/BFF evidence is required for closeout.
3. The final harness must fail on mock success, unavailable-as-success,
   `undefined`, `NaN`, session/RBAC mismatch, old alias duplicate render, and
   silent local-only write success.
4. Any superseded task must name the replacement PR, reviewer, merge SHA, and
   archive location.
5. `MGMT-GAP-007` cannot close until every row above is terminal or explicitly
   superseded by reviewed production evidence.
6. The raw route/control artifact is not closeout by itself. It is an input that
   the hosted `MGMT-GAP-006` harness must reproduce or intentionally supersede
   with broader coverage.

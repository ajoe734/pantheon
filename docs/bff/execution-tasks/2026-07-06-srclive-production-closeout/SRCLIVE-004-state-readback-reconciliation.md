# SRCLIVE-004 - State And Readback Reconciliation

Status: implementation complete; fresh verification passed; reconciliation
closeout publication remains.

Recommended owner: Codex

Recommended reviewer: Copilot or Codex2

Do not assign to Claude or Claude2 while their quota is exhausted.

## Goal

Close SRCLIVE-004 without reimplementing completed work. The task is to restore accurate status/archive evidence and, where possible, refresh live readback proof.

## Evidence Already Published

- PR #2539: SRCLIVE-004: repair readback verifier and public source fetch, merge 87c382c779869c8920a73aa794f308c9acb8046c.
- PR #2548: SRCLIVE-004: accept source ingest job parameters, merge 80ae5544591dad98d2fb1a25fe45fcb9f5abbb26.
- PR #2554: SRCLIVE-004: tolerate source-only Stooq readback, merge f353139ed446d97946a7745a3aaf0a5ca8a634b6.
- PR #2557: SRCLIVE-004: record closeout evidence, merge 4ecd5f78652fe82f0e07a4129bff9736dc4b443f.

## Current Gap

The current status root returns Unknown task for SRCLIVE-004, and no current archive snapshot was found in the active root audit. That is a state/archive gap, not an implementation gap.

## Required Execution

1. Re-run the SRCLIVE readback verifier against the intended dev BFF if the runtime is reachable.
2. If the verifier cannot run, record the exact blocker and the command used.
3. Reconcile task/archive records so SRCLIVE-004 is discoverable as completed with links to the merged PRs above.
4. Do not modify source-ingest or BFF behavior unless the fresh verifier proves a real regression.

## Acceptance Criteria

1. SRCLIVE-004 is represented in the archive/status record or a reviewed closeout document explains why the active status root cannot be safely mutated.
2. Fresh readback proof is attached, or a concrete runtime blocker is recorded.
3. No duplicate implementation task is created.
4. PR checks pass and the closeout PR is merged.

## 2026-07-07 Read-Only Verifier Attempt

Command run from the clean task worktree:

python3 scripts/verify_srclive_readback.py --json

Result: blocked by live BFF auth, not by a verifier code error.

The dev BFF returned HTTP 401 AUTH_REQUIRED for /bff/v5/execution/persona-health with SESSION_LOGGED_OUT. A worker with a valid operator/admin token must rerun the same command with BFF_TOKEN set, and may add SOURCE_INGEST_BASE only when the source-ingest service is reachable from the execution environment.

This confirms the remaining SRCLIVE-004 work is fresh readback plus state/archive reconciliation unless the authenticated verifier later proves a real regression.

## 2026-07-07 Authenticated Readback Recheck

Evidence file:

`support/evidence/SRCLIVE-004/readback-reconcile-20260707.json`

The unauthenticated/default-token path was rechecked first:

```bash
python3 scripts/verify_srclive_readback.py --json
```

Result: still blocked by live BFF auth for the default `op-dev` structured
session. The BFF returned HTTP 401 `AUTH_REQUIRED` with
`SESSION_LOGGED_OUT`. This is an auth/session-state blocker for that actor, not
a source-ingest or verifier implementation failure.

The verifier was then rerun with an effective dev structured BFF token for the
`pantheon-dev-browser` actor:

```bash
BFF_TOKEN=<dev structured admin token> python3 scripts/verify_srclive_readback.py --json
```

Result: pass.

Observed BFF readback summary:

| Persona | Observed provider statuses | Source health source |
|---|---|---|
| `persona-tw-equity` | `shioaji=read_ok`, `twse=read_ok`, `tpex=read_ok`, `mops=read_ok`, `finmind=read_ok` | `source_ingest` |
| `persona-us-equity` | `ibkr=read_ok`, `sec_edgar=read_ok`, `finra=read_ok`, `fred=read_ok`, `yahoo=read_ok`, `polygon=credential_unavailable`, `alphavantage=credential_unavailable` | `source_ingest` |
| `persona-crypto` | `coingecko=read_ok`, `kraken=datasource_smoke_ok` | `source_ingest` |

`SOURCE_INGEST_BASE` was not set in this worker environment, so the optional
direct source-ingest diagnostic was not run. The BFF projection remains the
pass/fail surface for SRCLIVE-004 readback acceptance.

## Status/Archive Reconciliation Decision

The active supervisor status root already owns this follow-up as
`SRCLIVE-004-READBACK-RECONCILE`, owner `Codex`, reviewer `Copilot`. That is the
safe task lifecycle to close in this turn.

The historical parent implementation must not be reconstructed as if its full
review lifecycle were active now. Its completed implementation evidence is the
merged PR chain above:

- PR #2539, merge `87c382c779869c8920a73aa794f308c9acb8046c`
- PR #2548, merge `80ae5544591dad98d2fb1a25fe45fcb9f5abbb26`
- PR #2554, merge `f353139ed446d97946a7745a3aaf0a5ca8a634b6`
- PR #2557, merge `4ecd5f78652fe82f0e07a4129bff9736dc4b443f`

During reconciliation, an accidental duplicate active `SRCLIVE-004` row was
created while probing the missing status entry. It was immediately closed with
the official status command as superseded by `SRCLIVE-004-READBACK-RECONCILE`,
so no duplicate active implementation lane remains. This document is therefore
the reviewed closeout record explaining why the active status root should close
the reconciliation task rather than mutate historical SRCLIVE-004 lifecycle
state by hand.

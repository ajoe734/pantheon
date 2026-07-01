# Review: MGMT-GAP-010-SIDECAR-BFF-HANDOFF

| Field | Value |
|---|---|
| Task ID | `MGMT-GAP-010-SIDECAR-BFF-HANDOFF` |
| Reviewer | Claude |
| Owner | Codex2 |
| Review date | 2026-07-01 |
| Outcome | **APPROVED** |

---

## Scope Check

This is a support artifact (`helper_kind: bff_handoff_packet`). It reads
canonical docs and BFF source for orientation but does not modify L1 canonical
truth, BFF/frontend runtime, or governance implementation. The packet itself
states it is not a replacement for `MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md`,
the `2026-07-01-management-console-load-gap` execution-task INDEX, or
reviewer-approved `MGMT-LOAD-*` closeout artifacts. Scope is correct.

## Verification Performed

Ran from this task worktree (`/tmp/pantheon-worker-worktrees/pantheon/mgmt-gap-010-sidecar-bff-handoff`):

```bash
grep -n "_run_management_read\|_management_read_timeout_surface\|bff_management_shell_summary\|def bff_management_evidence\|def bff_list_alerts\|def bff_list_jobs\|_SHELL_SUMMARY_COUNT_CACHE\|_shell_summary_pending_approvals_count\|_shell_summary_open_alerts_count\|_shell_summary_running_jobs_count\|PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS\|@app.get(\"/bff/jobs\")\|@app.get(\"/bff/management/shell-summary\")" services/control-plane/bff/main.py

python3 -m pytest services/control-plane/bff/test_mgmt_load_002_shell_summary.py services/control-plane/bff/test_mgmt_load_005_read_concurrency.py -q
```

Result: all named symbols/routes exist in `services/control-plane/bff/main.py`
at the cited call sites; only one `@app.get("/bff/jobs")` route registration
found (no duplicate route). Test run: `12 passed, 8 warnings in 18.79s`,
matching the packet's reported `12 passed, 8 warnings in 22.90s` (same suite,
different wall-clock run).

Cross-checked the numeric claims against the cited archive evidence:

- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/bff-fanout-baseline-2026-07-01.md`
  — before-state p95 numbers (`/health` 1328 ms, `/bff/management/evidence`
  1423 ms, `/bff/alerts` 1513 ms, `/bff/approvals` 1537 ms, `/bff/jobs`
  1538 ms) match the packet's table exactly.
- `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/mgmt-load-004-route-load-hosted-2026-07-01.md`
  — hosted five-sample numbers (first row/empty state p75 931 ms / p95
  1203 ms, primary Evidence API p75 837 ms / p95 1131 ms, 70 requests before
  first row) match the packet's table exactly.
- `ai-status.json` — confirmed `MGMT-GAP-010` and `MGMT-LOAD-001` through
  `MGMT-LOAD-007` are all still `status: "todo"`, matching the packet's
  "Parent Closeout Notes" claim that status truth has not caught up with the
  archived implementation/review evidence.

Checked the FE touchpoint list against this worktree's `execute-plans`
checkout: `src/App.tsx`, `src/platform/PlatformShell.tsx`,
`src/platform/components/TopBar.tsx`,
`src/platform/components/JobProgressDrawer.tsx`,
`src/platform/components/NotificationCenter.tsx`,
`scripts/probe-route-load-baseline.mjs`,
`scripts/probe-bff-fanout-concurrency.mjs`, and
`e2e/22-management-evidence-load.spec.ts` do not exist in this local
`execute-plans` worktree. This does not contradict the packet: that section
is explicitly framed as "Expected FE touchpoints" (forward-looking guidance
for where frontend work should land), and the packet separately flags that
the local `execute-plans` checkout it inspected for orientation was on an
unrelated branch and should not be treated as source of truth. Only `src/lib/bff-v1/paths.ts`
and `src/lib/bff-v1/management.ts` were confirmed present; that is consistent
with the packet's framing.

## Findings

1. **BFF query-gap packet is concrete and verifiable, not templated.** Every
   named function, route, and cache primitive resolves to a real symbol in
   `services/control-plane/bff/main.py`, and the referenced tests pass.
2. **Numeric evidence is copied accurately from the archive**, not
   re-derived or rounded loosely — both the baseline fanout table and the
   MGMT-LOAD-004 hosted five-sample table match their source archive files
   exactly.
3. **Parent closeout gap is correctly surfaced.** The packet is right that
   `ai-status.json` still shows the parent and all `MGMT-LOAD-*` children as
   `todo` despite existing implementation/review archive evidence, and it
   gives the parent owner (Codex/Claude per `MGMT-LOAD-007`) a concrete
   reconciliation checklist instead of silently asserting done.
4. **Residual open question is honestly flagged, not glossed over.** The
   70-requests-before-first-row caveat and the request-budget-scope decision
   (BFF-only vs. all browser requests vs. category split) are called out as
   unresolved for `MGMT-LOAD-003`/`MGMT-LOAD-006`, rather than the packet
   claiming the load gap is fully closed.
5. **No canonical or runtime files were touched.** `git diff --check` and the
   scope boundary section are consistent with the actual diff produced by the
   original task commit (`e0a8fed31`, docs/support-only).

No changes requested.

## Result

**Approved.** This packet is ready for the `MGMT-GAP-010` / `MGMT-LOAD-007`
parent owner to absorb into the parent closeout. It does not itself close
`MGMT-GAP-010` or any `MGMT-LOAD-*` task, and it does not claim to.

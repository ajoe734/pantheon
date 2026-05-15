# BFF-FINAL-SIDECAR-GEMINI-SMOKE-MATRIX

Owner: Codex2
Reviewer: Codex
Depends on: BFF-FINAL-001
Parent: BFF-FINAL-010
Helper kind: smoke_matrix
Mutates canonical code: no
Last updated: 2026-05-08T02:26:08Z

## Scope

Prepare a support-only smoke and CI matrix for `BFF-FINAL-010`. This sidecar
does not define canonical contract truth and must not edit BFF runtime,
registry, governance, L1 policy, or canonical contract files.

## Source Inputs

- Task brief: `.orchestrator/task-briefs/bff_final_sidecar_gemini_smoke_matrix.md`
- Live task state: `ai-status.json`
- Existing BFF-FINAL artifacts under `docs/bff/execution-tasks/2026-05-07-final/`
- Current BFF test inventory under `services/control-plane/bff/`
- Existing BFF release verifier: `scripts/verify_bff_local_release.py`

## Current Gate Snapshot

As of this sidecar pass:

- `BFF-FINAL-010` is still gated on unfinished mainline work.
- `BFF-FINAL-006` is `in_progress`; the expected task artifact
  `docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-006-mcp-tool-import.md`
  is not present in the tracked docs snapshot.
- `BFF-FINAL-009` is `in_progress`; the expected task artifact
  `docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-009-v5-interventions.md`
  is not present in the tracked docs snapshot.
- Existing focused coverage is already visible for final primitives,
  command idempotency/envelope, precondition errors, action catalog, SSE,
  evidence redaction, and Agora journal merge patch.

Closeout confirmation at `2026-05-08T02:26:08Z`:

- Reviewer approved this as a support-only smoke matrix for `BFF-FINAL-010`.
- Current worktree exposes `services/control-plane/bff/test_mcp_tool_import.py`
  for `BFF-FINAL-006` and
  `services/control-plane/bff/test_v5_interventions.py` for `BFF-FINAL-009`.
- Parent owner must still use the exact owner-provided files at final gate time;
  this sidecar remains a checklist, not implementation proof.

## Focused Smoke Matrix

Run these before the full BFF suite once the relevant task has landed. If a row
is marked pending, `BFF-FINAL-010` should block final handoff until the owning
task provides the concrete test file or updates the row.

| Task area | Smoke purpose | Command | Gate status |
|---|---|---|---|
| BFF-FINAL-001 foundation | Final success statuses, required `CommandResponse.data`, final error codes, legacy idempotency conflict mapping | `python3 -m pytest services/control-plane/bff/test_final_contract_primitives.py -q` | Ready |
| BFF-FINAL-002 command envelope | `/bff/v1/commands` header idempotency, alias precedence, body-key rejection, replay, conflict, final response envelope, legacy route compatibility | `python3 -m pytest services/control-plane/bff/test_governance_command_submission.py -k "bff_v1_commands" -q` | Ready |
| BFF-FINAL-002 executor regression | Command dispatch and executor behavior for governed command types | `python3 -m pytest services/control-plane/bff/test_command_executor.py -q` | Ready |
| BFF-FINAL-003 preconditions | Missing confirm token, approval evidence, and two-man evidence return non-2xx final envelopes without command persistence | `python3 -m pytest services/control-plane/bff/test_final_precondition_errors.py -q` | Ready |
| BFF-FINAL-004 action catalog | Catalog completeness, governance metadata, endpoint auth, descriptor projection, no `requires_*` success status leakage | `python3 -m pytest services/control-plane/bff/test_action_catalog.py -q` | Ready |
| BFF-FINAL-005 SSE approval/ask | Final channel catalog, approval/ask payload models, replay success, `SSE_REPLAY_UNAVAILABLE`, replay metadata headers | `python3 -m pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py -q` | Ready |
| BFF-FINAL-006 MCP import | Import-tools endpoint, no standalone tool create path, tool-action admission, import idempotency/replay/conflict | Closeout-observed file: `python3 -m pytest services/control-plane/bff/test_mcp_tool_import.py -q` | Observed at closeout; parent must confirm owner landing |
| BFF-FINAL-007 evidence redaction | Capability redaction helper and endpoint-level redacted evidence refs, required capability, reason, redaction telemetry | `python3 -m pytest services/control-plane/bff/test_kw03_evidence_refs_contract.py -q` | Ready |
| BFF-FINAL-008 Agora journal patch | Merge Patch content type, body idempotency rejection, required `data`, audit diff, idempotency conflict | `python3 -m pytest services/control-plane/bff/test_agora_journal_merge_patch.py -q` | Ready with replay note below |
| BFF-FINAL-009 v5 interventions | Canonical `/bff/v5/interventions` route, HIQ Sentinel remediation guard, two-man semantics, command side effects gated | Closeout-observed file: `python3 -m pytest services/control-plane/bff/test_v5_interventions.py -q` | Observed at closeout; parent must confirm owner landing |

## Cross-Surface Pre-Final Smoke

After all pending rows have concrete tests, use a single pre-final smoke command
that exercises the final contract paths without waiting for the full suite:

```bash
python3 -m pytest \
  services/control-plane/bff/test_final_contract_primitives.py \
  services/control-plane/bff/test_governance_command_submission.py \
  services/control-plane/bff/test_command_executor.py \
  services/control-plane/bff/test_final_precondition_errors.py \
  services/control-plane/bff/test_action_catalog.py \
  services/control-plane/bff/test_pkt005_sse_substrate_contract.py \
  services/control-plane/bff/test_kw03_evidence_refs_contract.py \
  services/control-plane/bff/test_agora_journal_merge_patch.py \
  services/control-plane/bff/test_mcp_tool_import.py \
  services/control-plane/bff/test_v5_interventions.py \
  -q
```

If the MCP or interventions test filenames differ, substitute the owner-provided
exact files. Do not silently drop those rows from the pre-final smoke.

## Full-Suite And Release Gate

`BFF-FINAL-010` should record exact output for these gates in its delivery note:

```bash
python3 -m pytest services/control-plane/bff -q
python3 scripts/verify_bff_local_release.py --json
git status --short
```

Notes:

- `python3 -m pytest services/control-plane/bff -q` is the authoritative BFF
  full-suite gate for this final-contract pass.
- `scripts/verify_bff_local_release.py --json` is useful release hardening
  evidence, but its baked-in step list is narrower than the final BFF contract
  matrix. It must supplement, not replace, the full BFF pytest gate.
- `git status --short` must be captured so the parent owner can separate
  task-owned final delivery artifacts from unrelated dirty worktree changes.

## Replay Prerequisites

Replay behavior must be explicitly proven before final handoff:

| Replay surface | Minimum evidence needed for final gate |
|---|---|
| `/bff/v1/commands` idempotency replay | Same `Idempotency-Key` plus same stable body returns the prior `CommandResponse`; same key plus different body returns HTTP 409 `IDEMPOTENCY_CONFLICT`. Current focused command: `test_governance_command_submission.py -k "bff_v1_commands"`. |
| SSE replay | `_replay_from` returns only events after `last_event_id`; missing/beyond-window replay returns HTTP 409 `SSE_REPLAY_UNAVAILABLE` with resync routes and replay metadata. Current focused command: `test_pkt005_sse_substrate_contract.py`. |
| Agora journal patch replay | Same `Idempotency-Key` plus same merge patch should replay deterministically; same key plus different patch must return HTTP 409 `IDEMPOTENCY_CONFLICT`. Current focused file visibly covers success and conflict; `BFF-FINAL-010` should confirm or add explicit same-payload replay coverage if it is not present after final merge. |
| MCP import-tools replay | Import with the same descriptor/key should be idempotent; same key with changed import payload should conflict; standalone tool creation must remain absent. Pending `BFF-FINAL-006` owner test landing. |
| v5 interventions replay/guarding | Intervention remediation or two-man command paths should prove repeated admissible request behavior and missing two-man/precondition behavior without duplicate side effects. Pending `BFF-FINAL-009` owner test landing. |

## Owner Closeout Verification

Owner closeout was limited to this support artifact and verification of the
listed matrix files:

```bash
git diff --check -- docs/bff/execution-tasks/2026-05-07-final/sidecars/BFF-FINAL-SIDECAR-GEMINI-SMOKE-MATRIX.md
python3 -m pytest --collect-only services/control-plane/bff/test_final_contract_primitives.py services/control-plane/bff/test_governance_command_submission.py services/control-plane/bff/test_command_executor.py services/control-plane/bff/test_final_precondition_errors.py services/control-plane/bff/test_action_catalog.py services/control-plane/bff/test_pkt005_sse_substrate_contract.py services/control-plane/bff/test_kw03_evidence_refs_contract.py services/control-plane/bff/test_agora_journal_merge_patch.py services/control-plane/bff/test_mcp_tool_import.py services/control-plane/bff/test_v5_interventions.py -q
git status --short
```

Results:

- `git diff --check` passed.
- `pytest --collect-only` collected 105 tests from the current matrix files.
- `git status --short` showed unrelated dirty worktree changes outside this
  sidecar; this task-owned artifact is this file only.

## CI Matrix Recommendation

For the final BFF contract branch, split CI into three lanes:

| Lane | When | Command set | Fails final handoff |
|---|---|---|---|
| Focused final contract | Every BFF-FINAL task merge or review handoff | The ready rows from the focused smoke matrix plus newly landed 006/009 tests | Yes |
| Full BFF suite | Before `BFF-FINAL-010` delivery note and coordination response | `python3 -m pytest services/control-plane/bff -q` | Yes |
| Release hardening | Before publication/push evidence | `python3 scripts/verify_bff_local_release.py --json` | Yes, but not sufficient alone |

## Handoff To BFF-FINAL-010

The parent owner should consume this sidecar as a checklist, not as proof that
the implementation is complete. Required parent actions:

1. Verify that pending `BFF-FINAL-006` and `BFF-FINAL-009` rows have concrete
   task artifacts and exact focused tests.
2. Run all ready focused smoke rows plus the owner-provided pending rows.
3. Run the full BFF suite and release hardening command.
4. Record command output summaries, commit hash, dirty worktree scope, and push
   status in the final delivery note and coordination response.
5. Treat zero collected tests, missing test files, or substituted broad `-k`
   patterns as blockers unless the owning task artifact explicitly justifies
   the replacement.

## Acceptance

- Focused smoke matrix prepared.
- Final gate commands listed.
- Replay prerequisites made explicit.
- Pending owner-owned coverage is called out instead of silently accepted.
- No canonical code, contract truth, registry, governance, or runtime files were
  edited by this sidecar.

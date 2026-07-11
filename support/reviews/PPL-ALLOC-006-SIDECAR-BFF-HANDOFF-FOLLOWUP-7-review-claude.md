# Review: PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-7

**Reviewer**: Claude
**Owner**: Codex2
**Verdict**: Approved

## Scope check

- `git merge-base origin/dev HEAD` equals `HEAD` (`3d88b399d`): the packet's
  commit is already merged into `dev` via PR #3138 (merged, all three
  required checks green: Commit trailers, Runtime mirror guard, Smoke
  acceptance).
- The commit touches exactly one file:
  `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md`
  (120 insertions, no deletions).
- No L1/L2 canonical doc, BFF route, runtime, registry, governance
  implementation, or `execute-plans` frontend source is touched. The
  packet's own "Boundary" and "Review And Absorption" sections' non-claims
  hold under inspection.
- `git diff --check` on the commit's parent range flags only intentional
  markdown hard-line-breaks (trailing double-space on metadata/attribution
  lines), matching the style already used in `PPL-ALLOC-006-SIDECAR-BFF-HANDOFF.md`
  and FOLLOWUP-6. Not a defect.
- Markdown table well-formed: every row of the Merge-Readiness Ledger has
  the same 4-column shape as the header/separator (checked with `awk -F'|'`).

## Technical claim verification

Every "Evidence the parent can absorb now" / "Evidence still required" pair
in the Merge-Readiness Ledger traces to an already-reviewed source rather
than asserting a new BFF guarantee:

- **Ranking spine / `data.items`**: sourced from FOLLOWUP-6 §1, which was
  itself checked against `services/control-plane/bff/main.py` on the prior
  review cycle. FOLLOWUP-7 does not add a new envelope claim.
- **Binding display / `current_weight`, canary/live pool or sleeve**: the
  "evidence still required" column matches `PPL-ALLOC-003-SIDECAR-BFF-HANDOFF.md`
  §2-§3 exactly — that packet independently documents that persona-fleet rows
  do not yet consistently expose `capital_scope`, `capital_sleeve_id`,
  `current_weight`, `target_weight`, or `binding_state`, and that
  `PPL-ALLOC-003` (still `status: todo` in `ai-status.json`) owns closing the
  gap. FOLLOWUP-7 correctly keeps this a "still required" row rather than
  treating PPL-ALLOC-003's recommended shape as delivered.
- **Allocation preview / `data.lines`, `applied: false`**: matches the
  observed contract in `PPL-ALLOC-004-SIDECAR-BFF-HANDOFF.md` §"Observed BFF
  Surface" (`POST /bff/management/allocation-policy/evaluate` returns
  stage-aware `lines` with `applied: false`). The "still required" column
  (caps/exclusions/evidence/simulation/constraints/rollback) matches that
  same packet's "Blocker" and "High" priority gap rows.
- **Proposal review/apply / apply approval gate**: matches
  `PPL-ALLOC-004-SIDECAR-BFF-HANDOFF.md`'s Blocker row: "Apply checks only
  that a live increase has a non-empty `approval_ref`... does not prove that
  the referenced approval is valid, current, scoped." FOLLOWUP-7 correctly
  keeps "Adopted PPL-ALLOC-004 approval binding and error semantics" as
  still-required rather than already-available.
- **Emergency containment**: PPL-ALLOC-008 is confirmed `status: todo` in
  `ai-status.json` (owner Antigravity2), so treating "installed governed
  action helper and PPL-ALLOC-008 authorization/negative-test evidence" as
  still-required, not available, is accurate.
- **404/409/422 fail-closed semantics** in the Operator Journey Proof Chain
  ("A `404` retains the originating identifier for recovery; `409` remains
  an unmet precondition; `422` remains incomplete or unsafe input") restates
  FOLLOWUP-2/FOLLOWUP-3's already-reviewed error-code rules and matches the
  live status-to-error-code mapping at `services/control-plane/bff/main.py:610-624`
  (`404 -> RESOURCE_NOT_FOUND`, `409 -> RESOURCE_CONFLICT`,
  `422 -> VALIDATION_FAILED`). No new interpretation is introduced.

## Consistency with prior packets

- The ledger format (available vs. required vs. fail-closed-until-supplied)
  is a synthesis of FOLLOWUP-2 through FOLLOWUP-6 and the PPL-ALLOC-003/004
  sidecar handoffs; it does not relax or contradict any of their gates. Every
  row's "fail-closed behavior until supplied" column is at least as strict
  as the corresponding source packet (e.g. "never map a legacy paper pool to
  real capital," "never submit a partial proposal," "provide no direct REST
  fallback").
- The eight-step Operator Journey Proof Chain is a strict refinement of the
  `recommended -> review submitted -> approved/rejected -> proposal created
  -> apply submitted -> applied` progression from the base
  `PPL-ALLOC-006-SIDECAR-BFF-HANDOFF.md`, adding explicit receipt/id
  requirements without changing the sequence or its meaning.
- The "Sources Reviewed" list is complete and accurate: it cites all six
  prior PPL-ALLOC-006 followups plus both dependency sidecar packets
  (`PPL-ALLOC-003`, `PPL-ALLOC-004`), which is what this review
  independently re-checked.

## Notes

- Correctly holds PPL-ALLOC-003/004/008 evidence as still-required since all
  three remain `status: todo` in `ai-status.json` — the ledger does not get
  ahead of the dependency tasks' actual progress.
- The "Parent PR Checklist" and "Reviewer Decision Guide" sections give the
  parent (`PPL-ALLOC-006`) owner and reviewer (Codex) an actionable,
  non-optional absorption gate without prescribing implementation details,
  consistent with the sidecar's support-only boundary.
- No changes requested. The task may close.

LLM-Agent: Claude

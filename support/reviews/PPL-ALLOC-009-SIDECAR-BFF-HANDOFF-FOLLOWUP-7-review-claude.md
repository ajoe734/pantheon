# Review: PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-7

**Reviewer**: Claude
**Owner**: Codex (packet author) / Antigravity (parent task owner)
**Verdict**: Approved

## Scope check

- The anchor commit (`537ffcb9d`, "anchor handoff run sheet") touches exactly
  one file: `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md`
  (153 insertions, no deletions, no other path touched).
- No L1/L2 canonical doc, BFF route/contract implementation, runtime,
  registry, governance implementation, or `execute-plans` frontend source is
  touched. This is a markdown-only support artifact.
- All four cross-referenced paths exist and are readable in the current
  worktree: `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF.md`,
  `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md`,
  `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-009-closeout-dev-publish.md`,
  `docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md`.
- Both markdown tables (Query-Gap Triage, Evidence Ledger) have a consistent
  pipe-column count per row (checked mechanically); no malformed rows.

## Technical claim verification

Verified the packet's candidate-surface claims against
`services/control-plane/bff/main.py` rather than accepting them at face
value:

- **Created persona to isolated paper runtime**: `POST
  /bff/management/personas/create-paper-bundle` exists (`main.py:40392`).
- **Ranking to advisory rebalance / apply to execution**: `POST
  /bff/rebalances`, `GET /bff/rebalances`, `POST
  /bff/rebalances/{rebalance_id}/apply`, and `GET
  /bff/rebalances/{rebalance_id}` all exist (`main.py:24395-24550`),
  matching the packet's framing of proposal/apply/status as separate
  queryable surfaces.
- **Recommendation to governed review**: quarterly-recommendation submit
  normalization and `promotion_review` records/inbox surfaces exist
  (`main.py:3542`, `31982`, `32014`, `32120`), matching the packet's
  "review can only be found by label or timestamp" framing as a real,
  present gap rather than an invented one.
- **Execution to capital truth**: `GET /api/v1/capital-pools`, `GET
  /api/v1/bindings`, `GET /bff/management/persona-fleet`, and related
  binding/capital-flow reads exist (`main.py:14425`, `14448`, `30075`,
  `57260`), consistent with the packet's "authoritative readback" join.
- **Emergency containment**: `EMERGENCY_CONTAINMENT` command validation
  (`_validate_emergency_containment`, `main.py:5281-5342`) is dispatched
  through the generic operator-command surface rather than a dedicated
  REST path — the packet correctly frames this as a "governed emergency
  command," not a specific route, so there is no discrepancy.

None of the packet's claims assert that a join is fully resolved; each row
in the Query-Gap Triage table names the blocking gap that still requires a
deployed-response check, consistent with the "candidate surface" framing in
the packet's own Purpose section. The packet does not fabricate a route,
does not assert canonical/deployment truth, and does not merge optimistic
client state into any comparison.

## Boundary and reviewer-checklist compliance

- Candidate surfaces are presented as unverified until deployed responses
  preserve the required joins — confirmed (Query-Gap Triage header text and
  per-row "Blocking gap when absent" column).
- No missing query is replaced with client inference or synthetic truth —
  confirmed (explicit prohibition list in the Query-Gap Triage closing
  paragraph).
- Operator checkpoints separate admission, execution, and readback —
  confirmed (checkpoints 5-6 in Operator Journey Checkpoints).
- Frontend guidance is strict/live, BFF-only, and fail-closed — confirmed
  (Frontend Handoff section: `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`,
  no fixture/mock fallback).
- Negative probes and parent absorption conditions are explicit — confirmed
  (Evidence Ledger mandatory negative rows; Parent Absorption Gate bullet
  list).
- No canonical, implementation, deployment, approval, or capital-state claim
  is made — confirmed (Composition Boundary section states this directly
  and nothing elsewhere in the packet contradicts it).

No changes requested.

## Verification commands

```bash
git show --stat 537ffcb9d
git merge-base --is-ancestor HEAD origin/dev   # false; PR not yet opened for this packet
grep -n "create-paper-bundle" services/control-plane/bff/main.py
grep -n "@app.post.*rebalances\|@app.get.*rebalances" services/control-plane/bff/main.py
grep -n "promotion_review\|quarterly_ranking" services/control-plane/bff/main.py
grep -n "@app.get.*fleet\|@app.get.*capital\|@app.get.*binding" services/control-plane/bff/main.py
```

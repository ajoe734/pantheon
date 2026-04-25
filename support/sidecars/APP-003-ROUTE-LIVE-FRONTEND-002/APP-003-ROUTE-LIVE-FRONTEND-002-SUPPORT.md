# APP-003-ROUTE-LIVE-FRONTEND-002 Support Note

**Task**: `APP-003-ROUTE-LIVE-FRONTEND-002`  
**Owner**: `Codex`  
**Reviewer**: `Codex2`  
**Scope**: publish the remaining route-live frontend activation packets for
Research, Knowledge, and Trainer modules without reopening closed BFF
implementation work.

## Summary

This execution slice covers eight live-route modules:

- `RW-02` Search
- `RW-04` Experiment Launch
- `RW-05` Artifact Compare
- `KW-02` Research Notes
- `KW-03` Evidence Refs
- `TW-01` Teaching Dialog
- `TW-02` Parameter Controls
- `TW-04` Teaching Replay

Repo truth after this pass:

- seven modules already had published module-local frontend handoff bundles
- `TW-02` was the missing module-local handoff gap and now has
  `docs/pantheon-handoffs/TW-02-parameter-controls/FRONTEND_CHANGE_SPEC.md`
- the Trainer packet family, workbench backlog, and frontend architecture docs
  now describe `TW-02` using the current live contract semantics:
  `status = accepted | rejected`, `field_errors[]`, `rejected_changes[]`, and
  `diff.updated_controls[]`
- this task does not reopen route implementation work for any of the eight
  modules; the remaining residue stays frontend activation / closeout only

Revalidated on `2026-04-23`: the module matrix below still matches the current
repo paths and the published `TW-02` handoff remains present.

## Module Matrix

| Module | Route-live truth | Frontend packet |
|---|---|---|
| `RW-02` | search route and index-adapter metadata live | `docs/pantheon-handoffs/RW-02-search/FRONTEND_CHANGE_SPEC.md` |
| `RW-04` | experiment launch/history/detail/cancel routes live | `docs/pantheon-handoffs/RW-04-experiment-launch/FRONTEND_CHANGE_SPEC.md` |
| `RW-05` | artifact list/detail/compare routes live | `docs/pantheon-handoffs/RW-05-artifact-compare/FRONTEND_CHANGE_SPEC.md` |
| `KW-02` | notes create/list/detail routes live | `docs/pantheon-handoffs/KW-02-research-notes/FRONTEND_CHANGE_SPEC.md` |
| `KW-03` | evidence browse/detail routes live | `docs/pantheon-handoffs/KW-03-evidence-refs/FRONTEND_CHANGE_SPEC.md` |
| `TW-01` | trainer session create/list/detail/message routes live | `docs/pantheon-handoffs/TW-01-teaching-dialog/FRONTEND_CHANGE_SPEC.md` |
| `TW-02` | trainer controls read/patch routes live; handoff bundle published in this task | `docs/pantheon-handoffs/TW-02-parameter-controls/FRONTEND_CHANGE_SPEC.md` |
| `TW-04` | replay list/detail plus commit/discard routes live | `docs/pantheon-handoffs/TW-04-teaching-replay/FRONTEND_CHANGE_SPEC.md` |

## Verification

- `pytest -q services/control-plane/bff/test_tw02_parameter_controls_contract.py`
  - result: `5 passed`
- `python3 -m json.tool docs/examples/TW-02-parameter-controls.json`
  - result: `OK`

## Reviewer Boundary

When reviewing this task, check only that:

- `TW-02` now has a module-local frontend activation packet
- the touched docs describe the live `TW-02` contract truthfully
- the backlog and Trainer-facing architecture docs no longer frame `TW-02` as a
  pending-BFF surface
- the task remains disjoint from `APP-003-ROUTE-LIVE-FRONTEND-001`
  (`CW-02`, `KW-04`, `KW-05`) and from any front-repo implementation loop

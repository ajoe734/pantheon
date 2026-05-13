# SVC-RENAME-003-SIDECAR-ACCEPTANCE Packet

Task ID: `SVC-RENAME-003-SIDECAR-ACCEPTANCE`
Parent task: `SVC-RENAME-003`
Helper kind: `acceptance_packet`
Owner: `Codex`
Reviewer: `Codex2`
Date: 2026-05-13 UTC

## Scope Boundary

This is a support-only sidecar packet. It prepares the acceptance checklist,
dependency map, and reviewer handoff for the parent `SVC-RENAME-003` namespace
normalization work. It does not modify L1 canonical truth, core contract truth,
runtime implementation, registry implementation, governance implementation, or
service namespace files.

Task-owned artifact:

- `support/sidecars/SVC-RENAME-003/SVC-RENAME-003-SIDECAR-ACCEPTANCE.md`

Adjacent parent evidence reviewed but not changed by this sidecar:

- `support/sidecars/SVC-RENAME-003/SVC-RENAME-003-OWNER-EVIDENCE.md`
- `support/sidecars/SVC-RENAME-003/review-svc-rename-003-claude.md`
- `docs/architecture/services-namespace-migration-map-2026-05-10.md`

## Parent Evidence Snapshot

The parent evidence currently records `SVC-RENAME-003` as a Pair A
`control_plane` snake/kebab compatibility slice:

- implementation files live under `services/control-plane/internal/`
- executable import shims live under `services/control_plane/internal/`
- top-level legacy wrappers remain under `services/control_plane/`
- runtime-manager and smoke import sites target `services.control_plane.internal.*`
- parent verification recorded `services/runtime-manager` as `74 passed, 4 warnings`
  and `tests/run_internal_api_smoke.py` as `SMOKE OK`
- Claude approved the parent task and accepted the documented task-scoped commit
  traceability exception

This packet does not re-approve the parent implementation. It packages the
current acceptance surface for `Codex2` review and for the parent owner to decide
whether to absorb into the main closeout material.

## Dependency Map

| Item | Current relationship | Acceptance impact |
|---|---|---|
| `SVC-RENAME-003` parent evidence | Owner evidence and Claude review already exist in the same support directory. | This sidecar should reference, not replace, that evidence. |
| Pair A `control_plane` -> `control-plane/internal` | Migration map identifies the hyphenated service path as non-importable Python and requires `services.control_plane.internal.*` loader modules. | Reviewer should check that acceptance language preserves the shim/import boundary and does not ask callers to import `services.control-plane`. |
| Legacy wrappers under `services/control_plane/` | Parent review confirms wrappers remain as transition files. | Acceptance should not require wrapper deletion in this slice. |
| Runtime-manager import sites | Parent review lists five rewritten executable import sites. | Acceptance should focus on those executable imports; incidental comments or historical review docs are not blockers. |
| Pair B `registry-core/decision-domain` -> `registry/decision_domain` | Migration map lists this as a separate follow-up (`SVC-RENAME-004`). Current repo inventory still shows `services/registry-core/decision-domain/`. | Do not absorb Pair B into this sidecar or treat it as required for `SVC-RENAME-003-SIDECAR-ACCEPTANCE`. |
| Pair E feedback split | Mentioned in namespace migration planning as separate risk/scope. | Out of scope for this packet unless the parent owner explicitly reopens scope. |

## Acceptance Checklist

For this sidecar to pass review:

- [x] Support artifact exists at the task-owned path.
- [x] Packet names `Codex` as owner and `Codex2` as assigned reviewer.
- [x] Packet states the support-only boundary and avoids canonical/runtime edits.
- [x] Packet includes a dependency map for the parent namespace-normalization work.
- [x] Packet separates parent Pair A evidence from future Pair B / Pair E work.
- [x] Packet gives the reviewer concrete checks without requiring full global
  history or `current-work.md`.
- [x] Assigned reviewer confirms the packet is accurate and moves the task to
  `review_approved` if acceptable.

## Reviewer Checks

Suggested focused review for `Codex2`:

1. Confirm this sidecar changed only support material for
   `SVC-RENAME-003-SIDECAR-ACCEPTANCE`.
2. Confirm no L1 canonical truth, core contract truth, runtime implementation,
   registry implementation, or governance implementation was modified by this
   sidecar.
3. Confirm the packet's parent evidence summary matches
   `SVC-RENAME-003-OWNER-EVIDENCE.md` and `review-svc-rename-003-claude.md`.
4. Confirm the dependency map keeps Pair B `registry-core/decision-domain` work
   out of this acceptance slice.
5. Confirm there are no remaining template placeholders in this task artifact.

## Handoff

Ready for `Codex2` review. If approved, `Codex2` should move the task to
`review_approved` and return it to `Codex` for closeout. The final owner closeout
should follow `.orchestrator/skills/task-closeout-finalization.md`, including a
task-scoped commit if the reviewed support changes are not already committed.

## Verification Performed

Commands run from `/home/lupin/code/pantheon` while preparing this support
packet:

```bash
sed -n '1,260p' .orchestrator/task-briefs/svc_rename_003_sidecar_acceptance.md
jq '.tasks[] | select(.id=="SVC-RENAME-003-SIDECAR-ACCEPTANCE")' ai-status.json
jq '{codex_agent:(.agents[] | select(.name=="Codex")), owned_active:[.tasks[] | select(.owner=="Codex" and (.status|IN("todo","in_progress","review","review_approved","blocked"))) | {id,status,reviewer,next,last_update}], reviewer_queue:[.tasks[] | select(.reviewer=="Codex" and .status=="review") | {id,owner,status,next,last_update}], handoffs:[(.handoffs // [])[] | select(.to=="Codex" and .status!="done") | {task_id,from,to,status,message,created_at}]}' ai-status.json
sed -n '1,260p' support/sidecars/SVC-RENAME-003/SVC-RENAME-003-SIDECAR-ACCEPTANCE.md
sed -n '1,220p' support/sidecars/SVC-RENAME-003/SVC-RENAME-003-OWNER-EVIDENCE.md
sed -n '1,180p' support/sidecars/SVC-RENAME-003/review-svc-rename-003-claude.md
rg -n "SVC-RENAME-002|SVC-RENAME-003|Pair A|Pair B|Pair E|registry-core/decision-domain|control_plane" docs/architecture/services-namespace-migration-map-2026-05-10.md
find services/control_plane services/control-plane/internal services/registry-core/decision-domain -maxdepth 2 -type f -not -path '*/__pycache__/*' -print
test ! -e services/registry/decision_domain
rg -l "services\\.control_plane\\.internal|services/control-plane/internal|services/control_plane/internal|services/control_plane/internal_api|services/control_plane/internal_api_min" services tests scripts
rg -l "services/registry-core/decision-domain|registry-core/decision-domain|decision-domain|decision_domain" services scripts tests docs/architecture
```

2026-05-13 Codex refresh notes:

- `ai-status.json` still shows this task as `in_progress`, owner `Codex`,
  reviewer `Codex2`.
- Parent owner evidence and Claude review still support the Pair A summary above.
- Pair B remains out of scope for this sidecar: the current
  `services/registry-core/decision-domain/` tree is present, and the future
  `services/registry/decision_domain/` target path is not present.

Support-only validation after editing:

```bash
! sed -n '1,92p' support/sidecars/SVC-RENAME-003/SVC-RENAME-003-SIDECAR-ACCEPTANCE.md | rg -n "\\[To be determined\\]|\\[File Path/Name|\\[Brief description|Owned - Ready|Assigned To:|Gemini2|Designated Reviewer|TODO|TBD"
git diff --check -- support/sidecars/SVC-RENAME-003/SVC-RENAME-003-SIDECAR-ACCEPTANCE.md
git diff --name-only -- support/sidecars/SVC-RENAME-003/SVC-RENAME-003-SIDECAR-ACCEPTANCE.md
```

- Stale placeholder pattern check returned no packet-body matches above the
  `Verification Performed` section.
- `git diff --check` returned clean for this task artifact.
- Scoped diff name-only review returned only this task artifact.

## Owner Closeout

2026-05-13 Codex closeout notes:

- `Codex2` approved the packet as support-only material and confirmed the scoped
  diff is limited to this artifact.
- The approved sidecar remains limited to support acceptance material; it did
  not edit L1 canonical truth, core contract truth, runtime implementation,
  registry implementation, governance implementation, or service namespace
  files.
- Owner finalization created a task-scoped commit before moving the task to
  `done`.

Finalization verification:

```bash
git diff --check -- support/sidecars/SVC-RENAME-003/SVC-RENAME-003-SIDECAR-ACCEPTANCE.md
git diff --name-only -- support/sidecars/SVC-RENAME-003/SVC-RENAME-003-SIDECAR-ACCEPTANCE.md
git status --short
```

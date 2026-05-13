# SVC-RENAME-003 Sidecar Acceptance Packet

## Metadata

| Field | Value |
|---|---|
| Parent task | SVC-RENAME-003 |
| Sidecar task | SVC-RENAME-003-SIDECAR-ACCEPTANCE |
| Helper kind | acceptance_packet |
| Owner | Codex2 |
| Reviewer | Codex |
| Lifecycle target | handoff to review |
| Scope | support artifact only |
| Mutates canonical truth | no |
| Mutates runtime, registry, or governance implementation | no |
| Date | 2026-05-13 |

## Purpose

This packet supports parent task `SVC-RENAME-003` by giving the reviewer and
parent owner a concrete acceptance checklist, dependency map, and absorption
notes for Pair A of the services namespace normalization work:
`control_plane` snake-case compatibility plus `control-plane/internal`
implementation placement.

This sidecar does not define product semantics and does not change canonical
truth. It is a review aid for deciding whether the existing parent-task
evidence is complete enough to absorb or reference in the main SVC-RENAME-003
closeout record.

## Source Context Used

| Source | Use in this packet |
|---|---|
| `.orchestrator/task-briefs/svc_rename_003_sidecar_acceptance.md` | Task scope, owner, reviewer, artifact boundary |
| `ai-status.json` | Durable lifecycle state and prior Codex review request |
| `docs/architecture/services-namespace-migration-map-2026-05-10.md` | Pair A file moves and import rewrite rules |
| `support/sidecars/SVC-RENAME-003/SVC-RENAME-003-OWNER-EVIDENCE.md` | Parent owner verification and provenance note |
| `support/sidecars/SVC-RENAME-003/review-svc-rename-003-claude.md` | Parent task review approval and accepted traceability exception |

## Parent Scope Summary

SVC-RENAME-003 is the Pair A namespace normalization slice. The concrete parent
scope is:

| Area | Expected state for parent task |
|---|---|
| Implementation location | `services/control-plane/internal/internal_api.py` and `services/control-plane/internal/internal_api_min.py` hold the implementation files |
| Importable shim package | `services/control_plane/internal/` exists as the Python-importable compatibility namespace |
| Loader modules | `services/control_plane/internal/internal_api.py` and `services/control_plane/internal/internal_api_min.py` load the kebab-tree implementation with `importlib.util.spec_from_file_location` |
| Legacy wrappers | `services/control_plane/internal_api.py` and `services/control_plane/internal_api_min.py` remain as transition wrappers |
| Import-site rewrites | Runtime-manager and smoke import sites use `services.control_plane.internal.*` |
| Compose impact | No Pair A docker-compose change is expected |

Pairs B, E, and J from the broader namespace normalization plan are not in this
parent scope. In particular, `registry-core/decision-domain` to
`registry/decision_domain` belongs to the later Pair B task, not this packet.

## Sidecar Acceptance Checklist

| Check | Status | Evidence / review note |
|---|---|---|
| Support artifact only | ready | This task updates only `support/sidecars/SVC-RENAME-003/SVC-RENAME-003-SIDECAR-ACCEPTANCE.md` |
| Current ownership metadata is accurate | ready | Owner is Codex2 and reviewer is Codex, matching `ai-status.json` |
| Placeholder review drift removed | ready | No unresolved reviewer marker, generic file placeholders, or generic commit/done criteria remain |
| Concrete SVC-RENAME-003 parent checklist included | ready | See "Parent Scope Summary" and "Parent Acceptance Probe List" |
| Dependency map included | ready | See "Dependency Map" |
| Parent-owner absorption notes included | ready | See "Parent-Owner Absorption Notes" |
| Canonical truth remains untouched by this sidecar | ready | No L1/L2 canonical file edits are part of this sidecar deliverable |
| Runtime, registry, and governance code remain untouched by this sidecar | ready | This packet is documentation-only support material |
| Handoff target is explicit | ready | Reviewer for this sidecar is Codex |

## Parent Acceptance Probe List

The parent owner or reviewer can use this list to decide whether the packet is
sufficient for SVC-RENAME-003 absorption. These checks describe the parent
task's accepted evidence; this sidecar did not rerun runtime tests.

| Probe | Expected result | Existing evidence |
|---|---|---|
| File placement | Implementation files are present under `services/control-plane/internal/` | `SVC-RENAME-003-OWNER-EVIDENCE.md`; Claude review |
| Shim placement | Python-importable shim files are present under `services/control_plane/internal/` | `SVC-RENAME-003-OWNER-EVIDENCE.md`; Claude review |
| Legacy compatibility | Top-level legacy wrappers re-export through the shim namespace | Claude review acceptance table |
| Import rewrites | Five known import sites target `services.control_plane.internal.*` | Claude review import-site audit |
| Runtime-manager regression | `services/runtime-manager` pytest suite passes | Owner and reviewer both recorded 74 passed, 4 warnings |
| Internal API smoke | `tests/run_internal_api_smoke.py` returns `SMOKE OK` | Owner and reviewer evidence |
| Business logic posture | Parent implementation is a relocation/shim change, not a behavior rewrite | Claude review "no business logic changes" row |
| Commit provenance | Traceability exception is documented and accepted for parent task | `SVC-RENAME-003-OWNER-EVIDENCE.md`; Claude review |

## Dependency Map

| Dependency | Relationship | Absorption guidance |
|---|---|---|
| `docs/architecture/services-namespace-migration-map-2026-05-10.md` | Planning map that defines Pair A moves and import rewrites | Use as historical execution scope, not as new canonical truth from this sidecar |
| `support/sidecars/SVC-RENAME-003/SVC-RENAME-003-OWNER-EVIDENCE.md` | Parent owner evidence packet | Parent owner may cite it directly in closeout/final records |
| `support/sidecars/SVC-RENAME-003/review-svc-rename-003-claude.md` | Parent reviewer approval | Parent owner may cite it as the approved functional gate |
| `services/control-plane/internal/*` | Parent implementation location | Read-only dependency for this sidecar; do not edit here |
| `services/control_plane/internal/*` | Parent shim package | Read-only dependency for this sidecar; do not edit here |
| `services/control_plane/internal_api.py` and `internal_api_min.py` | Parent transition wrappers | Read-only dependency for this sidecar; do not edit here |
| `services/runtime-manager/*` and `tests/run_internal_api_smoke.py` | Parent import-site and verification surfaces | Read-only dependency for this sidecar; do not edit here |

Non-dependencies for this sidecar:

- L1 canonical architecture and policy files.
- Runtime, registry, governance, BFF, broker, and Qlib implementation code.
- Pair B `registry-core/decision-domain` migration.
- Pair E `control-plane/feedback` migration.
- Pair J `learning` to `research` cleanup.

## Parent-Owner Absorption Notes

1. Treat this packet as a support artifact, not as canonical architecture.
2. If the parent owner absorbs this sidecar, cite this file alongside the
   owner evidence and Claude review rather than copying its checklist into L1
   truth.
3. Preserve the accepted parent traceability exception as recorded in
   `SVC-RENAME-003-OWNER-EVIDENCE.md` and
   `review-svc-rename-003-claude.md`; this sidecar does not reopen that
   decision.
4. Keep Pair A isolated from Pair B, Pair E, and Pair J when summarizing the
   parent delivery.
5. If more namespace cleanup is needed later, create a separate follow-up task
   rather than expanding this support packet.

## Sidecar Verification

Sidecar validation is limited to support-packet review because this task does
not modify implementation code. The exact checks used for this pass are:

```bash
sed -n '1,240p' .orchestrator/task-briefs/svc_rename_003_sidecar_acceptance.md
sed -n '1,240p' support/sidecars/SVC-RENAME-003/SVC-RENAME-003-OWNER-EVIDENCE.md
sed -n '1,240p' support/sidecars/SVC-RENAME-003/review-svc-rename-003-claude.md
sed -n '360,455p' docs/architecture/services-namespace-migration-map-2026-05-10.md
rg -n 'Gemini''2|To be ''determined|\\[File ''Path|branch ''commit/done' support/sidecars/SVC-RENAME-003/SVC-RENAME-003-SIDECAR-ACCEPTANCE.md
```

The final `rg` check is expected to return no matches.

## Handoff To Reviewer

Reviewer: Codex

Requested review outcome:

- Confirm this packet satisfies the requested concrete SVC-RENAME-003
  acceptance checklist.
- Confirm the dependency map is explicit enough for parent-owner absorption.
- Confirm no support-packet language implies this sidecar changed canonical
  truth, runtime, registry, or governance implementation.

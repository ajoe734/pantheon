# Review: SVC-DATA-OWNERSHIP-MIGRATION-MAP-SIDECAR-ACCEPTANCE

**Reviewer:** Claude
**Date:** 2026-04-29
**Task:** SVC-DATA-OWNERSHIP-MIGRATION-MAP-SIDECAR-ACCEPTANCE
**Artifact:** support/sidecars/SVC-DATA-OWNERSHIP-MIGRATION-MAP/SVC-DATA-OWNERSHIP-MIGRATION-MAP-SIDECAR-ACCEPTANCE.md
**Disposition:** approved

---

## Review Summary

The acceptance packet prepared by Claude2 is thorough, well-structured, and stays strictly within sidecar scope. No canonical truth was modified.

### Scope Compliance

- No edits to `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` or any L1 policy file — confirmed.
- No migration DDL, schema bootstrap, or service implementation changes — confirmed.
- No lifecycle finalization attempted for the parent task `SVC-DATA-OWNERSHIP-MIGRATION-MAP` — confirmed.

### Content Quality

**§4 Store Inventory (AC-1):** Code-backed inventory of 15 service store groups is complete. Covers JSONL and JSON flat stores across all relevant services. Env vars and compose default paths are cited where available.

**§5 Ownership Conflicts and Boundary Violations:** Two write-boundary violations correctly flagged:
- incidents-svc + postmortems-svc shared write to `incidents.json` — clear L1 policy reference (§3.1 single-owner rule).
- search-svc volume read from source-ingest path — clear L1 policy reference (§3.3 non-owner API call rule).

Filename collision (`approval_decisions.json`) correctly flagged as a schema naming ambiguity, not a write-owner violation.

**§6 L1 Cross-Reference:** Mapping to `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md §4` is accurate. Gaps (7 stores without named L1 schemas) are explicitly marked rather than silently omitted.

**§7 Acceptance Checklist:** Gap assessment is honest — AC-2 through AC-4, AC-6, AC-7 all correctly marked open. Sidecar does not over-claim completion.

**§8 Dependency Map:** Three blocked downstream pilot tasks accurately identified. No artificial blockers invented in task state.

**§9 Pilot Scope Suggestion:** consultation-svc recommendation is well-reasoned (sole write owner, no cross-service volume deps, pilot task already assigned). Appropriately framed as a suggestion, not a decision.

**§10 Reviewer Focus Areas:** Useful checklist for the parent owner. No scope overreach.

### Findings for Parent Owner (Claude)

The following decisions remain open and must be resolved in the parent map (`SVC-DATA-OWNERSHIP-MIGRATION-MAP`) before downstream pilot tasks can proceed:

1. **Incident/postmortem write owner:** Choose incidents-svc as sole writer with postmortems-svc calling API, OR declare both services as a co-located owner unit under one schema.
2. **Search read path post-migration:** Choose read role on source-ingest Postgres table OR source-ingest read API call from search-svc.
3. **Schema names for 7 stores without L1 coverage:** search, lineage, bff, research, training-session, research-worker-gateway, memory schemas must be proposed in the migration map.
4. **First pilot scope:** Confirm consultation-svc or name an alternative, with explicit rollback path.
5. **Priority tier assignment:** AC-2 requires each store to have a migration priority tier — this was not assigned in the sidecar (correctly out of scope here).

---

## Approval Decision

Approved. The sidecar fulfills its helper scope: code-backed inventory, boundary violation flags, L1 cross-reference, and dependency map are all present and accurate. The packet provides the parent owner with the full input needed to produce the migration map.

Owner (Claude2) may finalize this task to `done` after this approval.

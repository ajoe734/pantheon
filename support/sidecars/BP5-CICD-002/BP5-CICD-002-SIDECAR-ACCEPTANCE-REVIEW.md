# BP5-CICD-002-SIDECAR-ACCEPTANCE — Review Note

**Reviewer:** Codex  
**Date:** 2026-04-16  
**Status:** changes requested

---

## Blocking Findings

1. **AC-3 still cites the wrong `runtime-manager` Dockerfile path.**  
   In [support/sidecars/BP5-CICD-002/BP5-CICD-002-SIDECAR-ACCEPTANCE.md](/home/edna/code/pantheon/support/sidecars/BP5-CICD-002/BP5-CICD-002-SIDECAR-ACCEPTANCE.md:77), the packet says `runtime-manager` is backed by `services/execution/runtime-manager/`. The actual build evidence points to `services/runtime-manager/Dockerfile` in [cloudbuild.yaml](/home/edna/code/pantheon/cloudbuild.yaml:45) and [cloudbuild.yaml](/home/edna/code/pantheon/cloudbuild.yaml:101), and that file exists at [services/runtime-manager/Dockerfile](/home/edna/code/pantheon/services/runtime-manager/Dockerfile:1). The execution code lives under `services/execution/runtime-manager/**`, but that is not the Dockerfile evidence the packet claims. Please correct the evidence path or rewrite the note so the acceptance packet does not assert a false Dockerfile location.

2. **AC-5 contradicts itself on manual-only service IDs.**  
   The subsection headed [support/sidecars/BP5-CICD-002/BP5-CICD-002-SIDECAR-ACCEPTANCE.md](/home/edna/code/pantheon/support/sidecars/BP5-CICD-002/BP5-CICD-002-SIDECAR-ACCEPTANCE.md:128) says "Services correctly absent from `cloudbuild.yaml`", but its own table includes `incidents` and `postmortems`, which are actually present in `cloudbuild.yaml` as manual-only targets at [cloudbuild.yaml](/home/edna/code/pantheon/cloudbuild.yaml:60) and [cloudbuild.yaml](/home/edna/code/pantheon/cloudbuild.yaml:62). The closing sentence at [support/sidecars/BP5-CICD-002/BP5-CICD-002-SIDECAR-ACCEPTANCE.md](/home/edna/code/pantheon/support/sidecars/BP5-CICD-002/BP5-CICD-002-SIDECAR-ACCEPTANCE.md:136) then says "All `cloudbuild.yaml` service IDs are present in the Stage 0 matrix (no phantom IDs)," which is false for those two manual-only IDs. Please rewrite AC-5 to distinguish:
   - auto-detected IDs that must align with Stage 0 matrix routing, and
   - manual-only IDs that intentionally exist only in `cloudbuild.yaml`.

## Reviewer Scope Note

This review only covers the support packet accuracy for the sidecar slice. No canonical files, runtime code, registry truth, or parent-task implementation were modified during review.

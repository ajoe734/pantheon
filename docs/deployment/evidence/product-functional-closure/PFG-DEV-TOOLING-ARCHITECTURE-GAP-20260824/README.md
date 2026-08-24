# PFG-DEV-TOOLING-ARCHITECTURE-GAP-20260824 Evidence

## Overview

This task records and designs the development-tooling architecture corrections for the Pantheon supervisor, V2 TaskStore, and auto-worker fleet, addressing live friction observed during the 2026-08-20 to 2026-08-24 functional closure sprint.

## Deliverables

1. **Architecture Gap Record and Design Specification**:
   - `docs/operations/development-tooling-architecture-gaps-2026-08-24.md`
2. **Review Evidence Manifest**:
   - `docs/deployment/evidence/product-functional-closure/PFG-DEV-TOOLING-ARCHITECTURE-GAP-20260824/README.md`
   - `docs/deployment/evidence/product-functional-closure/PFG-DEV-TOOLING-ARCHITECTURE-GAP-20260824/evidence.json`

## Core Gaps & Proposed Designs Covered

1. **Immutable Task Correction vs. Append-Only Amendments**:
   - Design of `TaskAmendedEvent` in the V2 TaskStore append-only journal, allowing safe amendment of non-lifecycle task fields without mutating historical genesis records or corrupting journal head digests.
2. **Dependency-Aware Reopen & Root-Evidence Handoff**:
   - Automatic DAG dependency state invalidation upon task reopening, coupled with structured `RootEvidenceHandoff` verification contracts.
3. **Mandatory Artifact Ownership & Overlap Admission**:
   - Explicit `artifacts_manifest` (`owned_write_paths`, `referenced_read_paths`) and path-based mutual exclusion admission gating in `dispatch_admission.py`.
4. **First-Class Cross-Repository Sidecars & Subphase Dispatch**:
   - Native `target_repo`, `task_nature`, `parent_task_id`, and `subphase` schema support in TaskStore, integrated with `multi_repo_registry.py`.
5. **Exact-Head Review Rejection Recovery & `waiting_for` Cleanup**:
   - Pure-lifecycle `supersede` exit for closed/merged PR heads, exact-head SHA reject tagging, and deterministic `waiting_for` purging on all non-blocked state transitions.

## Verification

- Markdown syntax and git diff cleanliness (`git diff --check`).
- Document authority and boundary compliance (`docs/02-architecture/development-tooling-product-boundary.md`).
- All product runtimes, BFF endpoints, Source Ingestion services, and capital pathways remain untouched.

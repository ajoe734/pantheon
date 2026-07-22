# OCLAW-PMEM-003 - Canonical Memory Bridge To OpenClaw Workspace

Owner: Gemini2
Reviewer: Codex
Parent: `OCLAW-PMEM-000`
Depends on: `OCLAW-PMEM-001`

## Problem

Pantheon already has canonical `PersonaMemory` and `InstitutionalMemoryEntry`,
but OpenClaw workspace files are not derived from that Memory Plane. The system
therefore risks two separate memory truths: canonical memory in
`services/memory`, and workspace-local notes under OpenClaw.

## Scope

- Build a memory bridge that retrieves canonical memory through
  `GET /api/memory/retrieve` and materializes bounded context into the
  OpenClaw workspace.
- Materialized files must include source memory IDs, relevance scores, written
  timestamps, retrieval query/scope, and generation timestamp.
- Define canonical filenames, for example `MEMORY.md` plus
  `memory/context.json`, without treating those files as source of truth.
- Create a governed OpenClaw writeback candidate flow. OpenClaw may propose
  memory, but canonical writes must go through authorized memory service
  writeback endpoints.
- Preserve private memory isolation and committee-scope filtering.

## Acceptance

- Tests prove Memory Plane entries are materialized into workspace context with
  traceable source IDs.
- Tests prove private persona memory is not materialized for a different
  persona.
- Tests prove OpenClaw-generated memory candidates do not directly mutate
  canonical memory without authorized writeback.
- Dev evidence shows a persona agent workspace includes generated memory context
  from canonical `PersonaMemory`.

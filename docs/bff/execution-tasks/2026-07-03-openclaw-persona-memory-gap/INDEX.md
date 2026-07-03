# OpenClaw Persona / Memory Gap Execution Packet - 2026-07-03

Status: ready for fleet dispatch

Parent task:

- `OCLAW-PMEM-000` - OpenClaw persona model routing and memory architecture gap

Source gap spec:

- `docs/04/pantheon_openclaw_persona_memory_gap_2026-07-03/OPENCLAW_PERSONA_MEMORY_GAP_SPEC.md`

## Dispatch Command

```sh
python3 scripts/dispatch_openclaw_persona_memory_gap_2026-07-03.py
python3 scripts/ai_status.py sync
```

The dispatch script is idempotent. It preserves progress fields for already
started tasks and appends assignment log events only for newly created tasks.

## Execution Order

| Wave | Task | Owner | Reviewer | Summary |
|---|---|---|---|---|
| 0 | `OCLAW-PMEM-001` | Claude | Codex | Define canonical PersonaRuntimeProfile and model-routing policy. |
| 1 | `OCLAW-PMEM-002` | Codex2 | Claude | Reconcile OpenClaw agent identity, model, workspace, and SOUL for new and existing personas. |
| 1 | `OCLAW-PMEM-003` | Gemini2 | Codex | Build canonical Memory Plane to OpenClaw workspace materialization and governed writeback candidate flow. |
| 2 | `OCLAW-PMEM-004` | Claude2 | Codex | Wire BFF and Management UI surfaces for runtime profile, provider pool health, memory, quota, and reauth state. |
| 3 | `OCLAW-PMEM-005` | Codex | Claude | Add end-to-end dev gates and close the parent gap with hosted evidence. |

## Dependencies

```text
OCLAW-PMEM-001: none
OCLAW-PMEM-002: OCLAW-PMEM-001
OCLAW-PMEM-003: OCLAW-PMEM-001
OCLAW-PMEM-004: OCLAW-PMEM-002, OCLAW-PMEM-003
OCLAW-PMEM-005: OCLAW-PMEM-002, OCLAW-PMEM-003, OCLAW-PMEM-004
OCLAW-PMEM-000: OCLAW-PMEM-005
```

## Global Acceptance

Every `OCLAW-PMEM-*` child task must record:

1. branch and PR target;
2. local validation commands and output summary;
3. reviewer approval;
4. merge commit SHA;
5. dev BFF/OpenClaw evidence when runtime behavior changes;
6. explicit source-of-truth boundary notes for Persona Registry, Memory Plane,
   OpenClaw workspace, and provider pool;
7. residual risks with owner and expiry.

The parent gap is not complete until `OCLAW-PMEM-005` archives final proof and
the parent task has reviewer-approved closeout evidence.

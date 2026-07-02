# Persona Paper-First Live Promotion Execution Tasks

Generated: 2026-07-02

Status: ready for supervisor dispatch after human review

Source spec:

- `docs/04/pantheon_persona_paper_live_gap_2026-07-02/GAP_AND_EXECUTION_PLAN.md`

Task packet directory:

- `docs/bff/execution-tasks/2026-07-02-persona-paper-live-gap/`

Dispatch script:

- `scripts/dispatch_persona_paper_live_gap_2026-07-02.py`

Do not dispatch these tasks until the gap spec is accepted. The script creates
active `ai-status.json` assignments through the existing supervisor path.

## Task Matrix

| Task | Owner | Reviewer | Depends On | Purpose |
|---|---|---|---|---|
| `PPLG-001` | Codex | Claude | none | Lock canonical states, schemas, API contracts, and old-spec supersession. |
| `PPLG-002` | Claude | Codex | `PPLG-001` | Implement idempotent create-to-paper launch workflow. |
| `PPLG-003` | Codex2 | Claude2 | `PPLG-001` | Update Fleet/readiness read model and remove heavy duplicate payloads. |
| `PPLG-004` | Claude2 | Codex | `PPLG-001` | Implement paper eligibility, promotion score, and cohort ranking. |
| `PPLG-005` | Claude | Codex2 | `PPLG-004` | Implement human review workflows for canary/live/quarterly decisions. |
| `PPLG-006` | Codex | Claude2 | `PPLG-001` | Implement automatic guardrails and incident review evidence. |
| `PPLG-007` | Codex2 | Claude | `PPLG-002,PPLG-003,PPLG-005` | Update frontend create and Fleet UX to paper-first semantics. |
| `PPLG-008` | Gemini2 | Codex | `PPLG-002,PPLG-003,PPLG-004,PPLG-005,PPLG-006,PPLG-007` | Produce E2E release gate and closeout evidence. |

## Dispatch Command

After this packet is accepted and merged:

```bash
python3 scripts/dispatch_persona_paper_live_gap_2026-07-02.py
```

Expected behavior:

- Assigns eight active tasks via `scripts/ai_status.py assign`.
- Adds task metadata pointing back to the source spec and task packet.
- Does not grant broker, runtime, or capital authority by itself.

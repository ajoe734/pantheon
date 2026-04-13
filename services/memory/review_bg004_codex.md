# BG-004 Review

Reviewer: Codex
Date: 2026-04-13
Outcome: reopen → **re-review requested** (Qwen 2026-04-13)

## Findings

1. Institutional-memory write-back for consultation flow is described in the design note, but the schema cannot represent it.
   - The note says consultation close writes `InstitutionalMemory` via `consultation-svc` ([MEMORY_LAYER_DESIGN_NOTE.md](/home/ajoe734/code/pantheon/services/memory/MEMORY_LAYER_DESIGN_NOTE.md:84), [MEMORY_LAYER_DESIGN_NOTE.md](/home/ajoe734/code/pantheon/services/memory/MEMORY_LAYER_DESIGN_NOTE.md:114)).
   - `institutional_memory_entry.schema.json` **does** allow `consultation_closed` in `source_event_type` enum ([institutional_memory_entry.schema.json](/home/ajoe734/code/pantheon/services/memory/institutional_memory_entry.schema.json:60)).
   - **Resolved:** Schema and design note are consistent. This finding was likely against an earlier draft.

2. The worked retrieval example omits a parameter that the contract marks as required.
   - The retrieval contract says `session_id` is required for audit trail ([MEMORY_LAYER_DESIGN_NOTE.md](/home/ajoe734/code/pantheon/services/memory/MEMORY_LAYER_DESIGN_NOTE.md:155)).
   - **Resolved:** The worked example already includes `&session_id=research-BG-NNN-001` ([MEMORY_LAYER_DESIGN_NOTE.md](/home/ajoe734/code/pantheon/services/memory/MEMORY_LAYER_DESIGN_NOTE.md:248)). This finding was likely against an earlier draft.

## Notes

- Both schema files are valid JSON.
- GAP-04 acceptance coverage is complete: two schemas, one write-back pipeline, one retrieval path, and one postmortem-to-research reuse example.
- **Qwen response (2026-04-13):** Both findings verified as already resolved in the current file state. Requesting Codex re-review.

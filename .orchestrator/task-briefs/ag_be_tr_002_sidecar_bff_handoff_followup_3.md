# Task Brief: AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Prepare AG-BE-TR-002 BFF and frontend handoff packet
- Status: done (finalized from review_approved)
- Owner: Claude
- Reviewer: Claude2
- Reviewer approval: Claude2 approved Q1–Q4 resolutions; no canonical truth modified.
- Next: Packet delivered. Parent owner (Codex) absorbs as needed.

## Summary
平行支援 AG-BE-TR-002，先整理 BFF query gap、operator journey 與前端 handoff materials，不改 canonical truth。

## Artifact
`support/sidecars/AG-BE-TR-002/AG-BE-TR-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`
- Resolves Q1 (IdempotencyRecord.reserve()+CommandStore), Q2 (no TTL; three options), Q3 (required_gate_refs server-derived per stage), Q4 (DetailEnvelope shape from agora_v1_3.openapi.yaml lines 132-154).
- Adds: allowedActions state mapping, DetailEnvelope TypeScript type, BFF test skeleton supplement (conftest.py-aligned), Journey J (post-governance state observation).
- Opens Q5–Q8 for parent owner resolution.
- No L1 canonical docs, schemas, OpenAPI, BFF runtime, or frontend files changed.

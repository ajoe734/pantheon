# Task Brief: AG-CAND-TRUTH-001-BE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Complete Agora candidate provenance projection
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Review changes required for ccd63689e/44025075a. Passing evidence: focused suite 57 passed, 2 skipped only without AGORA_RESEARCH_TEST_POSTGRES_DSN; v1.10 hashes and replacement BFF host 35.201.204.12 are correct. Blockers: (1) v11 CandidateFieldState uses AvailableField.value={} and does not bind CandidateRationale/Concerns/NextEvent/Evidence/Details value schemas, so value:null validates; CandidateEvidenceItem also validates raw summary with summary_redacted=true. Add field-specific state schemas/conditionals and negative tests. (2) list redaction is bypassed: the exact component explanation hidden at evidence.summary still appears in rationale/concerns ComponentDigest and full current_score.components; return a list-safe score/digest and add tests covering every list path plus viewer detail. (3) generated fallback evidence:// refs from _component_evidence_refs are surfaced as available durable evidence although no source artifact is verified; mark unavailable unless a persisted governed evidence ref exists and test the no-ref path. (4) details provenance uses the pool creation snapshot even after lifecycle mutations; persist/use the member mutation timestamp so per-field as_of remains truthful. Re-run focused suite and refresh v1.10 schema/manifest/OpenAPI hashes.

## Summary
讓 candidate DTO 的理由、疑慮、事件、證據與細節都屬於同一真實 candidate 並帶 provenance/as-of；缺欄位明確 unavailable。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

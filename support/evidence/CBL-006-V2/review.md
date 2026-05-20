# CBL-006-V2 Review Notes

**Reviewer:** Codex2
**Owner:** Codex
**Review date:** 2026-05-20
**Task:** Capital binding evidence collector

## Artifacts Reviewed

- `services/capital/binding_live/evidence_collector.py`
- `tests/capital/test_binding_evidence_collector.py`
- `services/capital/binding_live/readiness_model.py`
- `tests/capital/test_binding_live_readiness.py`
- `tests/capital/test_conflict_resolution_log.py`

## Verification Commands Run

```bash
python3 -m pytest tests/capital/test_binding_evidence_collector.py tests/capital/test_binding_live_readiness.py tests/capital/test_conflict_resolution_log.py
# 15 passed in 1.47s

python3 -m py_compile services/capital/binding_live/evidence_collector.py services/capital/binding_live/readiness_model.py services/capital/binding_live/conflict_resolution_log.py tests/capital/test_binding_evidence_collector.py
# compile OK
```

## Review Findings

No blocking findings.

The collector resolves every `CapitalBindingLiveReadiness.required_evidence`
field in schema order and returns a serializable collection result with
readiness id, binding id, evidence root, collected payloads, content type,
hashes, and blocking reasons.

Fail-closed behavior is present for missing files, malformed JSON, empty
payloads, unreadable refs, unsupported URI schemes, non-file refs, and paths
that resolve outside the configured evidence root. The path resolution uses
`Path.resolve()` and rejects escaped roots before reading bytes.

The implementation is read-only. It does not mutate readiness packets, runtime
bindings, deployment plans, broker state, order routes, capital bindings, or
L1 canonical documents.

## Decision

**APPROVED** - Implementation satisfies the task acceptance criteria. The
collector is scoped to local required-evidence resolution, covers the happy
path plus fail-closed cases in tests, and keeps live activation side effects
closed.

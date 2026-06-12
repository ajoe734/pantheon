# Review: DATASTRAT-IDS-001
Reviewer: Claude2
Date: 2026-06-12
Commit: a366328e (DATASTRAT-IDS-001: add interaction source store)

## Verdict: APPROVED

All acceptance criteria verified. 19 focused tests pass.

## Acceptance Criteria Checklist

- [x] Contract `docs/contracts/interaction_source_record.schema.json` delivered
  with all required fields: interaction_id, source_surface, actor_type,
  persona_refs, session_id, raw_ref, summary, evidence_refs, visibility,
  redaction_status, created_at, updated_at, metadata.
- [x] source_surface enum covers all 12 required surfaces: trainer, ask_personas,
  committee, decision_journal, notebook, postmortem, agora, red_team,
  management_ai, operator_console, research_chat, other.
- [x] visibility required with enum {private, persona, desk, shared}.
- [x] redaction_status required with enum {pending, passed, failed}.
- [x] raw_ref is a pointer only — schema-level propertyNames guard forbids
  raw_text/raw_content/raw_prompt/prompt/transcript/messages/message/body/content
  at both root and metadata levels; model-level _require_ref enforces no newlines
  and max 1024 chars.
- [x] JSONL dev store using registry-split pattern (delegates to
  JsonlRegistryStore with interaction_id as key).
- [x] record can be created from every surface (12 parametrized test cases).
- [x] raw_ref must appear in evidence_refs — model enforces cross-validation
  in __post_init__.
- [x] InteractionSourceRecordStore: add (no-duplicate), save (upsert), get,
  list_all, list_by_surface, list_by_visibility, list_by_redaction_status, count.
- [x] All new types exported from services/source_ingestion/__init__.py.

## Code Quality Notes

- interaction_source_store.py: frozen dataclass with strict __post_init__; enum
  coercion centralizes validation; _reject_inline_raw_keys recursively guards
  Mapping and list structures at both model and from_dict boundaries.
- Schema and model are consistent: propertyNames forbidden-key list matches
  _FORBIDDEN_INLINE_RAW_KEYS constant; evidence_refs kind enum matches
  _EVIDENCE_REF_KINDS constant.
- additionalProperties: false in schema prevents undeclared fields from
  passing JSON Schema validation.
- Store path configurable via SOURCE_INGEST_DATA_DIR and
  INTERACTION_SOURCE_STORE_PATH env vars — consistent with existing store patterns.

## Minor Observations (non-blocking)

1. evidence_refs schema description says "At least one item should match raw_ref"
   (should vs must). The model enforces this as a hard invariant; the description
   is slightly softer than the implementation. No functional issue.
2. _reject_inline_raw_keys is not called directly in __post_init__ for the top-level
   record dict, but metadata is explicitly guarded at line 226-228 and evidence_refs
   at _coerce_evidence_refs; the from_dict path calls it on the full input dict.
   Coverage is complete.

## Verification

```
python3 -m pytest services/source_ingestion/tests/test_interaction_source_store.py -v
19 passed in 2.32s
```

Reviewed independently of owner; all tests observed to pass without modification.
This is the IDS-001 foundation layer; subsequent IDS-002/003/007 safety guards
depend on this contract and must land before any ingestion bridge (IDS-004).

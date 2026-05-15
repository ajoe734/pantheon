# BFF-FINAL-008 - Agora Journal Merge Patch

Priority: P1

Depends on: BFF-FINAL-001, BFF-FINAL-002

Area: Agora BFF write facade

## Goal

Implement final JSON Merge Patch contract for Agora decision journal entries.

## Contract Inputs

```http
PATCH /bff/agora/journal/{id}
Content-Type: application/merge-patch+json
Idempotency-Key: idem_...
```

Response:

```text
CommandResponse<DecisionJournalEntryDTO>
```

## Implementation Scope

Likely files:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/models.py`
- `services/control-plane/bff/read_store.py`
- new Agora journal store/adapter if not present
- new journal endpoint tests

## Steps

1. Add `DecisionJournalEntryDTO` if not already present.
2. Add `JournalEntryMergePatch` model with optional:
   - `title`
   - `body`
   - `tags`
   - `linkedStrategyIds`
   - `linkedPersonaIds`
   - `visibility`
3. Require `Content-Type: application/merge-patch+json`.
4. Require idempotency header.
5. Validate:
   - title 1-160 chars when present
   - body max 20000 chars when present
   - tags lowercase dot.case or slug
   - visibility capability
6. Persist or dispatch the update through the correct Agora/journal authority. If no authority exists yet, implement BFF-local dev store only as an explicitly degraded/dev path.
7. Audit before/after diff and correlation id.

## Acceptance Criteria

- Non-merge-patch content type is rejected.
- Body-level `idempotencyKey` is rejected.
- Successful patch returns required `data`.
- Audit evidence includes before/after diff.

## Verification

```bash
python -m pytest services/control-plane/bff -k "journal or agora" -q
```

## Implementation Notes

- Added `PATCH /bff/agora/journal/{id}` with required `Content-Type: application/merge-patch+json` and final `Idempotency-Key` admission.
- Added `DecisionJournalEntryDTO` and `JournalEntryMergePatch` final contract models.
- Rejected body-level `idempotencyKey` before patch application.
- Applied validated title/body/tags/link/visibility patches through an explicit `bff_local_dev_store` degraded path while preserving `canonicalWriteAuthority=agora_journal_service`.
- Persisted audit evidence with correlation id, idempotency key, and before/after field diff.
- Added idempotency replay/conflict handling for journal patch requests.

## Verification Run

```bash
python3 -m pytest services/control-plane/bff/test_agora_journal_merge_patch.py -q
python3 -m pytest services/control-plane/bff -k "journal or agora" -q
python3 -m pytest services/control-plane/bff/test_final_contract_primitives.py -q
python3 -m pytest services/control-plane/bff/test_governance_command_submission.py -k "bff_v1_commands" -q
```

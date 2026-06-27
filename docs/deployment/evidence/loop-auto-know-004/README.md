# LOOP-AUTO-KNOW-004 Evidence

Task: `LOOP-AUTO-KNOW-004` — Extract Agora interaction evidence into datasets

## Delivered Surface

- `services/control-plane/bff/agora/dataset_extraction/__init__.py`
  - Module marker; declares the `agora.dataset.v1` extraction surface.
- `services/control-plane/bff/agora/dataset_extraction/models.py`
  - `InteractionKind` enum: ask, feedback, journal, note, insight, training_example
  - `DatasetKind` enum: observe, learn
  - `route_to_dataset()` — routes interaction kind to the correct governed bucket
  - `AgoraInteractionEvidenceRequest` — inbound evidence payload (extra fields forbidden)
  - `DatasetRecord` — persisted record carrying immutable governance proof literals
- `services/control-plane/bff/agora/dataset_extraction/extractor.py`
  - `AgoraDatasetStore` — thread-safe in-memory store backed by `threading.RLock`
  - `extract_evidence()` — idempotent extraction; duplicate evidence_id returns cached record
- `services/control-plane/bff/agora/dataset_extraction/router.py`
  - `create_dataset_extraction_router()` factory; dependency-injected (identity, auth, store)
  - `POST /bff/agora/interaction-evidence` (201; requires `Idempotency-Key` header)
  - `GET /bff/agora/interaction-evidence/{evidence_id}` (200 / 404)
  - `GET /bff/agora/datasets/{dataset_kind}` (200; paged; observe or learn)
- `services/control-plane/bff/agora/router.py`
  - Wires `create_dataset_extraction_router` into the Agora top-level router factory.

## Acceptance Mapping

| Acceptance criterion | Evidence |
|---|---|
| Interaction evidence is routed into Observe or Learn datasets | `route_to_dataset()` maps ask/journal/note/insight → observe; feedback/training_example → learn; verified in `TestRouteToDataset` (7 cases) and route-layer tests |
| Dataset extraction is idempotent | `AgoraDatasetStore.add_or_get()` returns existing record with `idempotent=True` on duplicate `evidence_id`; `test_duplicate_evidence_id_is_idempotent`, `test_duplicate_does_not_overwrite_extracted_at`, `test_duplicate_evidence_id_returns_201_with_idempotent_true` |
| Evidence never promotes artifact or mutates running runtime directly | All `DatasetRecord` instances carry immutable literals: `governance_boundary="observe_or_learn_only"`, `no_promote_proof="agora_observe_learn_only"`, `no_runtime_mutation_proof="agora_evidence_extract_only"`; verified in `test_record_carries_governance_proof` and `test_submit_response_carries_governance_proof` |

## Verification

```bash
python3 -m pytest services/control-plane/bff/agora/dataset_extraction/ -q
```

Result: `49 passed in 8.94s`

```bash
python3 -m pytest services/control-plane/bff/agora/ -q
```

Result: `80 passed in 12.58s` (no regressions in existing Agora suite)

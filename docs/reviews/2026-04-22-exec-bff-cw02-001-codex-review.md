# APP-003-CW02-IMPL-001 Review

Reviewer: `Codex`
Date: `2026-04-22`
Disposition: `changes_requested`

## Findings

1. `from_sequence_no` can hide a broken transcript and incorrectly downgrade the surface back to `ok`.

- In [services/control-plane/bff/read_store.py](/home/edna/code/pantheon/services/control-plane/bff/read_store.py:8684), `get_consult_transcript()` sorts the full event list, then applies `from_sequence_no`, and only after that computes `has_gap` from the filtered slice at [services/control-plane/bff/read_store.py](/home/edna/code/pantheon/services/control-plane/bff/read_store.py:8684).
- That means an already-corrupted transcript can look healthy as soon as the client asks for a later slice. Reproduction: remove `sequence_no == 2` from the seeded `cs-20260419-081` transcript and request `GET /api/v1/consultations/cs-20260419-081/transcript?from_sequence_no=3`; the response is `200` with `events=[3]` and `meta.surfaces.transcript.state="ok"`.
- The CW-02 contract explicitly says sequence gaps are not allowed under `partial`, and that any inconsistent event stream must surface as `degraded`. The integrity check therefore has to run against the full canonical transcript, not only the filtered page window.

2. `meta.staleness.served_from` is hard-coded to `local_snapshot` even when the transcript is served from the service-backed store.

- The response payload always emits `"served_from": "local_snapshot"` whenever a transcript record exists at [services/control-plane/bff/read_store.py](/home/edna/code/pantheon/services/control-plane/bff/read_store.py:8721), but the store layer already distinguishes `service_store` vs `local_snapshot` through `dataset_source()` at [services/control-plane/bff/read_store.py](/home/edna/code/pantheon/services/control-plane/bff/read_store.py:4527).
- Reproduction: point `PANTHEON_BFF_CONSULT_TRANSCRIPT_STORE` and `PANTHEON_BFF_CONSULTATION_SESSION_STORE` at explicit JSON files built from the seeded data. `ReadSurfaceStore.dataset_source("consult_transcripts")` then returns `service_store`, but `get_consult_transcript("cs-20260419-081")["meta"]["staleness"]["served_from"]` still returns `local_snapshot`.
- This is now a provenance bug in the published contract surface. The frontend will be told it is looking at fallback data even when the backend-owned transcript store is healthy.

## Verification

- `pytest -q services/control-plane/bff/test_cw01_consult_request_contract.py services/control-plane/bff/test_cw02_debate_transcript_contract.py services/control-plane/bff/test_cw03_committee_board_contract.py` passed (`23 passed`).
- Reproduced the filtered-gap bug with the seeded CW-02 test harness and confirmed `from_sequence_no=3` currently returns `meta.surfaces.transcript.state="ok"` after removing sequence `2`.
- Reproduced the provenance bug with explicit service-backed transcript/session JSON stores and confirmed `dataset_source("consult_transcripts") == "service_store"` while the response still reports `meta.staleness.served_from="local_snapshot"`.

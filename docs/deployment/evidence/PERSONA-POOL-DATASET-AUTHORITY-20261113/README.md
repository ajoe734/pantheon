# Persona pool DatasetVersion authority

Task: `PERSONA-POOL-DATASET-AUTHORITY-20261113`
Owner: Codex; assigned independent reviewer: Claude.

The persona lifespan now constructs its pool loader from
`services/persona/pool_dataset_authority.py`. When no explicit canonical dataset
path is configured, it uses training-session's unmodified
`materialize_source_dataset_version`, then `load_evaluation_authority` with the
materialized file's parent as `authority_root`. The configured threshold policy
also selects the required instruments during materialization. No second source
implementation, admission policy, privileged route, or execution writer is added.

Compose defaults use the same source API, Taiwan official connector
`tw-twse-tpex-official-market`, and dataset `tw_price_daily` as training.
The persona container reads `source-ingest-data:/data/source-ingest:ro` and a
dedicated `source_ingest_reader` principal file from its existing read-only
principal mount. The existing dev principal issuer delivers this file to the
persona consumer. Materialized authority files persist under
`/data/bff/persona-pool-dataset-authority` on the existing writable data volume.
Both direct dataset path variables remain unset by default; explicit test/dev
paths delegate to the existing path loader.

`test_pool_dataset_authority.py` exercises the real source materializer,
evaluation authority, candidate strategy replay, pool store, and OODA packet
projection. Owner HTTP responses alone are replaced with fixtures, with a fixed
historical service clock for deterministic freshness validation. The candidate
book's persisted `source_ref` is exactly `governed-replay:<DatasetVersion ID>`.
The unchanged risk policy refuses the recorded candidate for
`candidate_virtual_book_drawdown_exceeds_removal_policy`; this evidence claims a
real replay and paper admission attempt, not successful admission. Missing
configuration, missing token, rejected source health, stale controller readback,
missing source storage, and missing policy each leave the pool unchanged and
record `not_attempted` with a reason in OODA. A subsequent rejected source read
does not fall back to a previously successful materialization.

Fixture provenance and limits:

- `fixtures/normalized.jsonl` contains SourceRecord wrappers around the exact
  164 recorded OHLCV rows (2330 and 2317, September–December 2024) already in
  `services/research/tests/fixtures/pool_admission_tw_recorded.json`. The test
  compares every normalized value with that recording. No price is synthesized;
  replay returns are computed by production code. Raw and feature storage files are constructed in the temporary
  test volume from those same rows.
- `fixtures/source-owner-http.json` is an explicitly constructed source owner
  contract fixture, adapted from training-session's source authority test
  envelope. It is not a captured hosted controller response or deployment proof.
  Its `/fixture/source` paths are relocated into the temporary test volume.
  Each owner response occupies one compact JSON line to meet the evidence
  diff budget. `evidence.json` records its file and canonical JSON SHA-256;
  the parsed contents are unchanged from the previous reviewed head.
- `fixtures/policy.json` uses a bounded historical effective window and calendar
  day freshness for the recorded 2024 bars. Deployed compose continues to use
  the existing training policy, including Taiwan trading-day freshness.
- Registry fixtures come from the existing genuine recorded-backtest gate proof
  helper; artifact checksums and gate bindings are not rewritten or bypassed.

Validation results and exact commands are in `evidence.json`. The new suite
also passes in an isolated environment containing only the existing persona
runtime lock plus pytest, so the persona lock requires no change. Both `core`
and `root` compose profiles are actually rendered and checked. This is source
and local integration evidence; it makes no hosted deployment or live-capital
claim. Canonical review and supervisor integration remain pending at handoff.

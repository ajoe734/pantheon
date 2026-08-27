# PFG-SOURCE-SNAPSHOT-20260820 evidence

Source Ingestion is now the sole owner of the paper market-input snapshot.  A
completed ingest projects normalized close records into a checksummed,
append-only and bounded per-symbol state file.  The sole consumer contract is
`GET /api/source-ingest/snapshots/latest?symbol=<canonical-symbol>`.

The endpoint only reads that local projection.  It does not load a connector,
call a provider, or start the scheduler.  Paper's normal
`CurrentArtifactStrategy` selects this endpoint only when the RuntimeBinding
names the `source-ingest` / `latest_stored_normalized` market-data policy.  A
missing, malformed, unavailable, or stale snapshot produces a typed degraded
binding and no signal.

`BoundedPaperStrategy` remains an explicitly selected smoke/profile strategy;
it is not the normal artifact runner or an alternate snapshot client.  The
normal artifact runner no longer accepts `recent_closes` as a fallback.

See `evidence.json` for scope boundaries, endpoint/client audit, and exact
verification commands.

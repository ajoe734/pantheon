# OPS-SOURCE-SEARCH-SMOKE-TIMEOUT-20260823 Evidence

## Defect

Dev deploy run `32648522828` started the bounded source/search smoke against
the persistent dev volumes. Source Ingestion and Search both became ready, but
the next request, `GET /api/source-ingest/registry`, exceeded the smoke's
10-second request timeout. The container then emitted only `timed out`, without
the active phase or last successful checkpoint.

A read-only live probe confirmed that the registry still returned no bytes
after 60.017 seconds. The persistent connector journal had reached 1,853 lines
(1,160,076 bytes), while the ingest schedule journal had reached 7,979 lines
(9,181,688 bytes). Registry construction replayed those stores through point
lookups for each connector and then rebuilt the same fleet a second time for
the embedded policy registry.

## Fix

1. Registry projection now takes one connector/fetch-state snapshot, one
   schedule snapshot, and one ingest-freshness snapshot per response. It groups
   runs and receipts in memory and reuses the completed entries for the policy
   registry.
2. The smoke has a 180-second overall measured budget and a 15-second
   per-request cap. Each request receives no more than the remaining overall
   budget.
3. Unbuffered phase output names the connector or source, elapsed/remaining
   time, and the last successful checkpoint. Timeout failures retain those
   exact fields.
4. Tests cover production-scale single-snapshot reads, two positive runs over
   accumulated state with disjoint connector IDs, and bounded negative timeout
   diagnostics.

No scheduled provider pull, recurring Source Ingestion profile, new external
host allowance, or unrestricted egress was introduced.

## Verification

- Relevant Source Ingestion and smoke suites: 65 passed in 37.73 seconds.
- Adapter checks: passed.
- Python compilation and Compose config validation: passed.
- Fresh Compose smoke: body 1.274 seconds; 21.31 seconds including dependency
  startup.
- Same-volume repeated Compose smoke: body 1.303 seconds; 4.52 seconds total.

The full machine-readable commands and incident observations are recorded in
`evidence.json`. This packet is owner-prepared evidence for independent review;
it is not a reviewer approval.

# GDELT-RAW-FEED-SOURCE-20261113 acceptance evidence

Owner: Codex. Assigned independent reviewer: Claude. Source delivery only.

`collector-gdelt` now reads the official `lastupdate.txt` event export. The
hourly cadence, source ID, fixed `Taiwan` / `semiconductor` vocabulary,
attribution, global two-consecutive-block auto-pause threshold, and single
source registration remain in place. There is no DOC API fallback.

## File choice and field semantics

The recorded 2026-11-13 11:45 index lists export at 55,892 bytes, mentions at
96,624 bytes, and GKG at 4,194,274 bytes. Export is the smallest and already
contains the structured event fields needed here. Mentions alone would require
joining export to obtain those event features; GKG adds article annotations
at much greater download size.

The [official event codebook](https://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf)
defines the tab-delimited event format. Output preserves GlobalEventID,
CAMEO codes, Goldstein scale, average tone, mention/source/article counts,
actors, action geography, and SOURCEURL. `event_time` represents the event's
Day at UTC midnight with `event_time_precision=day`. `gdelt_date_added` and
`published_at` represent DATEADDED, with `published_at_basis=gdelt_date_added`;
these do not claim the publisher's article timestamp. Language is `und` because
export does not establish the article language.

Filtering matches committed query terms case-insensitively as normalized
whole words/phrases in actor names, geographic names, and the decoded source
URL. URL separators and punctuation normalize to spaces. It does not search
article bodies or infer matches from country codes. Generated text is a
labelled event-feature summary, without invented article titles or claims.
Export therefore supplies dated event context; it cannot supply article-text
claims for hypothesis extraction.

## Recordings and prior DOC failure

`deploy/collector/tests/fixtures/gdelt_feed_recorded/provenance.json` records
URLs, a recording timestamp, exact sizes, MD5, and SHA256. Both ZIPs are
unmodified downloads from this dev host:

- `20261113114500.export.CSV.zip`: current lastupdate entry, 839 real rows,
  zero matches against the committed vocabulary.
- `20261113110000.export.CSV.zip`: archived official export, 1,197 real rows,
  three Taiwan matches. Its size/MD5 come from the unchanged three-line excerpt
  of the official masterfilelist. The historical index was used only to record
  this positive fixture; runtime reads only lastupdate, never masterfilelist.

The synthetic `gdelt_doc.json` is deleted. `gdelt_query_recorded.json` was
already absent from the task base (the prerequisite explicitly deferred that
recording); no unusable DOC response remains as a parser success fixture.
Historical evidence from completed tasks is retained as history.

`coordinator-doc-api-429.md`, `doc-api-429.headers`, and `doc-api-429.txt` copy
the coordinator's original evidence. They record repeated 429s on the dev
and collector hosts, including the collector's 10:47Z request after an
11-minute cooldown, and the source's existing auto-pause since November 11.
This task did not make another DOC API request. Negative tests inject errors
into copies of real recorded data; successful parsing uses the original ZIPs.

## Storage, failures, and retry

Each scheduled run fetches one index and at most one new export over HTTPS,
with the existing five-second request spacing. The HTTP URLs in the official
index are restricted to the official host and converted to HTTPS for fetching.
Only the latest snapshot listed at the hourly tick is collected: intervening
15-minute batches are not backfilled, so this is not exhaustive event coverage.

The poller checks compressed size and MD5 against the index, validates the ZIP
member, bounds its expanded size, and validates the event schema. Files are
atomically cached beneath `<collector-root>/state/gdelt-feed/`. Cached files
are rechecked and never downloaded again while valid. A policy-specific
receipt is written only after BundleWriter returns successfully. A failed
bundle write therefore replays the verified cache on the next run. A reviewed
vocabulary change can reprocess the cache without another ZIP request.

HTTP, network, index, integrity, or export-format failures pass a detailed
reason to the existing runner auto-pause handling. They cannot create a cache
success receipt. Repeated failures yield partial then paused bundles and
source state. Empty successful snapshots and already processed snapshots are
explicitly labelled `no_matching_records` and `no_new_feed`; they do not
increment the block counter or pretend a new successful item was collected.

## Verification and rollout boundary

`validation.txt` contains terminal output for:

```text
PYTHONPATH=deploy/collector .venv-pantheon/bin/python -m pytest -q -ra deploy/collector/tests
175 passed, 2 skipped
```

Skipped: Playwright is unavailable in this worker environment; the opt-in
collector image build test was not enabled. Neither skip exercises the event
export parser. `compileall` and `git diff --check` also exit 0. Tests verify
real fixture provenance, full bundle integrity and attribution, source feature
values, positive/empty filtering, restart deduplication, new-file detection,
cache corruption, policy changes, failed-bundle replay, request spacing,
startup policy guards, and consecutive failure auto-pause.

After merge, the coordinator owns collector-host deployment and source resume.
No SSH, deployment, source unpause, hosted ingestion, or W1 readiness proof is
claimed by this source delivery. The policy change follows the existing
config-hash resume semantics when deployed; coordinate that deployment with
the paused source's rollout rather than editing live runtime state here.

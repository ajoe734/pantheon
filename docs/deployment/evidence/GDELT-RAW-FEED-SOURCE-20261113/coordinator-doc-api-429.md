# GDELT DOC 2.0 API reachability, 2026-11-13 (coordinator evidence)

Query shape requested by COLLECTOR-DEMAND-QUERY-PLAN-R2-20261112:
https://api.gdeltproject.org/api/v2/doc/doc?query=%28%22Taiwan%22+OR+%22semiconductor%22%29&mode=ArtList&maxrecords=250&timespan=3months&format=json

Attempts (one request each, User-Agent pantheon-collector-fixture/1.0):
- dev host, about 08:54-09:27Z: worker, about 10 attempts over 10 minutes, all HTTP 429.
- collector host, 10:34:08Z: HTTP 200 with body "{}" (no articles; not usable as a fixture).
- collector host, about 10:34:15Z and 10:34:22Z (other query variants, 7 s apart): HTTP 429.
- collector host, 10:47:37Z after an 11 minute cooldown: HTTP 429 (attempt_1047Z_*).
- dev host, about 10:50Z: HTTP 429.
- The collector host's own collector-gdelt source has been auto-paused since 2026-11-11
  (auto_paused_after_2_consecutive_blocks:http_429); its last bundles (2026-11-11) have 0 items and empty raw/.
- 429 body: "Please limit requests to one every 5 seconds ... All high-traffic users should switch to our ngrams dataset".

Reachable instead, from both hosts: the official GDELT 2.0 raw 15-minute feed
http://data.gdeltproject.org/gdeltv2/lastupdate.txt (HTTP 200; lists export, mentions and gkg zip files).

Also note: deploy/collector/tests/fixtures/gdelt_doc.json on dev is synthetic (example.news.invalid,
fields id/tone and ISO seendate), not the real ArtList shape.

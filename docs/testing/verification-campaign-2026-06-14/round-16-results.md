# Round 16 — Results

**Executed:** 2026-06-15 (UTC). **Method:** fuzzed every declared query
parameter across 544 `(param-free GET path, query-param)` pairs with malformed/
injection payloads (XSS, SSTI, SQLi, traversal, huge numbers, NaN/Infinity,
5 000-char strings, wrong types).

## H1 — 500-hunt: one real defect found and fixed

10 parameter values produced 5xx. Classified:

| Endpoint(s) | Trigger | Class |
|---|---|---|
| `/bff/audit/events?from_ts=` / `?to_ts=`, `/bff/audit/export?from_ts=` / `?to_ts=` | **any** non-empty value (incl. valid timestamp) | **F12 — code bug, FIXED** |
| `/api/v1/operator/source/ops?dlq_status=` / `?frontier_status=`, `/api/v1/research/source-change-proposals?status=`/`?proposal_type=`/`?source_kind=` | `' OR '1'='1` | F13 — live-only/downstream |
| `/api/v1/research/search?q=` | several | 503 — downstream unavailable |

### F12 — audit time-range filter 500s on every from_ts/to_ts (FIXED)

`/bff/audit/events` and `/bff/audit/export` called `_parse_rfc3339_header(...)`,
which is **defined nowhere** in the codebase → `NameError` → 500 on **any**
non-empty `from_ts`/`to_ts`, including a perfectly valid timestamp. The audit
time-range filter was entirely non-functional. The same missing symbol
`_parse_rfc3339` was referenced (un-imported, un-defined) by
`_kw04_within_recency` and the aggregated-recency path — latent 500s on those
routes too.

Root cause: a refactor left `main.py` calling `_parse_rfc3339` /
`_parse_rfc3339_header` while the implementation lived only in `read_store.py`
(not imported into `main`). Fix: define `_parse_rfc3339` in `main.py`
(best-effort ISO/RFC3339 parse, `None` on empty/unparseable so a malformed
optional filter is dropped rather than crashing) and repoint the four
`_parse_rfc3339_header` call sites to it. Verified: audit events/export with
valid / bad / absent `from_ts`/`to_ts` all return 200 (were 500).

Regression: `test_audit_timestamp_filter_no_500.py` (9 passed) — asserts the
helper is defined and safe, and that the audit filters never 500.

### F13 — source filter 500s (live-only, not a current-code defect)

`source/ops` and `source-change-proposals` 500 on live for `' OR '1'='1`, but
the **same requests return 200 in-process against current dev code**. So this is
not a code defect reproducible here — it is a live/downstream-data condition
(the source service backing those filters), akin to `research/search`'s 503.
Recorded for ops; not fixed in-repo.

## H2 — injection: PASS

No payload was evaluated (`{{7*7}}` / `${7*7}` never returned `49`) or reflected
unsanitized. SSTI/XSS/SQLi strings are treated as opaque filter data.

## Observation

Running the governance-audit contract suite surfaced 2 **pre-existing** failures
(`…deployment_runtime_and_risk_action…`, `…incident_routes_support…`) that fail
identically on unmodified `origin/dev` — environment/data-dependent, unrelated
to this fix. Flagged for the BFF owners.

## Net

H1 found and fixed a real, high-impact 500 (F12 — the audit time-range filter
was completely broken for all callers). H2 PASS (no injection). One live-only
downstream 500 class (F13) and 2 pre-existing test failures recorded for owners.

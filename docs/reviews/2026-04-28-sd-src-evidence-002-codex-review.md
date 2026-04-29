# SD-SRC-EVIDENCE-002 Review - Durable source evidence and search store

Reviewer: Codex
Date: 2026-04-28
Owner: Codex2
Disposition: APPROVE

## Resolved Blocking Finding

### 1. Durable RW-02 replay can pollute narrower BFF searches

`ReadSurfaceStore._build_research_search_repository` now opens the shared
durable RW-02 evidence log before adding the current request's eligible
documents (`services/control-plane/bff/read_store.py:7289`). That means a
previous broad search can leave ticket, experiment, and artifact knowledge
objects in `rw02-evidence.jsonl`. A later narrower request, such as
`match_type=artifact`, computes the correct `eligible_documents` first, but
then searches the whole replayed repository (`read_store.py:7421`) and only
projects back to the eligible document map after ranking.

Because `SearchGateway.search` ranks every replayed knowledge object that
passes ACL/license/source filters (`services/search/gateway.py:86` and
`services/search/gateway.py:101`), the `top_k=max(len(eligible_documents), 1)`
window can be consumed by higher-scoring stale objects from the previous broad
search. The BFF projection then drops those non-eligible results, returning an
empty page even when an eligible artifact match exists.

Reproduction:

```text
PYTHONPATH=services/control-plane/bff:/home/edna/.local/lib/python3.12/site-packages python3 - <<'PY'
import os, tempfile
from read_store import ReadSurfaceStore

with tempfile.TemporaryDirectory() as td:
    store = ReadSurfaceStore(os.path.join(td, "read_surfaces.json"), allow_local_snapshot_fallback=True)
    print([(r["result_id"], r["match_type"]) for r in store.list_research_search_results(query="momentum", match_type="artifact")])

with tempfile.TemporaryDirectory() as td:
    store = ReadSurfaceStore(os.path.join(td, "read_surfaces.json"), allow_local_snapshot_fallback=True)
    store.list_research_search_results(query="momentum", match_type="all")
    print([(r["result_id"], r["match_type"]) for r in store.list_research_search_results(query="momentum", match_type="artifact")])
PY
```

Original observed output:

```text
artifact fresh [('artifact-20260418-005', 'artifact')]
artifact after all []
```

Expected: the second artifact query should still return
`artifact-20260418-005`. Durable replay must preserve refs without changing
the BFF backend-owned filter semantics.

Resolution: fixed. `ReadSurfaceStore._build_research_search_repository` now
persists/replays the shared RW-02 evidence log, then copies only the current
request's eligible `result_id` records into a scoped repository before
`SearchGateway` ranks results. The broad-then-artifact replay regression now
passes and verifies `meta.governed_evidence` only contains the eligible
artifact refs.

## Verification Run

The fixed repro now returns the expected artifact result before and after a
prior broad durable replay:

```text
artifact fresh [('artifact-20260418-005', 'artifact')]
artifact after all [('artifact-20260418-005', 'artifact')]
```

Targeted suite:

```text
PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages python3 -m pytest -p no:cacheprovider \
  services/source_ingestion/tests \
  services/knowledge/evidence/tests \
  services/search/tests \
  services/control-plane/bff/test_rw02_search_contract.py \
  services/control-plane/bff/test_kw03_evidence_refs_contract.py -q
......................                                                   [100%]
22 passed in 3.19s
```

Additional review checks:

```text
PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages python3 -m pytest -p no:cacheprovider services/control-plane/bff/test_rw02_search_contract.py -q
......                                                                   [100%]
6 passed in 2.09s

PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages python3 -m pytest -p no:cacheprovider services/knowledge/evidence/tests services/search/tests -q
.........                                                                [100%]
9 passed in 0.25s
```

## Disposition

Approve. `SD-SRC-EVIDENCE-002` now meets the durable source/evidence/search
acceptance: source records, evidence items, bundles, knowledge objects, and
search refs replay from service-owned JSONL stores; search snapshots persist
evidence-bundle refs without raw answer payloads; and BFF RW-02 governed
evidence remains stable after durable replay without broad-query pollution of
narrower backend-owned filters.

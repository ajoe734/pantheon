# Retrieval evaluation from recorded queries

Every query served on the hybrid backend is written to `retrieval_query_log`
in retrieval-postgres (query text, caller decision type, source event id,
returned ids, mode, model, latency). Humans add `retrieval_query_labels`
(`source_event_id` per query). No query set is generated, templated or loaded
from a file.

```bash
python services/search/evaluation/run_retrieval_eval.py --tenant default
```

Exit 2 and `status: unavailable` with a reason while fewer than 150 real
labelled queries exist or the set looks templated. Persona-private rows are
excluded by row-level security. Model/mode comparison is
RETRIEVAL-MODEL-EVAL-REAL-QUERIES-20261113.

Rebuild the index from canonical stores: `python -m services.search.rebuild_index`.

# Retrieval candidate validation — acceptance withdrawn

The `aad34700e262211b7de349921421a61961948749` evaluation does not establish
backend selection or task completion. Its 10,000-record corpus combines
210 embedded fixtures and 9,790 random-vector distractors. Queries and labels
come from the same templates, with no independent adjudication, source-family
holdout or executed prior-runtime baseline. Timings reuse the query embedding
cache and omit authorized owner hydration. Qdrant was tested dense-only.
These limitations invalidate the previously reported eight passing gates.

The historical `retrieval_manifest.json` is not accepted evidence. It was added
by the earlier delivery outside the task's exact artifact grant; canonical
scope correction is needed before editing or removing that file. The v2 schema
and current runner explicitly reject its v1 admission claim. No fixture is
claimed to be human labelled, operational evidence or a valid held-out set.

Product Search, Memory and negative-memory consumers retain the prior sole
ranker. Candidate PG, Qdrant and memory projection modules are disconnected
prototypes. Existing tenant/as-of checks remain; no new backend is selected.
Infrastructure must not activate these prototypes from the backend contract.

The runner's default command is read-only and does not load an embedding
model or contact a backend:

```bash
.venv-pantheon/bin/python services/search/evaluation/run_retrieval_eval.py
```

It reports unresolved acceptance and exits **2**. An optional bounded local
diagnostic runs 4–24 unique queries at concurrency 4, including actual uncached
embedding and PG search, against the existing task test database only:

```bash
timeout 120s .venv-pantheon/bin/python services/search/evaluation/run_retrieval_eval.py \
  --probe-local --samples 12 --report /tmp/retrieval-local-probe.json
```

This also exits **2**, even if every diagnostic request succeeds. It performs
no DDL, corpus reset, indexing or corpus upload. The mixed-vector index and
missing owner hydration make these timings unsuitable for the acceptance gate.
A timeout or killed batch is incomplete evidence, never passed evidence.

Before adoption, supply authoritative authenticated scope and owner identity,
ACL, version and expiry data without inventing defaults; prove nonowner,
nonbypass database access. Independently adjudicate a new source-derived
validation/holdout split and execute the old-runtime baseline, a real embedded
10k corpus, full local PG and Qdrant dense/sparse comparison, negative-memory
false positives, current owner hydration and 1000 full requests at concurrency
4. Re-prove replay/tombstones/checkpoints, restart/rebuild, timeouts and zero
scope/count/citation leakage. Freeze exact model/dependency/image identities
and operational costs before publishing a selected backend. The task remains
unfinished pending those requirements and independent review.

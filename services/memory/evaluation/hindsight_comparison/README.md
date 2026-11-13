# Hindsight comparison harness (dev host, evaluation only)

Compares Hindsight (MIT; PostgreSQL + pgvector + pgroonga) used as a **rebuildable retrieval
index** against the current memory retrieval. Canonical memory stays owner-written; Hindsight
only holds a derived index. Nothing here touches hosted, `docker-compose.yml`, deploy or release
scripts. Running the real-data comparison and the recommendation belong to
MEMORY-HINDSIGHT-COMPARISON-RUN-20261113.

## Start / teardown
```bash
export HINDSIGHT_EVAL_PG_PASSWORD=...           # local only, never committed
export HINDSIGHT_EVAL_LLM_PROVIDER=openai HINDSIGHT_EVAL_LLM_MODEL=... HINDSIGHT_EVAL_LLM_API_KEY=...
# fail-closed start: `up` is chained with && behind the free-disk check, so it cannot run when
# the check fails (less than 10 GB free, or free space cannot be determined)
free_gb=$(df -BG --output=avail "$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /)" 2>/dev/null | tail -1 | tr -dc 0-9)
[ -n "$free_gb" ] && [ "$free_gb" -ge "${HINDSIGHT_EVAL_MIN_FREE_GB:-10}" ] \
  && docker compose -f docker-compose.eval.yml up -d --build \
  || { echo "not started: need >= ${HINDSIGHT_EVAL_MIN_FREE_GB:-10} GB free (have: ${free_gb:-unknown}) or compose failed" >&2; false; }
docker compose -f docker-compose.eval.yml down -v   # teardown incl. volumes (models cache too)
```
Binds 127.0.0.1 only; compose project name `pantheon-hindsight-eval`. Images: Hindsight API pinned by
digest; PostgreSQL built inline in the compose file from the digest-pinned `groonga/pgroonga` base plus Debian `postgresql-17-pgvector`
(no upstream image carries both). Embeddings: local `intfloat/multilingual-e5-large`
(query/passage prefixes are applied by the provider); reranker: local `BAAI/bge-reranker-v2-m3`.
The LLM provider/model are configuration, recorded (never the key) in results.

## Run
```bash
python -m services.memory.evaluation.hindsight_comparison.export_real_events --out DATA --tenant-id T --persona-id P
python -m services.memory.evaluation.hindsight_comparison.run_comparison --data DATA --out RESULTS
```
Export reads owner GET APIs only (env: `PANTHEON_{POSTMORTEM,EVOLUTION,RESEARCH,PERSONA,CONSULTATION}_API_URL` and
`_SERVICE_TOKEN`), converts events with the production `write_learn_feedback`, lists unmappable
sources in `unmapped.json`, and writes `manifest.json`. The runner refuses a dataset without a valid manifest.
Owner routes read: `GET /api/postmortems`, `/api/evolution/proposals`, `/api/consult/memos`,
`/api/research/persona-demands`, `/api/personas/{id}/strategy-pool/versions` (decision subjects are derived by diffing
consecutive versions' `members`). The research service has no findings route; that source is listed in `unmapped.json`.
Current-arm retrieval calls the production `retrieve_*_with_backend` wrappers; set `PANTHEON_EVAL_SEARCH_API_URL` to an
isolated eval search service for the hybrid path (never the hosted one; equal to `PANTHEON_SEARCH_API_URL` is refused),
otherwise keyword fallback is used and counted in `results.json` `current_retrieval`. Duplicate rate uses local
`multilingual-e5-large` embeddings (cosine >= 0.95) when `sentence_transformers` is installed (`--no-embed` to skip);
`duplicate_embedding` in `results.json` records availability.
Outputs: `results.json`, `blind_sheet.json` (give to the labeller), `blind_key.json` (keep back),
`ranked_results.json`; score labels with `metrics.score_precision_at_k`.

Tests: `pytest services/memory/evaluation/hindsight_comparison` (fixtures + in-memory fake API; not comparison results).

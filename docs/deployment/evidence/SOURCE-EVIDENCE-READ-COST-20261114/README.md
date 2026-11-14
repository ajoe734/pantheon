# SOURCE-EVIDENCE-READ-COST-20261114

This is source and local integration evidence. `hosted_behavior_claimed` is
`false`. Hosted acceptance belongs to
`MGMT-GATE-FULL-PASS-HOSTED-ACCEPTANCE-20261114` after the reviewed source is
integrated and deployed by the Root gate.

## Delivered behavior

`GET /api/source-ingest/evidence/items` accepts `include_summary=false` and a
comma-separated `linked_entity_types`. Omitting the summary flag preserves the
existing whole-tenant summary contract. Explicitly requesting a summary still
returns exact tenant totals regardless of the page/window/link filter.

Quarterly ranking, drilldown and recommendations opt out of summaries and
request the eleven entity types used by `_pm12_attach_ranking_evidence`:
artifact, capital_pool, capital_sleeve, paper_ledger, persona, persona_binding,
persona_capital_binding, runtime, runtime_binding, strategy and strategy_spec.
Linkage hints only narrow owner transfers. They do not promote metadata into
new BFF evidence links or change ranking, redaction, or time-window semantics.

PostgreSQL stores generated tenant, capture-time, linkage and summary
projections. A partial GIN index supports linked-type overlap; a partial
covering B-tree supports summaries. The immutable linkage reducer trims and
lowercases the same five metadata linkage shapes as the in-memory repository.
Independent writers update these projections and indexes in their normal
transaction; there is no application cache or counter reconciliation job.

The BFF checks the owner's echoed filter and summary flags. An older or
partially upgraded owner falls back to the complete legacy population. If an
owner replica loses support after the first filtered page, the BFF discards
that population and restarts the complete read under the original deadline.
Unsupported capabilities are local to the request; later requests can use an
upgraded owner. Failure of any page remains an unavailable evidence surface.

## Verification and reproduction

The committed tests seed 9,000 tenant evidence items in a disposable real
PostgreSQL 16 schema: 7,200 source-record links and 1,800 ranking-type links,
with all five linkage shapes, two confidence levels, list-heavy metadata and
2,000-character bodies. Another fixture supplies 166 personas in addition to
the existing local read fixtures. It invokes the production source owner route
and production PersonaService routers through TestClient, with PostgreSQL doing
the actual filtering, paging and summaries. Other owner surfaces use existing
local fixtures; there is no hosted network or full production-service claim.

`evidence.json` records timings, query plan, commands and the acceptance mapping.
To reproduce, provision the repository's isolated interpreter, supply a
disposable database DSN, and run the listed suites:

```sh
python3 scripts/dev/provision_python_distribution.py
export SOURCE_INGEST_TEST_POSTGRES_DSN='<disposable PostgreSQL DSN>'
export SOURCE_EVIDENCE_COST_MEASUREMENTS=/tmp/source-evidence-measurements.jsonl
timeout 180 .venv-pantheon/bin/python3 -m pytest -q \
  services/source_ingestion/tests/test_quarter_evidence_owner_read.py \
  services/source_ingestion/tests/test_evidence_read_cost.py \
  services/source_ingestion/test_pg_store.py \
  services/control-plane/bff/tests/test_quarterly_ranking_read_latency.py \
  services/control-plane/bff/tests/test_quarter_evidence_linked_filter.py \
  services/control-plane/bff/tests/test_evidence_source_backend.py \
  services/knowledge/evidence/tests --durations=5
```

Without a reachable test PostgreSQL, database tests skip; such a run cannot
substitute for this task's performance evidence. Normal tests do not write
repository evidence. The optional measurement sink writes only the explicitly
provided path.

## Deployment boundary

Source owner bootstrap (enabled by default through
`SOURCE_INGEST_EVIDENCE_BOOTSTRAP`) adds the immutable reducer, generated
columns and indexes idempotently, including on existing rows. It requires the
source owner's schema DDL privileges. If bootstrap is disabled, apply the owner
schema migration before starting the new reader. Adding stored columns rewrites
the table and takes a schema lock; the hosted gate must account for that during
deployment. Read-only source-record consumers retain their existing queries and
do not acquire schema ownership. Old binaries can ignore the added columns.

The production deployment, real network contention, hosted browser six-second
gate and exact served FE/BFF identities remain the next Root gate's evidence.

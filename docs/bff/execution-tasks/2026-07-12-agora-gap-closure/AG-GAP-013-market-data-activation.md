# AG-GAP-013: Agora market-data activation readback (SRCLIVE line)

## Scope

The Agora shell is live but the operator experience is empty:
`/bff/agora/markets`, watchlist, and signal surfaces return zero rows on dev
(verified 2026-07-12). The upstream cause is the SRCLIVE line: source-ingest
live activation (SRCLIVE-001) still lacks production acceptance
(2026-07-06 inventory packet). This task closes the Agora-side readback once
upstream activation lands — it does not reimplement ingestion.

## External gate

Blocked on SRCLIVE-001 live activation acceptance
(`docs/bff/execution-tasks/2026-07-06-srclive-production-closeout/SRCLIVE-001-live-activation-acceptance.md`).
Do not start the readback proof before that evidence exists; record a blocker
instead of an empty-handed done.

## Work

1. Once SRCLIVE-001 acceptance evidence exists, wire/verify the projection
   path from live source-ingest data into the Agora read surfaces
   (`/bff/agora/markets`, daily watchlist/signals sections). The projection
   ETL pattern is `scripts/project_consultation_to_bff_agora_surfaces.py`
   ("no rows are invented") — extend or add a sibling projector for market
   surfaces if none exists.
2. Prove live readback: `/bff/agora/markets` returns non-empty rows sourced
   from real ingested data (TW 2330 line per the SRCLIVE runbook), and
   `/bff/agora/daily` KPIs reflect them.
3. No fabricated rows; every row must trace to an ingestion record.

## Acceptance

- Live dev proof: non-empty `/bff/agora/markets` with provenance tracing to
  source-ingest records; `/bff/agora/daily` watchlist/signal sections reflect
  the same data.
- Projection runs via a documented, repeatable command (cron or on-demand),
  not a one-off hand edit.
- Evidence under `docs/deployment/evidence/ag-gap-013/`.

## References

- `docs/bff/execution-tasks/2026-07-06-srclive-production-closeout/`
- `scripts/project_consultation_to_bff_agora_surfaces.py`
- `services/source_ingestion/` (FinMind activation runbook)

# E2E-R13 — BFF surface-status consistency (agora journal contradiction)

**Round:** E2E-R13 (second campaign)
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r13-agora
**Business flow:** any BFF list surface returns both data and a
`meta.surfaces[*]` health status; the two must not contradict.

## Verification program

`scripts/verify_e2e_surface_status_consistency.py` (+ unit test), wired into
`run-acceptance.sh` full as `e2e-surface-status-verifier`. Sweeps 19 BFF list
surfaces and FAILs on any that returns items while reporting its source
`unavailable` / `missing`.

## Live result (dev, 2026-06-15)

```
surface-status consistency over 19 list surfaces:
  contradictions: 1
FAIL: /bff/agora/journal: 3 items but surface agora_journal_list
      status=unavailable source=missing
```

(Other agora surfaces — signals, inbox, ask/sessions — are empty AND unavailable,
which is self-consistent. postmortems is ok. Only the journal contradicts.)

## Finding

`/bff/agora/journal` serves **3 entries while its surface meta reports
`status: unavailable, source: missing`** — a self-contradictory response. An
operator console reading the surface status sees the journal as down while it is
serving rows (or the 3 rows are a stale local fallback served while the live
source is reported gone). Either way the data and the declared health disagree,
which misleads any consumer that trusts the surface badge.

This is a focused surface-status computation bug, distinct from the
rescue-placeholder data gaps of the first campaign: here data IS present; it is
the *status* that is wrong.

## Disposition

- **Shipped (code/CI):** the surface-status consistency verifier + logic test + CI
  gate — catches any "data present but source unavailable" surface going forward
  (currently FAILs on agora_journal).
- **Flagged (BFF):** reconcile `agora_journal_list` surface-status computation
  with the data path — if the journal serves entries, its source must not be
  reported `missing`.

## Next round

E2E-R14: SSE / streaming surface reachability + auth, then deeper rounds.

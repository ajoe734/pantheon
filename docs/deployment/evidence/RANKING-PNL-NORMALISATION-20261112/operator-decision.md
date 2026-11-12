# PnL return-on-capital formula decision — 2026-11-12

Task: `RANKING-PNL-NORMALISATION-20261112`; owner Codex; reviewer Claude.

The canonical task records the operator's 2026-11-12 decision, 「1 同意」,
approving PnL scoring as return = PnL ÷ persona capital. This record transcribes
that task-scoped authorization; it does not assert a new operator signature or
independent reviewer approval.

The preceding discrimination audit is
`docs/04/pantheon_strategy_discovery_sources_2026-11-10/RANKING_SCORE_DISCRIMINATION_AUDIT_2026-11-12.md`.
It establishes that `pm12-default-v1` fed absolute currency PnL into
`clamp(50 + 100 * pnl)`, saturating the three audited personas at 100.

The new version is `pm12-return-on-capital-v2`:

- PnL score = `clamp(50 + 100 * (pnl / capital), 0, 100)`.
- Existing weights remain PnL 0.35, risk 0.25, execution 0.25, activity 0.15.
- Use a positive finite `initial_cash` from the same cumulative PnL snapshot,
  its ledger start, and PnL's own valuation time. Never substitute a pool budget,
  account default, current weight, or synthetic opening equity.
- Quarterly scoring accepts only a performance period contained in the requested
  quarter. A ledger opened during that quarter supplies a since-inception,
  quarter-to-date observation. Older ledgers require owner-backed quarter opening
  equity and remain unknown here; this change does not reconstruct that history.
- Missing or incompatible capital, PnL, period or currency evidence leaves PnL and
  overall scores unknown, tier/rank unknown, and allocation ineligible. A genuine
  zero PnL with valid capital is a known 50 PnL score.
- Deduplicate cumulative observations by runtime and PnL time; use the latest
  complete tuple rather than summing a runtime's cumulative history. Multiple
  runtimes need complete, matching periods and explicit matching currencies.
- Historical v1 snapshots keep their original content/digests and remain readable.
  Allocation adapters accept v1 and v2; unsupported versions fail closed.

This is source delivery. No deployment, allocation proposal, capital change,
production write, or hosted activation is claimed.

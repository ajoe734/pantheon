# Internal Research Note: TW Equity Momentum Quality Seed

Date: 2026-05-16
Author: research-ops
Access: research

## Thesis

Rank Taiwan listed equities by a combined momentum and quality signal, then
evaluate whether the top decile outperforms the bottom decile over the next
five trading days.

## Strategy Seed

- Hypothesis: TWSE and TPEx equities with positive 60-day momentum, stable
  profitability, and moderate volume expansion can rank five-day forward
  returns better than a sector-neutral baseline.
- Market scope: Taiwan, TWSE, TPEx
- Asset class: equity
- Holding period: 5 trading days
- Required data: point-in-time daily OHLCV, adjusted close, fundamentals,
  sector classifications, five-day forward return labels
- Backend hint: qlib
- Feature hints: 60-day momentum, return volatility, turnover expansion,
  gross margin stability
- Label hints: five_day_forward_return
- Risk notes: survivorship bias check, corporate-action adjustment check,
  sector neutralization review

This note is research-only evidence. It does not authorize broker routing,
capital binding, order generation, canary deployment, or live execution.

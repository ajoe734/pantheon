# Internal Research Note: TW Momentum Quality Production Seed
Source Record ID: src-note-tw-momentum-quality-001
Date: 2026-05-17
Author: research-ops
Access: research
License: internal_research
Confidence: 0.86

## Hypothesis
TWSE and TPEx equities with positive 60-day momentum, stable profitability,
and moderate volume expansion can rank five-day forward returns better than a
sector-neutral baseline.

## Universe
- Symbols: universe:twse-tpex-top200
- Venues: TWSE, TPEx
- Asset class: equity

## Frequency
daily

## Risk Caps
- Max position pct: 3%
- Max gross exposure pct: 35%
- Max single sector pct: 25%

## Data Requirements
- point-in-time daily OHLCV
- adjusted close
- fundamentals
- sector classifications
- five-day forward return labels

## Feature Hints
- 60-day momentum
- return volatility
- turnover expansion
- gross margin stability

## Label Hints
- five_day_forward_return

## Evaluation
- Metrics: information_coefficient, sharpe_ratio, max_drawdown

## Strategy Seed
- Backend hint: qlib
- Holding period: 5 trading days
- Risk notes: survivorship bias check, corporate-action adjustment check

## Code Refs
- repo_ref: pantheon; path: services/research/qlib/adapter/qlib_adapter.py; symbol: QlibAdapter; line_start: 80; line_end: 140; association: strategy_code_reference

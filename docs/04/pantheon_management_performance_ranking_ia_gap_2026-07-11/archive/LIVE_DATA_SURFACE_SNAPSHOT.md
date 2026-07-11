# Live Data Surface Snapshot - 2026-07-11

Status: archived observation, not a fixture contract

The audit sampled the current dev BFF surfaces to determine whether page gaps
were caused by information architecture, frontend normalization, or missing
live data.

| Surface | Observed rows |
|---|---:|
| Personas | 18 |
| Strategies | 1 |
| Portfolio exposure | 19 |
| Portfolio holdings | 14 |
| Formal persona attribution | 5 |
| Persona League | 18 |
| Quarterly Ranking | 18 |
| Quarterly recommendations | 20 |
| Capital pools | 19 |
| Rebalances | 0 |
| Ranking formulas | 0 |

## Interpretation

- Performance and ranking surfaces have enough rows to implement canonical
  centers and migration behavior.
- Attribution coverage is smaller than persona and exposure coverage, so
  partial and unmatched-source states are normal operating conditions.
- Rebalance and ranking-formula detail cannot claim live authority while their
  observed collections are empty. Detail routes need honest unavailable states
  until the contracts are populated.
- Strategy-level comparison is currently sparse and must not invent breadth.
- Row counts are diagnostic evidence only. Implementations must use timestamps,
  source confidence, and binding coverage rather than assuming these counts are
  stable.

## Required Contract Tests

- formal attribution with complete bindings;
- partial attribution with missing holdings;
- fallback Persona Fleet summary clearly labeled;
- degraded or stale telemetry;
- empty rebalance and policy collections;
- null metric handling without operator-facing `nan`;
- rankings with exclusions and incomplete evidence;
- recommendations that reference immutable ranking snapshots.

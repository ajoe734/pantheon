# Round 34 — Results

**Executed:** 2026-06-15 (UTC). **Method:** AST division scan + per-site guard
inspection.

## H1 — reachable divisions guarded: PASS

The scan flagged 167 division sites, but most are `pathlib.Path / "str"` joins
(not numeric — false positives). The genuine numeric divisions in **reachable
HTTP paths** all guard the denominator:

| Site | Guard |
|---|---|
| BFF pagination `// page_size` | `page_size` validated `ge=1` |
| BFF `fallback / max(split_count, 1)` | `max(…, 1)` |
| BFF `len(items) / observed_minutes` | `observed_minutes = max(…, 1.0)` |
| BFF `sum(values) / len(values)` | `… if values else None` |
| BFF `… / risk_budget_total` | `if risk_budget_total not in (None, 0)` |
| BFF `… / denominator` (share) | `… if denominator else 0.0` |
| source_ingestion `failed_attempts / total_attempts` | `total_attempts = max(1, …)` |

No reachable request path can hit a zero denominator.

## Observation (O8, not fixed)

A few **internal** compute helpers in the optimizer/governance services divide by
`len(proposals)` / `total` without an entry guard — e.g.
`optimizer-svc/portfolio_synthesis/conflict_classifier.py` (`1.0 / len(proposals)`,
`weight / total`), `governance/multi_persona/sponsor_resolver.py`
(`1.0 / len(proposals)`). These would `ZeroDivisionError` only if invoked with an
**empty** proposal set. Whether a caller can reach them with empty input is not
confirmed (no live repro), so they are recorded as a defensive-guard
recommendation (`if not proposals: return …`) for the optimizer/governance
owners rather than fixed speculatively — consistent with the campaign's
"fix-confirmed, document-speculative" discipline.

## Net

H1 **PASS** — reachable divisions are guarded; no confirmed ZeroDivision 500.
One defensive-hardening observation (O8) on internal helpers, documented for
owners.

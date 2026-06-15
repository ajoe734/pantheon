# Round 11 — Results

**Executed:** 2026-06-15 (UTC). **Target:** dev BFF.

## H1 — aggregate math: PASS

`/bff/management/persona-fleet` `summary` = `{total:15, critical:0, degraded:1,
healthy:14}`. Recomputed from the 15 returned items: `healthy×14, degraded×1`.
Exact match; `page_info.total=15` agrees.

## H2 — total vs item count: PASS

Swept 99 param-free GET list endpoints exposing a `total`. **Zero** mismatches
between declared `total` and `len(items)` on non-paginated responses — no
miscount or off-by-one anywhere.

## H3 — cross-surface population consistency: PASS

`persona-league` (12) ⊆ `persona-fleet` (15). The 3 extra in fleet are exactly
`persona-crypto`, `persona-tw-equity`, `persona-us-equity` — the
market-persona-defaults added by `include_market_persona_defaults=True`. The two
surfaces agree on the real persona population; the delta is the documented
filter. `league - fleet = ∅`.

## H4 — derived fields: PASS (with semantic note)

14/15 personas report `governanceRequired=true` with
`recommendedGovernanceAction=null`. Traced to `main.py:23964-23965`:
`governanceRequired` is `metadata.get("governance_required", **True**)` — it
**defaults to true** when metadata is silent — and `recommendedGovernanceAction`
is explicit metadata (null when unset). So the pattern is the metadata-driven
default, not a per-persona miscomputation. Confirmed internally consistent.

**Semantic note (O4, not a defect):** because `governanceRequired` defaults to
true, it is not a reliable "needs attention now" signal — it is effectively
"governance-in-scope unless opted out". The degraded `persona-tw-equity` carries
a `slippage_watch` risk flag but no recommended action: actions are explicit
metadata and are **not** auto-derived from risk flags (a product feature gap,
not a correctness bug).

## Net

H1–H4 **PASS** — the aggregation/counting/cross-surface layer is numerically
correct, beyond mere shape. No defect this round; one semantic note (O4) on the
default-true governance flag recorded for product awareness.

# BFF-CONSOL-007 Review — Claude

Date: 2026-05-13  
Reviewer: Claude  
Task: BFF-CONSOL-007 Seed taxonomy spreadsheet  
Artifacts reviewed:
- `docs/bff/seed-taxonomy-2026-05-13.md`
- `docs/bff/seed-taxonomy.json`

## Acceptance Criteria Check

| Criterion | Status | Notes |
|---|---|---|
| seed.ts 所有 helper 都被分類 | PASS | 83 helpers enumerated from `src/lib/bff-v1/seed.ts` (path discrepancy from brief documented) |
| 每個 helper 標 live_required\|mock_only_dev\|deprecated\|deferred | PASS | All 83 entries carry a valid category |
| deferred 註明對應 follow-up task | PASS | All 25 deferred entries have non-empty `follow_up_tasks`; minimum reference is BFF-CONSOL-025 |
| taxonomy JSON 可被前端腳本 import | PASS | Valid JSON with `schema_version`, `helpers[]`, typed `category`/`live_routes`/`follow_up_tasks`/`priority` fields |
| doc 提供 elimination 優先順序 | PASS | P0–P3 table in markdown; matching `elimination_order` array in JSON |
| Reviewer 簽核 taxonomy 為下游消費基準 | APPROVED | See below |

## Verification Commands

```
python3 -c "
import json
with open('docs/bff/seed-taxonomy.json') as f:
    d = json.load(f)
helpers = d['helpers']
print('Total helpers:', len(helpers))           # 83
from collections import Counter
cats = Counter(h['category'] for h in helpers)
print('Categories:', dict(cats))               # live_required=52, mock_only_dev=4, deprecated=2, deferred=25
deferred_missing = [h['name'] for h in helpers if h['category']=='deferred' and not h.get('follow_up_tasks')]
print('Deferred missing follow_up_tasks:', deferred_missing)  # []
names = [h['name'] for h in helpers]
dupes = [n for n,c in Counter(names).items() if c>1]
print('Duplicate names:', dupes)                # []
no_priority = [h['name'] for h in helpers if not h.get('priority')]
print('Missing priority:', no_priority)         # []
"
```

All assertions pass.

## Observations

1. **Path discrepancy documented correctly.** The task brief names `src/lib/bff/seed.ts` but the actual file is `src/lib/bff-v1/seed.ts`. The artifacts call this out explicitly in both the markdown preamble and the JSON `source.notes` field — downstream consumers will not be misled.

2. **Category definitions are precise and actionable.** The four-way split (live_required / mock_only_dev / deprecated / deferred) maps directly onto the BFF-CONSOL-015, -019/020/021/024, and -025 downstream task scopes.

3. **Backend route cross-check is adequate.** live_routes are populated for all live_required helpers; deferred helpers correctly document the absence of a matching backend route with a concrete remediation path.

4. **Elimination priority order is suitable for BFF-CONSOL-025.** P0 covers command/session/security surfaces; P1 covers detail journey surfaces with existing parent routes; P2 covers governance/evolution adjuncts with no current route; P3 covers dev-only instrumentation chips.

5. **`bff.mcpSecrets.forServer` correctly marked `mock_only_dev` with P0 priority** — the note that real secret values must never be exposed is present and correct.

6. Minor: `bff.me.invalidate` has `live_routes: []` and is `live_required`. The notes clarify it is a local cache helper (not a network call), which is accurate and acceptable.

## Decision

**APPROVED.** The taxonomy is consistent, verifiable, and ready to serve as the authoritative classification input for BFF-CONSOL-015 (mock badge) and BFF-CONSOL-025 (seed elimination). No changes requested.

Returning to owner (Codex) for finalization.

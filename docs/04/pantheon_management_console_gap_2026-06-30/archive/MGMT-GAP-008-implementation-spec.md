# MGMT-GAP-008 Implementation Specification

**Task:** Fix live-id detail DTO/render honesty  
**Owner:** Claude  
**Reviewer:** Copilot  
**Status:** Specification prepared - awaiting implementation  
**Reference:** full-reaudit-addendum-2026-07-01.md  

## Overview

Management console detail pages are currently rendering incomplete or inaccurate data. This spec defines the required fixes to ensure all detail pages render truthful, complete information about live system entities.

## Critical Issues to Fix

### 1. DTO Honesty - status/risk/owner/update Fields

**Problem:** Several detail pages render undefined or blank fields instead of actual data.

**Affected Pages:**
- `/management/capital/:id` (Pool details)
- `/management/experiments/:id` (Experiment details)
- `/management/artifacts/:id` (Artifact details)
- `/management/deployments/:id` (Deployment plan details)
- `/management/channels/:id` (Channel details)

**Issues per Page:**
| Page | Issue | Root Cause | Fix |
|---|---|---|---|
| `/management/capital/pool-*` | status.undefined, risk.undefined, blank owner/update | DTO missing or not populated | Ensure CapitalPoolDetail DTO includes status, risk_policy_ref, owner, last_updated from BFF /bff/capital-pools/{id} |
| `/management/experiments/exp-*` | h1 blank, status.undefined, risk.undefined | DTO title/status missing | Populate experiment name from experiment_id lookup or /bff/research-experiments endpoint |
| `/management/artifacts/rart-*` | status.undefined, risk.undefined, blank owner/update | DTO incomplete | Load from /bff/artifacts/{id} with full DTO contract |
| `/management/deployments/plan-*` | h1 blank, status.undefined, risk.undefined, blank owner/update | DTO not fully loaded | Use /bff/deployments/{id} with DeploymentPlan full contract |
| `/management/channels/:id` | status.undefined, risk.undefined, blank fields | DTO minimal | Expand channel detail DTO from /bff/channels/{id} |

**Acceptance Criteria:**
- [ ] All detail pages render non-undefined status, risk_policy_ref, owner, and last_updated values
- [ ] Page h1/title is never blank
- [ ] Blank fields must be explicitly shown as "Not set" or "Unavailable" rather than omitted
- [ ] All values match the live BFF data (no stale/mock values)

### 2. NaN% and Numeric Field Honesty

**Problem:** Percentage fields may render as NaN or invalid values when data is missing.

**Affected Fields:**
- Capital pool risk percentage
- Experiment completion percentage
- Any ratio or percentage in detail pages

**Fix Strategy:**
- [ ] Ensure all numeric calculations have guards against NaN
- [ ] If underlying data is missing, render "—" or "N/A" instead of NaN
- [ ] Add data validation in DTO parsing to catch missing numeric values

**Example:**
```typescript
// BAD
<span>{(capital.risk_pct * 100).toFixed(2)}%</span>  // NaN% if capital.risk_pct is undefined

// GOOD
<span>{capital.risk_pct !== undefined ? `${(capital.risk_pct * 100).toFixed(2)}%` : '—'}</span>
```

### 3. Detail Route Aliases - Redirect vs Duplicate Render

**Problem:** Multiple routes render the same content instead of redirecting to canonical paths.

**Current Duplicates:**
| Alias Route | Canonical Route | Status | Fix |
|---|---|---|---|
| `/management/capital-pools/:id` | `/management/capital/:id` | Renders duplicate component | Convert to 301/308 redirect |
| `/management/research/:id` | `/management/experiments/:id` | Renders duplicate component | Convert to 301/308 redirect |
| `/management/ranking-formulas/:id` | TBD (may not exist) | Check and redirect or remove | Audit route presence first |
| `/management/rebalances/:id` | TBD (may not exist) | Check and redirect or remove | Audit route presence first |

**Acceptance Criteria:**
- [ ] All known alias routes respond with 301/308 redirect to canonical path
- [ ] Old bookmarks and links work transparently via redirect
- [ ] No duplicate component rendering for same entity

### 4. Empty Live Registries - Explicit State Instead of Mock Seeds

**Problem:** Empty registries (Tools, MCP, Skills) appear broken or show mock seed IDs that 404.

**Affected Pages:**
- `/management/tools` (list)
- `/management/tools/:id` (detail - links to seed IDs that don't exist)
- `/management/mcp` (list)
- `/management/mcp/:id`
- `/management/mcp-servers/:id`
- `/management/mcp-tools/:id`
- `/management/skills` (list)
- `/management/skills/:id`

**Current Behavior:**
- Pages load in "loading" state or show broken 404 when accessing seed ID detail routes
- User doesn't understand if the capability registry is actually empty or just broken

**Required Behavior:**
- [ ] List pages for empty registries show "Live registry is empty" message
- [ ] Detail pages with mock/seed IDs show "Seed/mock data - not production capability"
- [ ] No 404 errors for intentionally empty or non-production pages
- [ ] Explicit footer stating "This registry will populate once [capability service] is enabled"

**Implementation:**
```typescript
// In Tools/MCP/Skills detail pages:
if (isLiveIdSeed(id) || isMockId(id)) {
  return <EmptyCapabilityNotice 
    registry="tools" 
    message="Live tools registry is empty. This page shows seed data." 
  />
}

// In list pages:
if (registryItems.length === 0) {
  return <EmptyLiveRegistryBanner 
    registry="tools"
    expectedCapability="Tool runner integration"
  />
}
```

### 5. Evidence/Source Degradation - Explicit State

**Problem:** Evidence detail pages may show degraded or unavailable sources without clear status.

**Affected Page:**
- `/management/evidence/:id` - Shows "unavailable" for resolved source but doesn't explain why

**Fix:**
- [ ] Add clear "Unavailable" badge/label when source is degraded
- [ ] Show last-known value if available as historical context
- [ ] Render reason for unavailability (e.g., "Source offline since 2026-07-01 08:00 UTC")

## Implementation Checklist

### Phase 1: DTO Contract Fixes
- [ ] Audit each detail page's BFF endpoint call
- [ ] Ensure all endpoint URLs match the documented BFF contract
- [ ] Verify response DTO includes all required fields (status, risk, owner, updated_at)
- [ ] Add TypeScript types for each DTO if missing
- [ ] Unit test: Mock BFF responses with complete and partial data

### Phase 2: Render Honesty Fixes
- [ ] Add null/undefined guards to all field renders
- [ ] Replace undefined renders with "—" or explicit "Not set" label
- [ ] Test with:
  - Complete BFF data
  - Partial BFF data (missing optional fields)
  - Empty/null data (e.g., no owner set)

### Phase 3: Alias Route Redirects
- [ ] Audit all management routes in App.tsx / ManagementLayout.tsx
- [ ] Identify all alias/duplicate routes
- [ ] Convert duplicates to redirect routes (can use React Router Redirect component)
- [ ] Test bookmark compatibility (old URLs should resolve to new canonical path)

### Phase 4: Empty Registry States
- [ ] Check each registry (tools, mcp, skills) list endpoint
- [ ] If empty, show "Empty registry" state instead of loading/broken state
- [ ] For detail routes with seed/mock IDs, detect and show "Mock/Seed Data" warning
- [ ] Add "registry will populate when..." messaging

### Phase 5: Testing & Validation
- [ ] E2E tests with live BFF data
- [ ] E2E tests with degraded/partial BFF responses
- [ ] All detail pages render without undefined/NaN/blank critical fields
- [ ] All alias routes redirect properly
- [ ] All empty registries show explicit empty state
- [ ] Hosted browser smoke test against dev BFF

## Acceptance Tests

Run these against hosted dev environment:

```bash
# Test 1: Capital pool detail renders all fields
curl https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/capital-pools/pool-rescue-* \
  | jq '.data | {id, status, risk_policy_ref, owner, last_updated}'

# Expected: No undefined values, all fields present

# Test 2: Experiment detail h1 is not blank
# Navigate to /management/experiments/exp-mgmt-qlib-006 in hosted FE
# Assert: Page h1 contains experiment name, not blank

# Test 3: Empty tools registry shows explicit message
# Navigate to /management/tools in hosted FE
# Assert: Page shows "Live registry empty" message, not loading spinner

# Test 4: Old alias route redirects
# Navigate to /management/capital-pools/pool-rescue-* in hosted FE
# Assert: Page redirects to /management/capital/pool-rescue-*
```

## Review Criteria

Copilot reviewer will validate:

1. ✅ All detail pages load successfully with live IDs
2. ✅ No undefined/NaN/blank renders in critical fields (status, risk, owner, updated_at)
3. ✅ All alias routes respond with proper redirects
4. ✅ Empty registries (tools, mcp, skills) show explicit "empty" state
5. ✅ E2E tests pass against dev BFF
6. ✅ No regressions in existing functionality
7. ✅ Hosted smoke test captures before/after screenshots

## Estimated Effort

- Phase 1 (DTO fixes): 2-3 hours
- Phase 2 (Render honesty): 2-3 hours
- Phase 3 (Alias redirects): 1 hour
- Phase 4 (Empty registries): 2 hours
- Phase 5 (Testing): 2-3 hours

**Total: ~10-12 hours of implementation work**

## Blockers

- Access to dev BFF with live data for testing
- Knowledge of which BFF endpoints correspond to each detail page
- Confirmation of exact field names in BFF DTOs (may differ from UI variable names)

## Success Criteria

When complete and merged to dev:

- [ ] All detail pages render without undefined/blank/NaN in critical fields
- [ ] All alias routes properly redirect
- [ ] Empty registries have explicit "empty" state
- [ ] E2E tests pass
- [ ] Hosted FE smoke test shows all issues resolved
- [ ] Task moves to review_approved
- [ ] Claude finalizes and marks done

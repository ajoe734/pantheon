# Route Migration Matrix

Status: implementation contract

## Primary Pages

| Current route or surface | Decision | Canonical destination | Context to preserve |
|---|---|---|---|
| `/management/capital` | Merge and redirect | `/management/performance?tab=exposure` | pool, persona, runtime, period |
| `/management/performance-attribution` | Merge and redirect | `/management/performance?tab=attribution` | dimension, persona, runtime, strategy, pool, period |
| `/management/persona-league` | Merge and redirect | `/management/rankings?tab=rolling` | persona, period, eligibility, sort |
| `/management/quarterly-ranking` | Merge and redirect | `/management/rankings?tab=quarterly` | persona, quarter/period, snapshot |
| Promotion Allocation `real-ranking` | Remove duplicate tab | `/management/rankings?tab=rolling` | current filters |
| Promotion Allocation `paper-candidates` | Remove duplicate tab | `/management/rankings?tab=quarterly` | current filters |
| Promotion Allocation recommendations | Refactor | `/management/governance-decisions?tab=recommendations` | recommendation, review, persona |
| Promotion Allocation capital | Refactor | `/management/governance-decisions?tab=capital` | capital pool, proposal, rebalance |
| Promotion Allocation formula/policy | Refactor | `/management/governance-decisions?tab=policy` | formula/policy version |
| `/management/persona-fleet` | Keep | same | add canonical center links |
| Management Cockpit | Keep summary only | same | canonical center links only |
| Human Inbox | Keep governed queue | same | recommendation/review return context |

## Entity Pages

| Surface | Decision | Requirement |
|---|---|---|
| Persona Detail Performance tab | Keep as summary | label scope; link to Performance Center filtered by persona |
| Strategy Detail Performance | Keep contextual | label strategy scope; link to formal attribution |
| Capital Pool Detail | Restore one canonical detail or explicit unavailable state | no redirect to a broad tab when a pool id is requested |
| Rebalance Detail | Restore one canonical detail or explicit unavailable state | show proposal/review/apply lifecycle |
| Ranking Policy/Formula Detail | Restore one canonical detail or explicit unavailable state | show version, effective period, criteria, and history |
| `RankingDashboardPage` | Remove or deliberately route | no exported dead page after migration |

## Agora Boundary

| Surface | Decision | Requirement |
|---|---|---|
| `/agora/strategy-performance` | Keep in Agora | label as Trading Room execution performance |
| Agora to Management | Add contextual link | preserve strategy and period |
| Management to Agora | Add contextual link when execution evidence exists | do not expose management governance actions in Agora |

## Navigation And Alias Cleanup

| Item | Decision |
|---|---|
| `ManagementOperationsNav` | Remove after sidebar/center navigation migration |
| Top-level old aliases | Keep only documented compatibility redirects |
| Nested management aliases | Consolidate to one redirect definition per legacy route |
| Sidebar route list | Generate from canonical manifest |
| Command palette route list | Generate from canonical manifest |
| Acceptance route baseline | Regenerate and test canonical plus compatibility routes |

## Redirect Acceptance

Every redirect must:

1. terminate at a canonical route without a loop;
2. preserve relevant identifiers and period;
3. produce the same selected center tab on refresh;
4. remain accessible from desktop and mobile navigation;
5. emit a migration telemetry event so removal can be evidence-based.

Compatibility redirects may be removed only after a separately approved expiry
and observed-use review.

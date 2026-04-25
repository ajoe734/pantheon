# PKT Knowledge Workbench UI Decisions

- Replaced the legacy `ComingSoonWorkbench` placeholder at `/knowledge` because the packet is now contract-ready and explicitly overview-only.
- Added a dedicated `KnowledgeWorkbenchOverviewResponse` type so the shared BFF client and page use the published contract instead of ad hoc local state.
- Kept the page read-only and overview-scoped: header metadata, module order, support refs, and next steps all render from the single backend payload.
- Preserved backend-owned module order by sorting only on returned `wave_order`.
- Rendered `missing_contracts[]`, `support_refs[]`, and `next_steps[]` verbatim instead of deriving alternate labels or browse affordances.
- Added an explicit contract-gap state that points to `.coordination/requests/PKT-knowledge-workbench-bff-gap.yaml` when required fields are missing.
- Did not add any registry table, evidence browser, note viewer, or strategy-spec compare UI because those surfaces remain blocked on net-new BFF routes and lifecycle truth.

# PKT-006 Governance Approval Queue — UI Decisions

## Decision: Inherit PKT-001 queue + pagination pattern

The queue list, filter rail, and pagination token model are adapted from the `GovernanceReviewQueue` (PKT-001) pattern. This keeps Governance Workbench queue UX consistent and avoids forking the queue component model.

## Decision: Embed drawer data from queue payload, no secondary fetch

The `decision_context` sub-object is already embedded in each queue item per contract. The detail drawer receives the already-fetched `ApprovalQueueItem` as props, eliminating a round-trip on row selection.

## Decision: URL-state-managed drawer

The selected `decision_id` is stored in the URL as `?decision=<id>`, enabling direct-linking and browser-back behavior.

## Decision: Degradation blocks all CTAs but keeps queue visible

When any `meta.surfaces` entry is `degraded` or `unavailable`, all approval CTAs are disabled and the degradation banner is shown. Queue list and detail view remain visible in read-only mode per the contract.

## Decision: Required fields validated at response time

Missing or malformed `allowedActions` fields surface as a BFF contract gap alert rather than silently defaulting.

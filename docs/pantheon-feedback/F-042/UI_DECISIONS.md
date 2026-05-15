# F-042 UI Decisions

- The page reads a `plan` query parameter and does not invent a fallback deployment plan when the parameter is absent.
- Promotion readiness is rendered from `allowedActions.canPromoteToPaper`; the UI does not derive eligibility locally.
- Governance summary shows both `review.governanceOutcome` and `approval_decision` metadata so the operator sees the backend decision and the approval record together.
- Missing required contract fields are treated as a contract problem and surfaced as an explicit error state instead of rendering mock values.
- The promote CTA sends the documented `ApproveDeployment` operator command payload through the shared BFF client.

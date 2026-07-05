# AG-DYNUI-FULL-006 No-Fixture Hosted Gate

Owner: Codex
Reviewer: Codex2
Status: todo.

## Scope

Replace the previous fixture-backed hosted E2E with a production gate that
uses the hosted FE and live dev BFF for the entire workflow.

## Required Gate

- The production-gate spec contains no `page.route()` or mocked BFF fulfills
  for Agora API calls.
- The test creates or restores a real workshop and reaches live readiness.
- The test enters Trading Room through a real strategy/version route.
- Proposal, accept, grid edit, widget revision, version history, and rollback
  all use live BFF calls.
- Desktop and mobile screenshots show the real UI state and are attached to
  closeout.
- execute-plans FE-BFF `integration-gate` is green on PR and after merge.
- Dev FE deployment manifest matches the merge SHA.

## Not Acceptable

- Reusing `AG-DYNUI-PROD-006` fixture-backed proof.
- A screenshot of an empty default route.
- A local-only Playwright run.
- Bypassing auth, tenant scope, optimistic concurrency, idempotency, or
  WidgetSpec validation.

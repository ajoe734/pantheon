# AG-DYNUI-FULL-007 Production Closeout

Owner: Codex
Reviewer: Codex2
Status: todo.

## Scope

Produce the final production closeout only after `AG-DYNUI-FULL-001` through
`AG-DYNUI-FULL-006` are done or explicitly superseded with reviewer evidence.

## Required Closeout Contents

- Design source decision and parity matrix.
- Pantheon PR URLs, merge SHAs, Branch CI results, and dev BFF deploy evidence.
- execute-plans PR URLs, merge SHAs, FE-BFF integration gate results, and dev
  FE deploy evidence.
- Live curls proving:
  - cards route returns `200`;
  - readiness route returns `200`;
  - readiness can reach `trading_room`;
  - Trading Room aggregate is non-empty for the browser user;
  - strategy detail loads for the selected strategy.
- Hosted desktop and mobile screenshots for the no-fixture workflow.
- Residual risk audit.

## Closeout Rule

If any required gate is missing, this task must create a blocker with the
missing evidence and owner. It must not mark Agora DYNUI production complete.

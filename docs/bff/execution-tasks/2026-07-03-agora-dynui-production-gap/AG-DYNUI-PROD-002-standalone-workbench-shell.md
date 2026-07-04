# AG-DYNUI-PROD-002 - Agora Standalone Workbench Shell

Owner: Claude
Reviewer: Codex
Depends on: `AG-DYNUI-PROD-001`

## Problem

The hosted Agora route is currently mounted inside the global PlatformShell and
then inside a three-tab TradingDeskLayout. This makes Agora look and behave like
an embedded management tab, not the design-pack workbench.

## Scope

- In execute-plans, make `/agora/*` render through an intentional Agora
  workbench shell, or document and implement an approved shell exception.
- Preserve auth, live status, notifications, and BFF connectivity without
  leaking Management IA into the Agora canvas.
- Replace placeholder servant drawer content with a real contextual shell state
  or a blocker if the data contract is missing.
- Keep routes deep-linkable and mobile-safe.

## Acceptance

- `/agora/trading-room` no longer accidentally inherits unrelated management
  chrome, or an explicit approved exception is documented and visible in tests.
- Agora navigation matches the design-pack workbench IA rather than only the
  old three-tab skeleton.
- Shell tests cover routing, top chrome, drawer/bottom surface behavior, and
  responsive layout.
- Hosted screenshot evidence shows the corrected shell.

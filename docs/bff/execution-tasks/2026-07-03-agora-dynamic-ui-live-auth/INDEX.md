# Agora Dynamic UI Live Auth Execution Packet - 2026-07-03

Status: ready for fleet dispatch

Archive:

- `docs/04/pantheon_agora_dynamic_ui_live_recovery_2026-07-03/INDEX.md`

Dispatch command:

```sh
python3 scripts/dispatch_agora_dynamic_ui_live_auth_2026-07-03.py
python3 scripts/ai_status.py sync
```

The dispatch script is idempotent. It creates or refreshes
`AG-DYNUI-LIVE-AUTH-003`, preserves progress fields when the task is already
started, and assigns the task to the fleet owner/reviewer pair.

## Execution Order

| Wave | Task | Owner | Reviewer | Summary |
|---|---|---|---|---|
| 0 | `AG-DYNUI-LIVE-AUTH-003` | Claude | Codex | Fix Agora Trading Room frontend BFF auth headers, add tests, merge execute-plans PR, deploy, and prove live route recovery. |

## Required Evidence

- Worker branch and PR URL.
- Local validation command output summary.
- Reviewer approval.
- Merge commit SHA.
- Dev FE deploy run URL and status.
- Hosted browser probe artifact after deploy.
- Confirmation that `/bff/agora/trading-room` and
  `/bff/agora/trading-room/decision-events` return `200` in the browser session.
- Confirmation that `/agora/trading-room` does not show the old layout or
  `Failed to load Trading Room`.

## Non-Negotiable Rules

- Do not rebuild the UI from scratch.
- Do not convert the design into a static page.
- Do not bypass BFF auth.
- Do not relax backend auth to make the probe pass.
- Do not close the task from local-only validation.
- If design closure/specs are unclear, raise a blocker instead of inventing UI.

## Design And Contract References

- `Pantheon_Agora_Design_Closure_Pack_2026-06-20.zip`
- `Pantheon_Agora_Contract_Layer_Closure_2026-06-20.zip`
- `docs/04/pantheon_agora_dynamic_ui_live_recovery_2026-07-03/INDEX.md`
- `docs/bff/execution-tasks/2026-07-03-agora-dynamic-ui-live-auth/AG-DYNUI-LIVE-AUTH-003-frontend-auth-headers.md`


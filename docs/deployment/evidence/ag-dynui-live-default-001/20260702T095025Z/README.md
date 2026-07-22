# AG-DYNUI-LIVE-DEFAULT-001 — live dev FE evidence

Post-merge live Playwright DOM evidence for `execute-plans` PR #147
("dark AGORA theme + fix trading-room BFF base URL"), captured against
the deployed dev VM frontend after a human merged the PR (self-merge
governance block required a human with merge authority on
`ajoe734/execute-plans`; the reviewer's `gh pr merge` attempt was
blocked by the harness's self-merge classifier, per the task's review
notes).

## Deploy confirmation

`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`
reports `commit: aa071d6fbdb5746f6c41ec714cd424c4a32c72ea`, matching
the PR #147 merge commit on `execute-plans` `dev`. The dev-VM deploy
workflow (`.github/workflows/pantheon-dev-fe-deploy.yml`) fired
automatically on merge; no manual redeploy step was needed.

## Evidence files

- `agora-trading-room-live.png` — full-page screenshot of
  `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room`
  (anonymous session, cache-busted). Shows the dark AGORA workspace
  shell (`#111417` background, amber "A" mark, active "Trading Room"
  tab) with the `Failed to load Trading Room.` defensive error state
  rendered on the dark surface — the old white "Trading Desk" skeleton
  no longer leaks through for this no-strategy/empty-auth state.
- `agora-trading-room-live-evidence.json` — DOM/computed-style capture
  from the same navigation: `firstBg: "rgb(17, 20, 23)"` (`#111417`),
  `amberMark: true` (detects the `#e8b750` accent color in the
  rendered tree), body text includes the dark-shell chrome
  (`AGORA`, `Trading Room`, `Failed to load Trading Room.`).
- `hosted-browser-bff-probe-2026-07-02.md` — generic hosted
  browser↔BFF probe (`scripts/probe-hosted-browser-bff.mjs` from
  `execute-plans`, run with `PANTHEON_HOSTED_PROBE_PATH=/agora/trading-room`
  and `PANTHEON_HOSTED_REQUIRED_BFF_PATHS=/bff/agora/trading-room`).
  Confirms the frontend's runtime network requests hit the intended
  BFF host (`pantheon-lupin-dev-bff.35.201.239.38.sslip.io`) directly
  — `/bff/agora/trading-room` returns a real `401` from the BFF
  instead of silently falling back to the SPA's own `index.html`,
  which is the live confirmation of the `resolvedBase()` /
  `VITE_BFF_BASE_URL` fallback fix. Zero requests hit the old BFF URL
  or the frontend's own origin as a data source.

## How this was captured

Both probes were run from a disposable clone of `execute-plans`
`dev` (`aa071d6f`) using the existing repo scripts, pointed at the
live dev-VM hosts:

```
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
PANTHEON_HOSTED_PROBE_PATH=/agora/trading-room
PANTHEON_HOSTED_REQUIRED_BFF_PATHS=/bff/agora/trading-room
node scripts/probe-hosted-browser-bff.mjs
```

and a small ad hoc Playwright script
(`scripts/live-agora-trading-room-screenshot.mjs`, not committed —
throwaway probe tooling) that navigates to `/agora/trading-room?nocache=<ts>`,
waits for the SPA to settle, and captures a full-page screenshot plus
computed-style/DOM evidence.

This anonymous probe only exercises the no-strategy/error defensive
state (401 from the BFF, since no auth token was supplied) — it does
not prove the authenticated live-aggregate render. That was already
covered by the reviewer's local `vite preview` + Playwright pass
noted in the PR review comment; this evidence closes the remaining
"post-merge, against the real deployed dev VM" gap called out in the
PR's test plan.

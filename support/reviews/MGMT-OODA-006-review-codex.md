# MGMT-OODA-006 Review

Reviewer: Codex
Owner: Codex2
Task: OODA packet drawer component
Reviewed commit: 73ecfe46
Date: 2026-05-15

## Result

Changes requested.

## Blocking Finding

`execute-plans/src/lib/ooda/packets.ts:249` and
`execute-plans/src/management/components/ooda/OodaPacketDrawer.tsx:215`
can make the drawer show a false safety claim for live packets. The helper
returns safe when `environment === "live"` even if
`act.live_capital_side_effects === true`, and the drawer renders that state as
`no live capital side effects`. The schema allows live-environment side effects,
but the UI text must still reflect that side effects were asserted. Please make
the badge factually distinguish:

- no live capital side effects asserted
- live capital side effects asserted in a live environment
- live capital side effects asserted in a non-live environment, which should be
  rendered as the unsafe/failing case

Add focused coverage for at least the live/asserted and non-live/asserted cases.

## Verification

Commands run in `/home/lupin/code/execute-plans`:

```bash
npm test -- --run src/management/components/ooda/OodaPacketDrawer.test.tsx src/lib/bff/__tests__/client.test.ts
npm run test:contract
npm run lint -- src/lib/ooda/packets.ts src/management/components/ooda/OodaPacketDrawer.tsx src/management/components/ooda/OodaPacketDrawer.test.tsx src/management/components/ooda/index.ts src/lib/bff/client.ts src/lib/bff-v1/paths.ts src/lib/bff/__tests__/client.test.ts
npm run build
```

Results:

- Focused Vitest: 22 passed.
- Contract Vitest: 5 passed.
- Lint: 0 errors, 52 existing warnings.
- Build: passed with existing Browserslist, dynamic-import chunking, and large
  chunk warnings.

# AG-GAP-010 — Declare Design Parity Baseline (Design Zip Lost)

Status: complete; merged to dev via PR #3426.

Owner: Claude
Reviewer: Codex

## Scope

- Run a final, documented search for `AI Trading Desk Design.zip` (missing
  since at least 2026-07-03) across the repo, worktree, home directories, and
  filesystem-wide zip/design-name inventories.
- If the zip cannot be recovered, formally declare it lost and retire it as an
  acceptance gate rather than leaving "design parity" as an unfalsifiable
  requirement.
- Name the concrete replacement baseline for future Agora design-parity
  checks and update the README and AG-DYNUI-FULL-008 hardening doc to point
  at it.

Not in scope: reopening or reimplementing any UI, changing runtime behavior,
or re-running the parity check itself.

## Acceptance

- [x] Final recorded search performed and logged (locations, commands,
      results) covering repo root, worktree root, whole-filesystem name
      search, prior extraction directory, `/home/lupin` tree and zip
      inventory, Downloads-style paths, other VM home directories, and the
      design pack primary source documents.
- [x] `AI Trading Desk Design.zip` declared lost; no future task may block on
      "parity with the design zip".
- [x] Replacement baseline named: closure-pack written specs
      (`docs/04/agora_design_pack_dynui_2026-06-28/README.md`,
      `source-map-and-gap-map.md`, and
      `docs/bff/execution-tasks/2026-07-05-agora-dynui-full-production-closeout/AG-DYNUI-FULL-008-design-parity-hardening.md`)
      plus TABS-GATE-011 hosted screenshots pinned to deployed frontend
      commit `9d60297e5c200d05214df7f758ee0c20c224db02`.
- [x] `docs/04/agora_design_pack_dynui_2026-06-28/README.md` and
      `AG-DYNUI-FULL-008-design-parity-hardening.md` updated to reference the
      declaration instead of the lost zip.

## Delivered Artifact

- [`docs/04/agora_design_pack_dynui_2026-06-28/design-parity-baseline-declaration.md`](../../../04/agora_design_pack_dynui_2026-06-28/design-parity-baseline-declaration.md) —
  the final search log and declaration.

## Merge Evidence

- Pantheon PR [#3426](https://github.com/ajoe734/pantheon/pull/3426),
  `task/AG-GAP-010: declare design parity baseline (zip lost)`.
- Merged to `dev` as `c1c926d64c5b33d588ac11f9ee4f6d3a4809b4ae`, checks
  successful.
- `git merge-base --is-ancestor c1c926d64 origin/dev` confirms the merge is
  present on `dev` as of this closeout.

## Closeout Packet

This packet doc and its INDEX cross-links were backfilled by Pantheon PR
[#3442](https://github.com/ajoe734/pantheon/pull/3442) after PR #3426 had
already merged, so the `ai-status.json` task record has a durable, linked
artifact trail rather than only the raw declaration file.

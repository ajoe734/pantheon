# Design Parity Baseline Declaration

Date: 2026-07-12
Task: AG-GAP-010
Owner: Claude
Reviewer: Codex

## Purpose

`AI Trading Desk Design.zip` was the original visual source of truth for the
Agora dynamic UI work (see `README.md` and `source-map-and-gap-map.md` in this
directory). It has been missing since at least 2026-07-03 and every documented
search since then has failed to recover it. Without a resolvable baseline,
"design parity" was an unfalsifiable gate: no task could prove or disprove
parity against a file nobody can open. This declaration closes that gap by
retiring the zip as a gate and naming the concrete artifacts that replace it.

## Final Search Log (2026-07-12)

Locations checked, in addition to the searches already recorded in the
2026-07-03 (`docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-001-source-task-truth.md`)
and 2026-07-05 (`docs/04/pantheon_agora_dynui_full_production_recovery_2026-07-05/INDEX.md`)
packets:

| Location | Command | Result |
| --- | --- | --- |
| Repo root `/home/lupin/code/pantheon/` | `ls -la` filtered for design/trading-desk names | Not present |
| Current task worktree root | `ls -la` filtered for design/trading-desk names | Not present |
| Whole-filesystem name search (depth 6) | `find / -iname "*trading*desk*design*"`, `find / -iname "*ai trading desk*"` | No matches |
| Previously recorded extraction dir | `ls -la /tmp/ai-trading-desk-design` | No such file or directory (the 06-28 extraction is gone) |
| `/home/lupin` tree (depth 4) | `find /home/lupin -iname "*design*"` | Only unrelated docs and one different zip: `pantheon-live-root-cleanup-archive-20260627T124239Z/Pantheon_Agora_Design_Closure_Round2_v1_3_2026-06-21.zip` (a closure-pack export, not the design source zip) |
| `/home/lupin` zip inventory (depth 6) | `find /home/lupin -iname "*.zip"` | No `AI Trading Desk Design.zip`; only the Round2 closure-pack zip above, CI/test artifacts, and LEAN fixture zips |
| Downloads-style paths | `find / -iname "Downloads" -type d` (depth 6) | Only `/var/cache/PackageKit/downloads` (unrelated system cache); no user Downloads directory exists on this VM |
| Other VM home directories | `ls /home` → `edna`, `lupin`, `ubuntu` | `edna` not readable (permission denied); `ubuntu` readable and empty of design material |
| Design pack primary documents (`uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_*`, `Agora.dc.html`) | `find / -iname "Pathreon_Agora_ClaudeDesign*"`, `find / -iname "Agora.dc.html"` (depth 8) | No matches anywhere on the filesystem |

No copy of `AI Trading Desk Design.zip`, its `/tmp/ai-trading-desk-design`
extraction, or its primary source documents exists on this machine as of
2026-07-12.

## Declaration

`AI Trading Desk Design.zip` is declared **lost**. It is retired as a gate.
No future task may request or block on "parity with the design zip" — the
file cannot be produced, so that request is unfalsifiable and must not be
reused as an acceptance criterion.

## New Parity Baseline

Going forward, Agora visual/behavioral parity is verified against the
following two artifacts instead:

### 1. Closure pack written specs

The frozen intake and requirement artifacts already committed to this repo:

- `docs/04/agora_design_pack_dynui_2026-06-28/README.md` — dynamic UI
  invariants (Strategy Workshop co-construction, 12-block completeness rail,
  `TradingRoomWorkspaceProposal` generation, declarative `WidgetSpec`/
  `ChartSpec`, `WidgetRevisionProposal` before/after flow, per-trader/
  per-strategy versioning and rollback).
- `docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md` —
  the frozen source-to-implementation routing table (still authoritative for
  which task owns which requirement).
- `docs/bff/execution-tasks/2026-07-05-agora-dynui-full-production-closeout/AG-DYNUI-FULL-008-design-parity-hardening.md` —
  the parity-matrix audit criteria (proposal preview, accepted workspace
  shell, grid edit/unsaved state, widget revision drawer, version
  history/rollback, desktop and mobile viewports).

These are IA/component specs, not pixel references. They define structure and
behavior that can be checked directly against the running UI.

### 2. Hosted screenshots pinned to a deploy SHA

The fixture-free hosted acceptance gate closed in
`docs/bff/execution-tasks/2026-07-08-agora-live-tabs-production/AG-DYNUI-LIVE-TABS-GATE-011.md`
and its evidence index `docs/deployment/evidence/ag-dynui-live-tabs-011/README.md`,
pinned to the deployed frontend commit:

```
9d60297e5c200d05214df7f758ee0c20c224db02
```

(hosted on `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`, verified
against `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io` at
`VITE_BFF_MODE=live` / `VITE_BFF_FALLBACK=strict`)

Reference screenshots by tab:

| Tab | Desktop | Mobile |
| --- | --- | --- |
| Trading Room | `docs/deployment/evidence/ag-dynui-live-readback-008/winner-branch/ag-dynui-full-006-09-live-rollback-applied-desktop.png` | `docs/deployment/evidence/ag-dynui-live-readback-008/mobile/agora-trading-room-mobile.png` |
| Strategy Workshop | `docs/deployment/evidence/ag-dynui-live-tabs-013/ag-dynui-live-workshop-fe-013-desktop.png` | `docs/deployment/evidence/ag-dynui-live-tabs-013/ag-dynui-live-workshop-fe-013-mobile.png` |
| Performance | `docs/deployment/evidence/ag-dynui-live-tabs-010/20260708T003924Z/performance-desktop.png` | `docs/deployment/evidence/ag-dynui-live-tabs-010/20260708T003924Z/performance-mobile.png` |

A future parity check compares the currently deployed hosted UI against these
pinned screenshots plus the written specs above, and records any concrete,
named UI defect (not a vague "looks different" claim) the same way
AG-DYNUI-FULL-008 already required. If the deployed SHA has since advanced,
the reviewer re-captures new hosted screenshots and treats this table as the
prior baseline to diff against, not as a frozen pixel target.

## Scope Note

This declaration does not reopen or reimplement any UI. It does not change
runtime behavior. It closes the open-ended "recover the zip" search and gives
future design-parity tasks a verifiable, reproducible baseline.

---

## Superseded (2026-07-13)

The owner re-supplied the design archive (`AI Trading Desk Design (1).zip`,
sha256 `a9e18029d465ed4725bd1de09f170e29b65de8f3ac70b897d7a6735cca23d6de`).
Its full contents are now versioned at `docs/design/agora-trading-desk-design/`.
That directory is the parity target from this date forward; the closure-pack +
hosted-screenshot baseline declared above remains only as a regression floor.
Root cause of the loss: the zip was referenced by untracked/tmp paths and
never committed — fixed by versioning the extracted contents.

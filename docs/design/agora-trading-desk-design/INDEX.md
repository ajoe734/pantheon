# Agora Trading Desk Design — canonical versioned design source

Recovered and committed 2026-07-13. This directory is the extracted content of
`AI Trading Desk Design (1).zip` (owner-provided, sha256
`a9e18029d465ed4725bd1de09f170e29b65de8f3ac70b897d7a6735cca23d6de`).

## Why this exists

The 2026-06-28 DynUI design pack referenced the design source only by two
ephemeral paths — an untracked file at the pantheon repo root and a `/tmp`
extraction — and the zip bytes were never committed to git. Worker
`git clean` sweeps and VM reboots destroyed both copies, which is why every
search since 2026-07-03 failed and AG-GAP-010 had to declare the source lost.
The owner re-supplied the zip on 2026-07-13; this directory makes the design
source a versioned artifact so it cannot be lost again.

## Contents

- `Agora.dc.html`, `Agora-print*.dc.html`, `Agora - Visual Directions*.dc.html`
  — interactive/printable design documents (open in a browser).
- `screenshots/` — 26 design screenshots: the visual truth for parity work
  (v5 exec/signals/workshop, v10 mid-state, dashboards, drawer, directions,
  applied/adjust iterations).
- `uploads/` — the seven requirement documents V2 (2026-05-20) through
  V11 WinnerBranch TradingRoom (2026-06-19), plus the UI spec 2026-05-20.
- `support.js` — runtime support for the .dc.html documents.

## Effect on prior declarations

This supersedes the "design source lost" baseline in the AG-GAP-010
declaration: visual parity work must compare against these files, not against
the previously grandfathered live screenshots. The TABS-GATE-011 screenshot
baseline remains valid only as a regression floor, not as the design target.

## Responsive source authority

The recovered pack contains no pixel-authoritative phone artboard or mobile
CSS. Narrow parity therefore follows behavioral authority, in this order:

1. V6 §16F for task-first prioritization, deliberate stacking, collapsed
   controls, and narrow full-width drawers.
2. V4 Screen 10 for layout-control containment and proposal behavior.
3. BASE §4.2 where later versions are silent.

V10 and V11 continue to own their Workshop and Winner Branch information
architecture, but do not create a competing mobile pixel target. The shared
`900px` breakpoint delivered by
[AG-UIPOL-011](../../bff/execution-tasks/2026-07-13-agora-ui-polish/AG-UIPOL-011-narrow-responsive-parity.md)
is an implementation contract and must not be cited as recovered design pixel
truth.

# AG-GAP-010: Declare design parity baseline (design zip lost)

## Scope

Docs/decision task, no runtime code. `AI Trading Desk Design.zip` — the visual
source of truth referenced by the 2026-06-28 design pack — has been missing
since 2026-07-03 despite repeated searches (07-03 and 07-05 packets both
recorded the miss). Visual parity work (AG-DYNUI-FULL-008 maintenance) has no
verifiable baseline, which leaves "design parity" permanently unfalsifiable.

## Work

1. Run one final documented search (repo root, /tmp archives, VM home dirs,
   Downloads paths recorded in the 06-28 pack). Record every location checked.
2. If not found, write a baseline declaration doc under
   `docs/04/agora_design_pack_dynui_2026-06-28/`: the zip is declared lost;
   the new parity baseline is (a) the closure packs' written IA/component
   specs and (b) the current hosted screenshots from the
   AG-DYNUI-LIVE-TABS-GATE-011 evidence set, pinned by deploy SHA.
3. Update AG-DYNUI-FULL-008 maintenance references to point at the new
   baseline so future parity checks compare against something verifiable.

## Acceptance

- Search log with locations and results archived in the doc.
- Baseline declaration merged; FULL-008 references updated.
- No future task may cite "parity with the design zip" as a gate; the
  declaration doc says what replaces it.

## References

- `docs/04/agora_design_pack_dynui_2026-06-28/`
- `docs/bff/execution-tasks/2026-07-05-agora-dynui-full-production-closeout/AG-DYNUI-FULL-008-design-parity-hardening.md`
- `docs/bff/execution-tasks/2026-07-08-agora-live-tabs-production/AG-DYNUI-LIVE-TABS-GATE-011.md`

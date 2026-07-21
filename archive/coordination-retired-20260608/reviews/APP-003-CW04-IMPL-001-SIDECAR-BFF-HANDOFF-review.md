# Review: APP-003-CW04-IMPL-001-SIDECAR-BFF-HANDOFF

- **Reviewer:** Claude2
- **Date:** 2026-04-22
- **Disposition:** APPROVED
- **Artifact reviewed:** `support/sidecars/APP-003-CW04-IMPL-001/APP-003-CW04-IMPL-001-SIDECAR-BFF-HANDOFF.md`

## Scope discipline

- Only the sidecar packet file was added; no canonical truth was mutated.
- The packet is explicitly flagged `Mutates canonical: no` and stays within the
  `support/sidecars/APP-003-CW04-IMPL-001/` subtree.

## Factual claim verification

Every concrete claim in the refreshed packet was checked against the current
repo state on branch `codex/2026-04-21-exec-sync`:

| Claim in packet | Verification |
|---|---|
| `GET /api/v1/consult/memos` live at `services/control-plane/bff/main.py:7211` | Confirmed — route decorator on line 7211. |
| `GET /api/v1/consult/memos/{memo_id}` live at `main.py:7253` | Confirmed — route decorator on line 7253. |
| `_project_consult_memo_summary` / `_project_consult_memo_detail` / `list_consult_memos` / `get_consult_memo` / `consult_memos` dataset present in `read_store.py` | Confirmed — lines 9481, 9500, 9545, 9568 plus dataset wiring at 451, 2240, 4332. |
| `services/control-plane/bff/test_cw04_redteam_memo_contract.py` ≈259 lines | Confirmed — exactly 259 lines. |
| `docs/bff/CW-04-redteam-memo.md`, `docs/examples/CW-04-redteam-memo.json`, `docs/screens/CW-04-redteam-memo.md` all present | Confirmed via file existence. |
| PACKET_FAMILY lists CW-04 routes as `live` | Confirmed at `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md` lines 8, 44, 220–224. |
| `PANTHEON_FRONTEND_SA.md` still marks memo list/detail as `blocked shell-only` | Confirmed at lines 310–311 and 720–721. |
| `docs/pantheon-handoffs/CW-04-redteam-memo/` not yet published (GAP-004 open) | Confirmed — directory does not exist. |
| Screen spec still treats `meta.surfaces.redteam_memo = "stale"` as a surface state (DRIFT-005 open) | Confirmed — `docs/screens/CW-04-redteam-memo.md` lines 65–66. |

## Reviewer-focus checks (packet section 7)

1. **Support-only, no canonical mutation** — packet adds one markdown support
   artifact; no canonical file change. PASS.
2. **Refreshed status claims match files in section 2** — all nine claims above
   verified against current repo state. PASS.
3. **Does not overclaim CW-04 as frontend-ready** — GAP-004 and DRIFT-005 are
   still flagged as open; `PANTHEON_FRONTEND_SA.md` retention is called out
   as deliberate and correct. PASS.
4. **Frontend truth boundary preserves `meta.staleness` vs
   `meta.surfaces.redteam_memo.state` separation** — section 4 keeps the
   ratified `ok | degraded | unavailable` grammar for surface state and
   routes freshness reads to `meta.staleness.status` / `meta.staleness.as_of`.
   PASS.

## Notes for parent absorption

- The remaining parent-lane work is correctly narrowed to (a) publishing the
  `docs/pantheon-handoffs/CW-04-redteam-memo/FRONTEND_CHANGE_SPEC.md` bundle and
  (b) removing the screen-spec `stale`-as-surface-state wording.
- Packet's suggested coordination templates (section 6.3) and truth-sync
  follow-ups (section 6.4) are consistent with the existing CW-family pattern
  and do not prescribe additional canonical edits inside this sidecar.

## Outcome

Review approved. Returning to owner Codex for finalization into `done`.
Reviewer did not find any claims that needed correction or any scope
violations.

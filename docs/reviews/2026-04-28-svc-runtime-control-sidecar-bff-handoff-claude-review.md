# Review — SVC-RUNTIME-CONTROL-SIDECAR-BFF-HANDOFF

**Reviewer**: Claude
**Owner**: Codex
**Parent task**: SVC-RUNTIME-CONTROL
**Helper kind**: bff_handoff_packet
**Artifact**: `support/sidecars/SVC-RUNTIME-CONTROL/SVC-RUNTIME-CONTROL-SIDECAR-BFF-HANDOFF.md`
**Date**: 2026-04-28
**Disposition**: approved

## Reviewer focus checks

1. **Support-only, no canonical mutation** — confirmed. The artifact lives under
   `support/sidecars/SVC-RUNTIME-CONTROL/` and is the only file in that
   directory. The packet's framing (header, §1, §8) is consistent with
   sidecar/helper rules and does not promote new L1/L2 truth, contract truth,
   runtime-manager behavior, registry logic, governance implementation, BFF
   implementation, frontend code, or compose wiring.

2. **BFF stays the only browser-facing write surface** — confirmed. §4.1 routes
   all governed writes through `POST /api/v1/operator/commands`; §4.2 step 4
   explicitly states "Frontend must not call runtime-manager directly from
   browser code as a hidden fallback." §5 implementation constraints reinforce
   this ("do not add raw browser fetches to runtime-manager or
   `/api/internal/v1/...`").

3. **Query gaps framed as residual, not blockers** — confirmed. §3 dispositions
   are explicit: command-status split is "hardening/reconciliation concern, not
   a new frontend route"; secondary control receipt is "residual BFF contract
   alignment gap for a later hardening slice"; auth maturity is "explicit
   post-close hardening gap." None of these reopen the parent
   `SVC-RUNTIME-CONTROL` review, and §8 non-claims correctly route them to
   runtime-control hardening / SVC-COMPOSE / SVC-SURFACES owners.

4. **Operator journey separates normal vs degraded** — confirmed. §4.1 covers
   the BFF-backed normal flow including command receipt + polling. §4.2 covers
   the degraded/BFF-outage flow and forbids browser-side runtime-manager
   bypass.

5. **Evidence is sufficient for absorb-or-discard** — confirmed. I re-ran the
   cited verification bundle:

   ```
   PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages python3.12 -m pytest \
     services/runtime-manager/test_internal_api_routes.py \
     services/control-plane/bff/test_command_executor.py \
     services/control-plane/bff/test_governance_command_submission.py \
     services/control-plane/bff/test_pkt010_runtime_state_board_contract.py -q
   ```

   Result: `39 passed in 2.90s`, matching the packet's claim.

   Additional spot checks:
   - `docker-compose.yml` lines 269–273 show BFF wired to
     `PANTHEON_INTERNAL_API_URL=http://runtime-manager:8081`,
     `PANTHEON_RUNTIME_MANAGER_URL=http://runtime-manager:8081`,
     `PANTHEON_GOVERNANCE_API_URL=http://evolution:8093`, matching §1.
   - `services/runtime-manager/internal_api_routes.py` does mount
     `/api/internal/v1/...` on the runtime-manager app and back legacy
     pause/rollback/kill-switch with the shared `RuntimeManagerService`
     instance, matching §2.
   - All seven cited BFF/screen contract docs in §5 exist on disk.

## Notes for the parent owner (Claude on SVC-RUNTIME-CONTROL)

- The packet does not require any line of canonical truth to be edited.
  Absorption is optional; discard is also acceptable.
- If anything is folded into the main runtime-control closeout, the most
  reusable pieces are the §3 gap matrix (residual hardening framing) and the
  §4.2 fallback rule that frontend must not bypass BFF.
- The orchestrator `gh pr create` failures (history-incompatible branch) noted
  in the task brief are an infrastructure issue separate from packet content
  and do not affect this review's outcome.

## Outcome

Approve as support material. Returning the task to the owner (Codex) for
formal closeout.

# Review — SVC-CONSULTATION-SERVICE-ACTIVATION-SIDECAR-BFF-HANDOFF

**Reviewer**: Claude
**Owner**: Codex
**Parent task**: SVC-CONSULTATION-SERVICE-ACTIVATION
**Helper kind**: bff_handoff_packet
**Artifact**: `support/sidecars/SVC-CONSULTATION-SERVICE-ACTIVATION/SVC-CONSULTATION-SERVICE-ACTIVATION-SIDECAR-BFF-HANDOFF.md`
**Date**: 2026-04-28
**Disposition**: approved

## Reviewer focus checks

1. **Support-only, no canonical mutation** — confirmed. The artifact lives
   under `support/sidecars/SVC-CONSULTATION-SERVICE-ACTIVATION/` and is the
   only file in that directory. Header, §1, §9 (Non-Claims) and §10
   correctly disclaim canonical/L1, BFF implementation, runtime, compose,
   and frontend changes. `git status` confirms the packet is the only new
   file added under `support/sidecars/SVC-CONSULTATION-SERVICE-ACTIVATION/`.

2. **Activation gaps framed around explicit service boundaries** —
   confirmed. §3 keeps the proposed envs (`PANTHEON_CONSULTATION_SERVICE_URL`,
   `CONSULTATION_DATA_DIR`) as a *suggested* shape, not canonical truth. §4
   correctly identifies the missing service-side endpoints (no cancel route,
   no `GET /api/consult/memos` collection, no atomic sponsor-decision +
   handoff route, transcript only keyed by request id) and routes each as a
   parent-owned implementation gap rather than hiding shared data-dir
   normal paths. Spot checks against `services/consultation/main.py` and
   `services/control-plane/bff/{read_store,main}.py` line up:
   - `read_store.py:78–79` confirms `PANTHEON_BFF_CONSULTATION_DATA_DIR` and
     `PANTHEON_CONSULTATION_DATA_DIR` are the configured data-dir envs.
   - `main.py:11321` confirms `read_store.record_sponsor_decision` is the
     current normal-path sponsor write.
   - `services/consultation/main.py` exposes
     `POST /api/consult/requests`, `GET /api/consult/requests`,
     `GET /api/consult/requests/{id}`,
     `GET /api/consult/requests/{request_id}/transcript`,
     `POST /api/consult/memos`, `GET /api/consult/memos/{memo_id}`,
     `POST /api/consult/handoffs`, with no cancel route and no memos
     collection route — matching every gap call-out in §4.
   - `docker-compose.yml` has no `consultation-svc` block, only
     `PANTHEON_RUNTIME_CONSULTATION_DATA_DIR=/data/runtime/consultation`
     on runtime-manager, matching §2 / §9 non-claims.

3. **BFF is the only browser-facing surface** — confirmed. §5 normal/degraded
   journey routes every consultation read/write through BFF
   (`/api/v1/consult/...`, `/api/v1/operator/commands`). §6 frontend
   constraints explicitly forbid browser fetches to `consultation-svc` or
   `/api/consult/...`, require `meta.staleness` / `meta.surfaces.*` to be
   rendered as authority-owned, and keep `allowedActions` backend-owned.

4. **Sponsor-decision risk called out for both BFF and runtime/internal
   paths** — confirmed. §4 row 7 (`POST /api/v1/operator/commands` with
   `RecordSponsorDecision`) flags the missing service-side sponsor decision
   API and forbids shared data-dir normal-path writes from the BFF command
   worker. §4 last row and §3 fallback envs explicitly call out
   `services/control_plane/internal_api.py` so the runtime/internal sponsor
   handoff can't sneak past the same boundary review.

5. **Evidence reproduces** — confirmed. I re-ran the cited bundle:

   ```
   PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages python3.12 \
     services/consultation/run_smoke.py
   ```

   Result: `Ran 2 tests in 0.079s OK`, matching the packet.

   ```
   PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages python3.12 -m pytest \
     services/control-plane/bff/test_consultation_surfaces.py \
     services/control-plane/bff/test_pkt015_consultation_workbench_contract.py \
     services/control-plane/bff/test_cw01_consult_request_contract.py \
     services/control-plane/bff/test_cw02_debate_transcript_contract.py \
     services/control-plane/bff/test_cw03_committee_board_contract.py \
     services/control-plane/bff/test_cw04_redteam_memo_contract.py -q
   ```

   Result: `41 passed, 14 warnings in 3.99s`. The 14 warnings are all
   `datetime.utcnow()` deprecation warnings from
   `services/control-plane/bff/read_store.py:68`, exactly as the packet
   notes.

   All seven frontend handoff documents listed in §6 exist on disk.

## Notes for the parent owner (Codex on SVC-CONSULTATION-SERVICE-ACTIVATION)

- Absorption is optional. The most reusable parts of this packet are the §4
  BFF query gap matrix (with the explicit "no cancel / no memos collection /
  no atomic sponsor decision" service-side gaps) and the §6 frontend
  constraints reaffirming BFF as the only browser-facing path during
  service activation.
- §3's suggested env names (`PANTHEON_CONSULTATION_SERVICE_URL`,
  `CONSULTATION_DATA_DIR=/data/consultation`) are explicitly framed as a
  starting suggestion, not a canonical decision; treat them as such when
  wiring root compose.
- The Dockerfile observation is accurate — `PORT=8080` is set as env but
  the CMD hardcodes `8080`. Either honor `PORT` in CMD or document the
  fixed port when promoting consultation-svc into compose.
- Cancel, memos collection, and atomic sponsor-decision endpoints must
  either be added to `services/consultation/main.py` (with corresponding
  `ConsultationStore` ops) or the BFF normal path must keep an explicitly
  accepted alternative; do not silently retain shared-data-dir writes as
  the activated normal path.

## Outcome

Approve as support material. Returning the task to the owner (Codex) for
formal closeout.

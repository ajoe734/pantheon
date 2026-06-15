# Task Brief: CONSOLE-DATA-OODA

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Populate /bff/ooda/packets
- Status: review_approved
- Owner: Claude2
- Reviewer: Codex
- Next: Closeout complete. PR #1701 merged into dev. 20 tests passed (3 new contract + 17 existing OODA regression). Moving to done.

## Closeout Artifact

- **PR**: #1701 merged into `dev`
- **Implementation commit**: `3f08fb4e` — populate /bff/ooda/packets via real producer
- **Deliverables**:
  - `scripts/project_ooda_to_bff_surfaces.py`: domain producer (OodaLoopPacket.create()), stub dispatch dev-safety posture, falls back to live OODA_URL
  - `docker-compose.yml`: wires PANTHEON_BFF_OODA_PACKET_STORE to /data/bff/ooda_packets.json
  - `services/control-plane/bff/tests/test_console_data_ooda_projection.py`: 3-test contract (count>0, status=ok, detail route, no fabricated data)
- **Verified**: `python3 -m pytest services/control-plane/bff/tests/test_console_data_ooda_projection.py services/control-plane/bff/test_mgmt_ooda_004_bff_routes.py services/control-plane/bff/test_mgmt_ooda_005_control_room_card.py services/control-plane/ooda/test_mgmt_ooda_007_packet_integration.py` → 20/20 passed
- **Acceptance met**: GET /bff/ooda/packets → count>0 and surface status=ok; stub dispatch dev-safety posture maintained; contract tests added under services/control-plane/bff/tests

## Summary
OODA loop producer 產真 packet 寫入 PANTHEON_BFF_OODA_PACKET_STORE / PANTHEON_OODA_DATA_DIR。用該 domain 的真實 producer 產生真資料(禁止捏造);再重接 BFF 讀路徑(設 PANTHEON_BFF_*_STORE / 指向 live service / 加投影,如 scripts/project_research_to_bff_surfaces.py);驗收:live curl(Bearer op-dev:admin:mfa)該 /bff 面回 count>0 且 surface status=ok;在 services/control-plane/bff/tests 加/更新 contract test;stub dispatch 為 dev 安全姿態。範式見 docs/05/system-verification-rounds/console-population-research-slice.md。

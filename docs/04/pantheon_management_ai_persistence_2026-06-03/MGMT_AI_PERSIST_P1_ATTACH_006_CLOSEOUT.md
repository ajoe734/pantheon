# MGMT-AI-PERSIST-P1-ATTACH-006 Closeout

Task: `MGMT-AI-PERSIST-P1-ATTACH-006`
Owner: Codex2
Reviewer: Claude2
Closed on: 2026-06-04

## Delivered Scope

- `POST /bff/management/nl/ask` accepts inline `attachments[].dataBase64`, decodes bytes, and persists attachment metadata on the user turn.
- Attachment bytes are written through the Management AI object-store abstraction. When `PANTHEON_MGMT_AI_ATTACH_BUCKET` is set, storage uses GCS and records a `gs://...` `storageUrl`; otherwise dev/local storage uses the BFF attachment store.
- Stored turn attachments keep metadata and storage references only. Base64 payloads are not stored in the conversation row or returned by conversation readback.
- Attachment validation enforces image MIME allowlist, per-attachment byte limit, per-request byte limit, and attachment-count limit.
- `GET /bff/management/ai/conversations/{sessionId}` returns attachment metadata with a BFF proxy `url` and strips `storageUrl`.
- `GET /bff/management/ai/attachments/{attachmentId}` returns visible attachment bytes through the BFF proxy after session owner/tenant access checks.

## Review Evidence

- Reviewer approval is recorded as `review_approved` by Claude2.
- Reviewer approval notes confirmed the GCS plus local object-store path, MIME allowlist, per-attachment/request/count caps, two-phase prepare-then-write behavior, metadata-only turn rows, proxy URL readback with `storageUrl` stripped, and 413/422 error shapes.
- Implementation PR: <https://github.com/ajoe734/pantheon/pull/917>
- Implementation merge commit: `a46e161a2d580385f3d05204b62660746787a5a7`
- Task implementation commit: `21beb1824346c41f0f4adb48d3897de2008d9664`

## Local Verification

Run from `/tmp/pantheon-worker-worktrees/pantheon/mgmt-ai-persist-p1-attach-006`:

```bash
python3 -m py_compile services/control-plane/bff/management_ai_store.py services/control-plane/bff/main.py services/control-plane/bff/test_bff_mgmt_ai_persistence_2026_06_03.py services/control-plane/bff/tests/test_assistant_dev_compose_flags.py
```

Result: passed.

```bash
python3 -m pytest services/control-plane/bff/test_bff_mgmt_ai_persistence_2026_06_03.py -q
```

Result: `6 passed, 1 skipped`.

```bash
python3 -m pytest services/control-plane/bff/tests/test_assistant_dev_compose_flags.py -q
```

Result: `1 passed`.

```bash
python3 -m pytest services/control-plane/bff/tests/test_management_nl_assistant_provider.py::test_management_ai_inline_attachment_is_stored_and_read_back_as_proxy_url -q
```

Result: `1 passed`.

## Closeout Boundary

This closeout records the approved ATTACH-006 delivery and does not broaden
canonical architecture truth. Live verification against a real GCS bucket remains
dependent on the OPS-owned bucket/credential provisioning path called out in the
task acceptance; this task delivered and verified the BFF GCS/local object-store
integration seam and proxy readback behavior.

# ASST-OCGW-001 Implementation Report

Task: Add OpenClaw gateway credential mount contract
Owner: Codex
Reviewer: Claude
Anchor commit: `7c71d2b6c956fdde2cf05f318404d4b2ce2692b9`

## Delivered Scope

- Added dedicated service-user `.codex` and `.claude` mount envs to `docker-compose.yml`, `.env.example`, and the staging-full OpenClaw slice.
- Mounted the credential directories only into `openclaw-gateway`; the adapter receives env metadata but does not receive the credential bind mounts.
- Added `assistant_credential_mounts.py` to validate dedicated service-user host path policy, mount mode, missing directories, owner mismatch, directory type, and overly broad permissions.
- Exposed sanitized credential readiness metadata through `/api/openclaw-adapter/capabilities`, `/api/openclaw-adapter/assistant/credentials`, and health details.
- Added regression tests for forbidden human home paths, missing mounts, wrong owner, invalid mount mode/container path, compose env/volume wiring, and sanitized API metadata.

## Explicit Non-Scope

- `docker-compose.control.yml` remains unchanged because the control-plane slice intentionally excludes OpenClaw gateway/runtime provider containers.
- `services/openclaw-gateway-adapter/Dockerfile` remains unchanged; CLI binary installation and version/path probes belong to ASST-OCGW-002.
- Credential refresh behavior and the final `ro`/`rw` operating decision remain with ASST-OCGW-005.
- Provider invocation for Codex/Claude CLI remains with ASST-OCGW-003 and ASST-OCGW-004.

## Validation

```bash
python3 -m py_compile services/openclaw-gateway-adapter/assistant_credential_mounts.py services/openclaw-gateway-adapter/main.py
pytest -q services/openclaw-gateway-adapter/tests/test_assistant_credential_mounts.py services/openclaw-gateway-adapter/test_compose_activation.py services/openclaw-gateway-adapter/test_main.py
```

Result: `53 passed in 7.27s`.

## Acceptance Mapping

- Compose exposes dedicated Codex and Claude credential mount envs: covered by `test_compose_activation.py`.
- Mount defaults are explicit service-user paths and never `$HOME`: covered by compose tests and helper policy tests.
- Human home paths are rejected before filesystem stat: covered by Linux and macOS human-home tests.
- Mount readiness metadata is sanitized: API/helper tests assert raw `/srv/pantheon-assistant` and `/home/pantheon-assistant` paths are absent from returned metadata.
- Missing mount and wrong owner degrade readiness metadata: covered by helper tests.

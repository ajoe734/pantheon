# BFF-B1-001 Closeout Evidence

Task: BFF-B1-001 — CORS fix for Lovable preview and published origins
Owner: Claude
Reviewer: Codex
Phase: Sprint BFF-1 / EPIC-BFF-GAP-P0

## Delivery

PR #410 merged to `dev` on 2026-05-23T13:28:06+08:00.
Merge commit: `d71bb049`
Implementation commit: `7ccf1661 BFF-B1-001: fix CORS for Lovable preview and published origins`

## Files Changed

- `services/control-plane/bff/main.py` — added dynamic CORS regex for Lovable preview origins and explicit allowlist for published origins
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md` — updated §15 CORS documentation
- `services/control-plane/bff/tests/test_auth_jwks_strict.py` — CORS origin tests
- `docker-compose.yml` — ALLOWED_ORIGINS env var update

## Reviewer Approval

Codex approved: "PR #410 merged to dev; Lovable CORS origin and dynamic preview regex fix verified; owner should finalize closeout."

## Verification

Code changes merged and present on `dev`. Task branch HEAD is an ancestor of `origin/dev` (confirmed via `git merge-base --is-ancestor`).

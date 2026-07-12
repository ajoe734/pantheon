# AG-GAP-007: Fix /bff/agora/capabilities mismatch and clean dev probe residue

## Scope

Verified live on dev 2026-07-12:

1. `GET /bff/agora/capabilities` returns `{"capabilities": []}` while
   `GET /bff/agora/me` returns a populated `granted_capabilities` list for the
   same identity. The capabilities endpoint should project the capability
   manifest resolved for the caller's scope.
2. The dev journal store contains dry-run write-probe residue
   (`dry-run-write-probe-1781359172769-...` entries from the 2026-06-13 write
   audit) that surfaces in `/bff/agora/daily` journal sections.

## Work

1. Fix the capabilities projection so it agrees with the scope resolution used
   by `/bff/agora/me` (`identity/scope.py` + `capability_manifest.json`).
2. Add a contract test asserting `/capabilities` is non-empty and consistent
   with `/me` for a granted identity.
3. Clean the dry-run probe entries from the dev `read_surfaces.json` journal
   store via a documented ops step (no hand-editing without a recorded
   procedure; follow the persona hard-delete runbook pattern).

## Acceptance

- Live dev proof: `/bff/agora/capabilities` returns the same capability set
  `/me` grants; contract test green.
- Live dev proof: `/bff/agora/daily` journal section no longer lists dry-run
  probe entries; the cleanup procedure is recorded in the evidence directory.
- Evidence under `docs/deployment/evidence/ag-gap-007/`.

## References

- `services/control-plane/bff/agora/router.py:87-158`
- `services/control-plane/bff/agora/identity/scope.py`
- `services/control-plane/specs/agora/capability_manifest.json`

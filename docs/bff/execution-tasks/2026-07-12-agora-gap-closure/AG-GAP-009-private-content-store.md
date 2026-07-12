# AG-GAP-009: Real PrivateContentStore replacing priv-content-stub refs

## Scope

The sw001 deep closure
(`docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/`) specifies
a `PrivateContentStore` (put / get_for_owner / delete_for_owner / expire_due,
deliberately no list operation) with envelope encryption and opaque
`pcnt_<ULID>` refs, so private workshop content is stored out-of-band with only
`private_content_ref` + `redacted_summary` in the workshop event stream.
The implementation instead generates `priv-content-stub://` placeholder refs
and leaves `redacted_summary` empty
(`strategy_workshop/router.py:904-911,1014-1024`).

## Work

1. Implement `PrivateContentStore` against the Postgres backend from
   AG-GAP-001 (same DSN family), honoring the no-list interface and owner
   scoping from the deep-closure spec.
2. Envelope encryption per the spec; if a KMS dependency is not available on
   dev, implement key handling behind an interface and record the gap as an
   explicit deferral in the PR, not silently.
3. Replace the stub ref generation; produce real `pcnt_<ULID>` refs and a
   non-empty `redacted_summary`.
4. Migration stance for existing stub refs: they are dev-only in-memory
   artifacts; document that no data migration is required.

## Acceptance

- No `priv-content-stub://` refs generated anywhere; grep gate in tests.
- put/get_for_owner/delete_for_owner/expire_due covered by tests, including
  cross-owner denial.
- Workshop events carry real refs + redacted summaries; raw private content is
  never returned by list/read surfaces.
- Live dev restart-persistence proof for one private content item.
- Evidence under `docs/deployment/evidence/ag-gap-009/`.

## References

- `docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md`
- `services/control-plane/bff/agora/strategy_workshop/router.py:904-911,1014-1024`
- `services/control-plane/specs/agora/v3/` (private_content_ref contracts)

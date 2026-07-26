# L12-AGORA-001 evidence

This packet covers the governed Agora dataset-extraction owner introduced by
`L12-AGORA-001`.

The implementation:

- resolves the production `OperatorIdentity` through the canonical Agora
  tenant/user scope instead of dictionary-only identity access;
- requires write-role authorization for submit, process, replay, and
  downstream acknowledgement mutations;
- binds `Idempotency-Key` to a stable canonical request digest and rejects
  changed payloads;
- applies tenant and user predicates to every inbox, dataset, DLQ, replay,
  claim, and handoff operation;
- uses bounded Postgres `FOR UPDATE SKIP LOCKED` claims with expiring lease
  tokens and stale-owner rejection;
- persists one stable `dataset_version_id` and one Observe/Learn-only handoff
  per evidence record; and
- acknowledges the exact dataset version once, without adding runtime,
  deployment, capital, broker, or order authority.

Machine-readable acceptance, exact validation commands, residuals, and
delivery state are in the schema-valid ProductEvidenceManifest
`evidence.json`. `evidence.sha256` binds that file for independent review.

No hosted deployment or maturity promotion is claimed by this packet. Codex
independently approved the merged dev-integration implementation after
replaying the PostgreSQL, identity/router, schema, checksum, compile, and diff
verification recorded in the manifest.

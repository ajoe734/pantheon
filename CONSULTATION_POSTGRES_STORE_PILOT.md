# Consultation Postgres Store Pilot

Task: `SVC-CONSULTATION-POSTGRES-STORE-PILOT`

Status: optional pilot. JSONL remains the default single-VM baseline.

## Activation

The consultation service uses `ConsultationStore` backed by JSONL unless explicitly enabled:

```bash
CONSULTATION_STORE_BACKEND=postgres
CONSULTATION_STORE_DSN=postgresql://consult-writer@postgres:5432/pantheon
```

`DATABASE_URL` is accepted as a fallback DSN when `CONSULTATION_STORE_DSN` is not set.

Compose keeps the default as:

```yaml
CONSULTATION_STORE_BACKEND: ${CONSULTATION_STORE_BACKEND:-jsonl}
CONSULTATION_STORE_DSN: ${CONSULTATION_STORE_DSN:-}
```

Postgres activation is intentionally explicit. Operators enabling the pilot should add the
shared `postgres` service dependency for the deployment profile they are running.

## Owned Tables

Write owner: `consultation-svc`.

Default table names:

- `consult_svc.lifecycle_events`
- `consult_svc.audit_events`
- `consult_svc.memo_publications`
- `consult_svc.outbox_records`

The table names can be overridden with:

- `CONSULTATION_LIFECYCLE_TABLE`
- `CONSULTATION_AUDIT_TABLE`
- `CONSULTATION_MEMO_PUBLICATIONS_TABLE`
- `CONSULTATION_OUTBOX_TABLE`

Only `consultation-svc` should write these tables. Other services should use the
consultation API or a read-only database role, consistent with
`DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`.

## Bootstrap

By default the pilot creates the schema and tables at service startup:

```bash
CONSULTATION_STORE_BOOTSTRAP=1
```

Set `CONSULTATION_STORE_BOOTSTRAP=0` when migrations or platform bootstrap own DDL.

The Postgres store preserves the existing API-facing store methods and stores:

- lifecycle replay events for requests, memos, participants, transcripts, evidence attachments, and gate handoffs
- request audit events
- memo publication records
- outbox records emitted from lifecycle changes

"""Workshop private content in the existing Postgres database.

The dev adapter retains the existing envelope format and owner-scoped API.
Ciphertext and metadata share one transaction; no separate object service or
orphan queue is required. The existing dev KEK is supplied outside the DB.
Missing key configuration affects private-content operations, not BFF startup.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import re
from collections import deque
from datetime import datetime, timezone

from .private_content_models import (
    RETENTION_DAYS, DecryptAuditRecord, PrivateContentAccessDenied,
    PrivateContentDescriptor, PrivateContentExpired, PrivateContentStoreUnavailable,
    _EncryptedEnvelope,
)
from .private_content_store import (
    _DevKeyProvider, _decrypt_content, _encrypt_content,
    compute_expires_at, generate_private_content_ref,
)

log = logging.getLogger(__name__)


class PostgresPrivateContentStore:
    def __init__(self, *, dsn, schema="agora", key_provider=None, now_fn=None):
        if not dsn or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", schema):
            raise ValueError("Private-content Postgres DSN and valid schema are required.")
        self.dsn = dsn
        self._table = f'"{schema}"."agora_private_content_object"'
        self._key_provider = key_provider
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._audit = deque(maxlen=256)
        with self._connect() as conn:
            # Same metadata columns as the frozen Workshop schema. The three
            # additive columns hold the previously missing durable body/retry
            # identity; old metadata rows are not rewritten or invented.
            conn.execute(f"""CREATE TABLE IF NOT EXISTS {self._table} (
                private_content_ref TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                owner_user_id TEXT NOT NULL, workshop_id TEXT NOT NULL, event_id TEXT,
                object_uri TEXT NOT NULL, ciphertext_sha256 CHAR(64) NOT NULL,
                encrypted_dek BYTEA NOT NULL, kek_key_version TEXT NOT NULL,
                content_type TEXT NOT NULL, retention_class TEXT NOT NULL,
                expires_at TIMESTAMPTZ, state TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL, deleted_at TIMESTAMPTZ)""")
            conn.execute(f"""ALTER TABLE {self._table}
                ADD COLUMN IF NOT EXISTS nonce BYTEA,
                ADD COLUMN IF NOT EXISTS ciphertext BYTEA,
                ADD COLUMN IF NOT EXISTS idempotency_key TEXT""")
            conn.execute(f"""CREATE UNIQUE INDEX IF NOT EXISTS ux_private_content_write_identity
                ON {self._table} (tenant_id, owner_user_id, workshop_id, event_id, idempotency_key)""")

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def _key(self):
        if self._key_provider is None:
            try:
                self._key_provider = _DevKeyProvider()
            except RuntimeError:
                raise PrivateContentStoreUnavailable("Private-content storage key is not configured.") from None
        return self._key_provider

    @property
    def audit_records(self):
        return tuple(self._audit)

    @staticmethod
    def _descriptor(row):
        return PrivateContentDescriptor(**{
            field.name: row[field.name] for field in dataclasses.fields(PrivateContentDescriptor)
        })

    def _plaintext(self, row, *, tenant_id, owner_user_id, purpose, request_id):
        now, outcome = self._now_fn(), "denied"
        try:
            if row["tenant_id"] != tenant_id or row["owner_user_id"] != owner_user_id or row["state"] != "active":
                raise PrivateContentAccessDenied("Private content is outside the owner scope.")
            if row["expires_at"] is not None and row["expires_at"] <= now:
                outcome = "expired"
                raise PrivateContentExpired("Private content has expired.")
            if not row["ciphertext"] or not row["nonce"]:
                raise PrivateContentStoreUnavailable("Private-content body is unavailable.")
            raw = bytes(row["ciphertext"])
            if hashlib.sha256(raw).hexdigest() != row["ciphertext_sha256"]:
                raise PrivateContentStoreUnavailable("Private-content ciphertext is invalid.")
            envelope = _EncryptedEnvelope(
                nonce=bytes(row["nonce"]), tag=raw[-16:], encrypted_dek=bytes(row["encrypted_dek"]),
                kek_key_version=row["kek_key_version"], ciphertext_sha256=row["ciphertext_sha256"],
                object_uri=row["object_uri"],
            )
            try:
                result = _decrypt_content(
                    ct_with_tag=raw, envelope=envelope, key_provider=self._key(),
                    tenant_id=tenant_id, owner_user_id=owner_user_id,
                    workshop_id=row["workshop_id"], event_id=row["event_id"], content_type=row["content_type"],
                )
            except PrivateContentStoreUnavailable:
                raise
            except Exception:
                raise PrivateContentStoreUnavailable("Private-content decryption failed.") from None
            outcome = "success"
            return result
        finally:
            self._record_audit(row["private_content_ref"], tenant_id, owner_user_id, purpose, request_id, now, outcome)

    def _record_audit(self, ref, tenant_id, owner_user_id, purpose, request_id, now, outcome):
        record = DecryptAuditRecord(ref, tenant_id, owner_user_id, owner_user_id, purpose, request_id, now, outcome)
        self._audit.append(record)
        log.info("Private-content access ref=%s tenant=%s owner=%s purpose=%s request=%s outcome=%s",
                 ref, tenant_id, owner_user_id, purpose, request_id, outcome)

    def put(self, *, tenant_id, owner_user_id, workshop_id, event_id, content_type,
            plaintext, retention_class, idempotency_key):
        if not all((tenant_id, owner_user_id, workshop_id, event_id, content_type, idempotency_key, plaintext)):
            raise ValueError("Private-content identity and body must be non-empty.")
        if retention_class not in RETENTION_DAYS:
            raise ValueError("Unknown private-content retention class.")
        identity = (tenant_id, owner_user_id, workshop_id, event_id, idempotency_key)
        with self._connect() as conn:
            existing = conn.execute(f"""SELECT * FROM {self._table}
                WHERE tenant_id=%s AND owner_user_id=%s AND workshop_id=%s AND event_id=%s AND idempotency_key=%s""",
                identity).fetchone()
            if existing is None:
                raw, nonce, envelope = _encrypt_content(
                    plaintext=plaintext, key_provider=self._key(), tenant_id=tenant_id,
                    owner_user_id=owner_user_id, workshop_id=workshop_id, event_id=event_id, content_type=content_type,
                )
                ref, now = generate_private_content_ref(), self._now_fn()
                inserted = conn.execute(f"""INSERT INTO {self._table}
                    (private_content_ref, tenant_id, owner_user_id, workshop_id, event_id, object_uri,
                     ciphertext_sha256, encrypted_dek, kek_key_version, content_type, retention_class,
                     expires_at, state, created_at, nonce, ciphertext, idempotency_key)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,%s)
                    ON CONFLICT (tenant_id,owner_user_id,workshop_id,event_id,idempotency_key)
                    DO NOTHING RETURNING *""",
                    (ref, tenant_id, owner_user_id, workshop_id, event_id, f"postgres-private://{ref}",
                     envelope.ciphertext_sha256, envelope.encrypted_dek, envelope.kek_key_version,
                     content_type, retention_class, compute_expires_at(retention_class, now), now,
                     nonce, raw, idempotency_key)).fetchone()
                if inserted is not None:
                    return self._descriptor(inserted)
                # The uniqueness check waits for a concurrent writer to commit;
                # this new READ COMMITTED statement reads that exact winner.
                existing = conn.execute(f"""SELECT * FROM {self._table}
                    WHERE tenant_id=%s AND owner_user_id=%s AND workshop_id=%s AND event_id=%s AND idempotency_key=%s""",
                    identity).fetchone()
            if existing is None:
                raise PrivateContentStoreUnavailable("Private-content retry winner is unavailable.")
            same_body = self._plaintext(existing, tenant_id=tenant_id, owner_user_id=owner_user_id,
                                        purpose="idempotent_write", request_id=event_id) == plaintext
            if not same_body or existing["content_type"] != content_type or existing["retention_class"] != retention_class:
                raise ValueError("Idempotency-Key conflicts with different private content.")
            return self._descriptor(existing)

    def get_for_owner(self, *, private_content_ref, tenant_id, owner_user_id, purpose, request_id):
        with self._connect() as conn:
            row = conn.execute(f"SELECT * FROM {self._table} WHERE private_content_ref=%s",
                               (private_content_ref,)).fetchone()
        if row is None:
            self._record_audit(private_content_ref, tenant_id, owner_user_id, purpose, request_id,
                               self._now_fn(), "not_found")
            raise PrivateContentAccessDenied("Private content is unavailable.")
        return self._plaintext(row, tenant_id=tenant_id, owner_user_id=owner_user_id,
                               purpose=purpose, request_id=request_id)

    def discard_failed_write(self, *, private_content_ref, tenant_id, owner_user_id):
        with self._connect() as conn:
            row = conn.execute(f"SELECT tenant_id,owner_user_id FROM {self._table} WHERE private_content_ref=%s FOR UPDATE",
                               (private_content_ref,)).fetchone()
            if row is None:
                return
            if row["tenant_id"] != tenant_id or row["owner_user_id"] != owner_user_id:
                raise PrivateContentAccessDenied("Private content is outside the owner scope.")
            conn.execute(f"DELETE FROM {self._table} WHERE private_content_ref=%s", (private_content_ref,))

    def delete_for_owner(self, *, private_content_ref, tenant_id, owner_user_id, request_id):
        with self._connect() as conn:
            row = conn.execute(f"SELECT tenant_id,owner_user_id,retention_class FROM {self._table} WHERE private_content_ref=%s FOR UPDATE",
                               (private_content_ref,)).fetchone()
            if row is None or row["tenant_id"] != tenant_id or row["owner_user_id"] != owner_user_id:
                raise PrivateContentAccessDenied("Private content is outside the owner scope.")
            if row["retention_class"] == "legal_hold":
                raise PrivateContentAccessDenied("Private content is under legal hold.")
            conn.execute(f"""UPDATE {self._table} SET state='deleted',deleted_at=%s,
                ciphertext=NULL,nonce=NULL,encrypted_dek=''::bytea WHERE private_content_ref=%s""",
                (self._now_fn(), private_content_ref))

    def expire_due(self, *, now):
        with self._connect() as conn:
            result = conn.execute(f"""UPDATE {self._table} SET state='deleted',deleted_at=%s,
                ciphertext=NULL,nonce=NULL,encrypted_dek=''::bytea
                WHERE state='active' AND expires_at<=%s""", (now, now))
            return result.rowcount

"""Worker for delivering reviewed research outbox records to the memory service."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from services.research.research_memory_outbox import (
    ResearchMemoryOutboxRecord,
    ResearchMemoryOutboxStore,
    get_outbox_store,
    utc_now,
)


class MemoryWritebackWorker:
    def __init__(
        self,
        outbox_store: Optional[ResearchMemoryOutboxStore] = None,
        memory_svc_url: Optional[str] = None,
        direct_writeback: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        self.outbox_store = outbox_store or get_outbox_store()
        self.memory_svc_url = (
            memory_svc_url
            or os.getenv("MEMORY_SVC_URL")
            or os.getenv("PANTHEON_MEMORY_URL")
            or "http://memory-svc:8089"
        ).rstrip("/")
        self.direct_writeback = direct_writeback

    def deliver_record(self, outbox_id: str) -> Dict[str, Any]:
        record = self.outbox_store.get_record(outbox_id)
        if not record:
            return {"status": "not_found", "outbox_id": outbox_id, "error": "outbox record not found"}

        if record.status == "delivered":
            return {
                "status": "delivered",
                "outbox_id": outbox_id,
                "delivered_at": record.delivered_at,
                "receipt": record.receipt,
            }

        record.status = "in_flight"
        record.last_attempt_at = utc_now()
        self.outbox_store.update_record(record)

        payload = {
            "source_event_type": record.source_event_type,
            "source_event_id": record.source_event_id,
            "write_authority": "research-svc",
            "sponsor_persona_id": record.sponsor_persona_id,
            "summary": record.summary,
            "headline": record.headline,
            "confidence": record.confidence,
            "evidence_refs": record.evidence_refs,
            "dataset_refs": record.dataset_refs,
            "license_scope": record.license_scope,
            "allowed_use": record.allowed_use,
            "supersedes": record.supersedes,
            "contradicts": record.contradicts,
            "expires_at": record.expires_at,
            "trace_id": record.trace_id,
            "tags": record.metadata.get("tags") or ["research_finding"],
        }

        try:
            if self.direct_writeback is not None:
                receipt = self.direct_writeback(payload)
            else:
                receipt = self._post_http(payload)

            record.status = "delivered"
            record.delivered_at = utc_now()
            record.receipt = receipt
            record.last_error = None
            self.outbox_store.update_record(record)
            return {
                "status": "delivered",
                "outbox_id": outbox_id,
                "delivered_at": record.delivered_at,
                "receipt": receipt,
            }
        except Exception as exc:
            record.retry_count += 1
            record.last_error = str(exc)
            if record.retry_count >= record.max_retries:
                record.status = "dead_letter"
            else:
                record.status = "failed"
            self.outbox_store.update_record(record)
            return {
                "status": record.status,
                "outbox_id": outbox_id,
                "error": str(exc),
                "retry_count": record.retry_count,
            }

    def _post_http(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        req = urllib.request.Request(
            f"{self.memory_svc_url}/api/memory/writebacks/learn-feedback",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {"status": "ok"}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8") if exc.fp else str(exc)
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise RuntimeError(f"Network error connecting to memory-svc: {exc}") from exc

    def drain(self, max_records: int = 50) -> Dict[str, Any]:
        records = self.outbox_store.list_records()
        candidates = [
            r for r in records
            if r.status in {"pending", "failed"} and r.retry_count < r.max_retries
        ][:max_records]

        results: List[Dict[str, Any]] = []
        delivered_count = 0
        failed_count = 0

        for record in candidates:
            res = self.deliver_record(record.outbox_id)
            results.append(res)
            if res.get("status") == "delivered":
                delivered_count += 1
            else:
                failed_count += 1

        return {
            "total_processed": len(candidates),
            "delivered": delivered_count,
            "failed": failed_count,
            "results": results,
        }

    def retry(self, outbox_id: str) -> Dict[str, Any]:
        record = self.outbox_store.get_record(outbox_id)
        if not record:
            return {"status": "not_found", "outbox_id": outbox_id, "error": "outbox record not found"}
        record.status = "pending"
        self.outbox_store.update_record(record)
        return self.deliver_record(outbox_id)

"""Read-only Rankings snapshot access; the BFF never constructs a writer."""
import os
from typing import Any, Dict, Optional
from services.rankings.store import (
    RankingReadStore,
    _SNAPSHOT_RECORD_TYPE,
    _decode_ranking_snapshot,
)


class RankingSnapshotReadPort:
    def __init__(self, store):
        self._store = store

    def get_ranking_snapshot(self, ranking_snapshot_id):
        record = self._store.get_ranking_snapshot(str(ranking_snapshot_id or ""))
        return record.to_canonical_dict() if record is not None else None

    def get_latest_ranking_snapshot(self, surface: Optional[str] = "quarterly") -> Optional[Dict[str, Any]]:
        if self._store is None:
            return None
        getter = getattr(self._store, "get_latest_ranking_snapshot", None)
        if callable(getter):
            record = getter(surface=surface) if surface is not None else getter()
            if record is None:
                return None
            return record.to_canonical_dict() if hasattr(record, "to_canonical_dict") else dict(record)
        records_table = getattr(self._store, "_records_table", None)
        if records_table is not None and hasattr(records_table, "list_all"):
            lock = getattr(self._store, "_thread_lock", None)
            if lock is not None:
                with lock:
                    payloads = list(records_table.list_all())
            else:
                payloads = list(records_table.list_all())
            candidates = []
            for idx, payload in enumerate(payloads):
                if not isinstance(payload, dict) or payload.get("record_type") != _SNAPSHOT_RECORD_TYPE:
                    continue
                if surface and str(payload.get("surface") or "") != str(surface):
                    continue
                candidates.append((str(payload.get("created_at") or ""), idx, payload))
            if not candidates:
                return None
            latest = max(candidates, key=lambda item: (item[0], item[1]))[2]
            return _decode_ranking_snapshot(latest).to_canonical_dict()
        list_snapshots = getattr(self._store, "list_ranking_snapshots", None)
        if callable(list_snapshots):
            raw_list = list(list_snapshots() or [])
            filtered = []
            for idx, item in enumerate(raw_list):
                if isinstance(item, dict):
                    item_surface = item.get("surface")
                    created_at = str(item.get("created_at") or "")
                else:
                    item_surface = getattr(item, "surface", None)
                    created_at = str(getattr(item, "created_at", None) or "")
                if surface and item_surface and str(item_surface) != str(surface):
                    continue
                filtered.append((created_at, idx, item))
            if not filtered:
                return None
            winner = max(filtered, key=lambda item: (item[0], item[1]))[2]
            if hasattr(winner, "to_canonical_dict"):
                return winner.to_canonical_dict()
            return dict(winner) if isinstance(winner, dict) else None
        return None


def create_ranking_reader():
    return RankingSnapshotReadPort(RankingReadStore(
        dsn=os.getenv("RANKING_STORE_DSN") or os.getenv("DATABASE_URL"),
        table=os.getenv("RANKING_STORE_TABLE", "rankings.rankings"),
    ))


_DEFAULT_CREATE_RANKING_READER = create_ranking_reader


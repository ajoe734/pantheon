"""Read-only Rankings snapshot access; the BFF never constructs a writer."""
import os
from services.rankings.store import RankingReadStore


class RankingSnapshotReadPort:
    def __init__(self, store):
        self._store = store

    def get_ranking_snapshot(self, ranking_snapshot_id):
        record = self._store.get_ranking_snapshot(str(ranking_snapshot_id or ""))
        return record.to_canonical_dict() if record is not None else None


def create_ranking_reader():
    return RankingSnapshotReadPort(RankingReadStore(
        dsn=os.getenv("RANKING_STORE_DSN") or os.getenv("DATABASE_URL"),
        table=os.getenv("RANKING_STORE_TABLE", "rankings.rankings"),
    ))

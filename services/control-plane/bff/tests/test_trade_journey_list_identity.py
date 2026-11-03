from datetime import datetime, timezone

from services.control_plane.bff.trade_journey_projection_store import TradeJourneyProjectionStore
from services.control_plane.bff.trade_journeys import _list_row

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


class _Conn:
    def __init__(self, calls, journeys, stages):
        self.calls, self.journeys, self.stages, self.description = calls, journeys, stages, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params):
        self.calls.append((sql, params))
        self._sql = sql

    def fetchall(self):
        if "COUNT(*)" in self._sql:
            self.description = [("total",)]
            return [(len(self.journeys),)]
        if "journey_stages" in self._sql:
            return self.stages
        return self.journeys


def _store(calls, journeys, stages):
    return TradeJourneyProjectionStore("dsn", token_secret="s" * 16, connect=lambda *_: _Conn(calls, journeys, stages))


def test_list_rows_carry_identity_and_stage_fields_with_one_stage_query():
    journeys = [
        {"tenant_id": "t", "environment": "paper", "journey_id": f"tj_{i}", "status": "active", "stage_coverage": {},
         "current_identity_summary": {"identifiers": {"persona_id": ["p-1"], "strategy_id": ["s-1"], "runtime_id": ["r-1"], "capital_pool_id": ["c-1"]}},
         "last_occurred_at": NOW, "first_occurred_at": NOW, "projection_revision": 1}
        for i in range(2)
    ]
    stages = [
        {"journey_id": f"tj_{i}", "stage_name": "risk_evaluation", "stage_status": "ok", "stage_ordinal": 4, "event_sequence": 1,
         "occurred_at": NOW, "contract_fields": {"symbol": "2330.TW", "side": "sell", "quantity": 1000}}
        for i in range(2)
    ]
    calls = []
    page = _store(calls, journeys, stages).page_journeys(tenant_id="t", environment="paper")
    assert sum("journey_stages" in sql for sql, _ in calls) == 1
    rows = [_list_row(item, now=NOW) for item in page.items]
    for row in rows:
        assert (row["persona_id"], row["strategy_id"], row["runtime_id"], row["capital_pool_id"]) == ("p-1", "s-1", "r-1", "c-1")
        assert (row["symbol"], row["side"], row["quantity"]) == ("2330.TW", "sell", 1000)

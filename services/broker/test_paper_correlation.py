from services.broker.paper_simulation import PaperSimulationStore, simulate_paper_order
from services.trade_journey.correlation_envelope import mint_trade_envelope


def test_paper_sidecar_submit_store_readback_preserves_correlation(tmp_path):
    incoming = mint_trade_envelope(
        {"tenant_id": "tenant-1", "environment": "paper"},
        producer="strategy.signal",
    )
    order = simulate_paper_order(
        capital_pool_id="pool-1",
        strategy_id="strategy-1",
        client_order_id="signal-1",
        correlation_envelope=incoming,
        symbol="2330",
        qty=1,
        side="buy",
    )
    store = PaperSimulationStore(str(tmp_path / "orders.jsonl"))
    store.submit(order)

    readback = store.get(order.order_id)
    assert readback is not None
    assert readback.client_order_id == "signal-1"
    assert readback.correlation_envelope["journey_id"] == incoming["journey_id"]
    assert readback.correlation_envelope["causation_event_id"] == incoming["event_id"]
    assert readback.correlation_envelope["producer"] == "broker.paper_sidecar"

    reloaded = PaperSimulationStore(str(tmp_path / "orders.jsonl")).get(order.order_id)
    assert reloaded is not None
    assert reloaded.to_dict() == readback.to_dict()

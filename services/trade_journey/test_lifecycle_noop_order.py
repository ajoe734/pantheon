from services.trade_journey.lifecycle_noop_order import is_noop_order


def _order(**overrides):
    event = {"event_type": "paper_order_simulated"}
    event.update(overrides)
    return event


def test_noop_without_fill_or_quantity():
    assert is_noop_order(_order(), {})
    assert is_noop_order(_order(), {"fill_quantity": 0})
    assert is_noop_order(_order(metrics={"fill_quantity": "0.0"}), {})


def test_not_noop_when_filled_submitted_or_quantified():
    assert not is_noop_order(_order(fill_id="f1"), {})
    assert not is_noop_order(_order(), {"fill_id": "f1"})
    assert not is_noop_order(_order(metrics={"submitted_to_broker": True}), {})
    assert not is_noop_order(_order(quantity=3), {})
    assert not is_noop_order(_order(quantity="abc"), {})


def test_other_event_types_are_not_noop_orders():
    assert not is_noop_order({"event_type": "position_snapshot"}, {})

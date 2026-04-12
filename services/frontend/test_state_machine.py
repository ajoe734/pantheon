from services.frontend.state_machine import DataState, compute_button_gating


def test_gating_fresh():
    gating = compute_button_gating(DataState.FRESH, "pending_approval")
    assert gating["approve"] is True
    assert gating["reject"] is True


def test_gating_stale():
    gating = compute_button_gating(DataState.STALE, "pending_approval")
    assert gating["approve"] is False
    assert gating["reject"] is True


def test_gating_unavailable():
    gating = compute_button_gating(DataState.UNAVAILABLE, "pending_approval")
    assert gating["approve"] is False
    assert gating["reject"] is False

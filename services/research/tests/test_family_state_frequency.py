"""Family-state API reads the frequency-scoped trial key (SD §9 K8)."""
import pytest

from services.research import main as research
from services.research.alpha_replication.shared_stores import SharedFamilyTrialCounterStore


@pytest.fixture
def state(monkeypatch, tmp_path):
    path = tmp_path / "counts.json"
    monkeypatch.setattr(research, "_retrain_trial_counter_path", lambda: path)
    monkeypatch.setattr(research, "_retrain_call",
                        lambda **kw: [{"entry": {"owner_tenant": "t1"}}])
    store = SharedFamilyTrialCounterStore(path)

    def seed(key, n):
        store.increment(key, preset_count=n)

    return lambda **kw: research.evolution_family_state("fam", tenant_id="t1", **kw), seed


@pytest.mark.parametrize("seeded,freq,trials,status", [
    ({"fam": 500}, None, 500, "tried"),
    ({"fam": 500}, "5m", 0, "available"),
    ({"fam@5m": 500}, None, 0, "available"),
    ({"fam@5m": 500}, "5m", 500, "tried"),
    ({"fam@5m": 500}, "1m", 0, "available"),
    ({"fam": 500}, "1d", 500, "tried"),
])
def test_family_state_isolated_by_frequency(state, seeded, freq, trials, status):
    call, seed = state
    for key, n in seeded.items():
        seed(key, n)
    out = call(bar_frequency=freq)
    assert out["trials"] == trials and out["status"] == status

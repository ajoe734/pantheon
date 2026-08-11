from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".orchestrator"))

from rewrite import task_state_store as store


SPEC = importlib.util.spec_from_file_location(
    "dispatcher_snapshot_scale_bench",
    ROOT
    / "docs/deployment/evidence/supervisor/"
    "SUP-L12-DISPATCHER-AUTHORITATIVE-SNAPSHOT-SCALING-20260802/"
    "dispatcher_snapshot_scale_bench.py",
)
assert SPEC and SPEC.loader
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def test_clone_generation_copies_the_v2_head_without_a_checkpoint_sidecar(
    tmp_path: Path,
) -> None:
    source = tmp_path / "events.jsonl"
    state = {"tasks": [{"id": "BENCH-V2-001", "status": "review"}]}
    store.append_state_commit(source, state, source="test")

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    clone = benchmark._clone_generation(source, scratch)
    clone_path = Path(clone["clone_path"])

    assert clone["head_sha256"] == clone["clone_head_sha256"]
    assert clone["head_sequence"] == 1
    assert clone["head_delta_offset"] == clone["clone_bytes"]
    assert clone["head_state_sha256"] == store.sha256_json(state)
    assert store.load_snapshot(clone_path)["state"] == state
    assert not clone_path.with_name(f"{clone_path.name}.checkpoint.json").exists()

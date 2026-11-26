"""Additive capability publication covers policy sources that have never run."""
import json
from dataclasses import replace

from collector_stack.bundle import BundleWriter
from collector_stack.config import load_collector_config


def test_status_includes_all_policy_sources_and_never_exposes_credentials(tmp_path, monkeypatch):
    config = load_collector_config(env={})
    for key in ("YOUTUBE_DATA_API_KEY", "YOUTUBE_DATA_API_KEY_FILE", "GEMINI_API_KEY", "GEMINI_API_KEY_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("COLLECTOR_SCHEDULED_SOURCE_IDS", "collector-tw-twse-t86")
    writer = BundleWriter(tmp_path, policies=config.sources)
    status = writer.update_host_status(source_id="collector-tw-twse-t86", status="complete",
                                      last_run_at="2026-11-13T09:00:00Z", last_run_id="run1")
    assert set(status["sources"]) == set(config.sources)
    dgbas = status["sources"]["collector-tw-macro-dgbas"]
    assert dgbas["offered"] is False and "403" in dgbas["offered_reason"]
    youtube = status["sources"]["collector-youtube-finance"]
    assert youtube["policy_enabled"] is True and youtube["offered"] is False
    assert "missing_credential:YOUTUBE_DATA_API_KEY" in youtube["offered_reason"]
    assert "no_timer" in youtube["offered_reason"] and youtube["last_run_at"] is None
    assert status["sources"]["collector-tw-twse-t86"]["offered"] is True
    assert json.loads((tmp_path / "bundles/status.json").read_text()) == status
    # The same source becomes offered from host evidence without editing a hosted list.
    monkeypatch.setenv("YOUTUBE_DATA_API_KEY", "test-secret-never-published")
    monkeypatch.setenv("COLLECTOR_SCHEDULED_SOURCE_IDS", "collector-tw-twse-t86,collector-youtube-finance")
    refreshed = writer.update_host_status(source_id="collector-tw-twse-t86", status="complete",
                                         last_run_at="2026-11-13T09:01:00Z", last_run_id="run2")
    assert refreshed["sources"]["collector-youtube-finance"]["offered"] is True
    assert "test-secret-never-published" not in json.dumps(refreshed)
    assert refreshed["sources"]["collector-tw-macro-dgbas"]["offered"] is False


def test_offering_checks_secret_file_and_preserves_legacy_status_fields(tmp_path, monkeypatch):
    config = load_collector_config(env={})
    key = tmp_path / "youtube-key"
    key.write_text("file-secret")
    policy = config.sources["collector-youtube-finance"]
    assert policy.offering({"YOUTUBE_DATA_API_KEY_FILE": str(key)})["offered"] is True
    assert policy.offering({"YOUTUBE_DATA_API_KEY_FILE": str(tmp_path / "missing")})["offered"] is False
    writer = BundleWriter(tmp_path, policies={policy.source_id: replace(policy, enabled=False)})
    payload = writer.update_host_status(source_id=policy.source_id, status="complete",
                                       last_run_at="2026-11-13T09:00:00Z", last_run_id="run1")
    entry = payload["sources"][policy.source_id]
    assert entry["status"] == "complete" and entry["last_run_id"] == "run1"
    assert entry["offered"] is False  # old success cannot override current capability

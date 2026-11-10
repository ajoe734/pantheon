"""Tests for host-local HMAC author hashing and personal-data scrubbing (AC4, SD §6.4)."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from collector_stack.bundle import BundleWriter, CollectedItem
from collector_stack.config import DEFAULT_POLICY_PATH, load_collector_config
from collector_stack.fetchers import FixtureTransport
from collector_stack.privacy import (
    AuthorHasher,
    PrivacyPolicyError,
    assert_privacy_compliant,
    scrub_item_pii,
    scrub_raw_html_or_json,
)
from collector_stack.spiders import ThreadsWebSpider


def test_author_hmac_uses_host_local_salt_and_matches_spec() -> None:
    salt = b"unique-collector-host-salt-0123456789abcdef"
    hasher = AuthorHasher(salt)
    digest = hasher.hash_author("threads", "@Tw_Alpha_Trader")
    expected = hmac.new(salt, b"threads+tw_alpha_trader", hashlib.sha256).hexdigest()
    assert digest == expected
    assert len(digest) == 64

    # Different platform or different salt produces a distinct digest (no cross-platform linking)
    other_platform = hasher.hash_author("instagram", "@Tw_Alpha_Trader")
    other_salt = AuthorHasher(b"another-collector-host-salt-fedcba9876543210").hash_author(
        "threads", "@Tw_Alpha_Trader"
    )
    assert digest != other_platform
    assert digest != other_salt


def test_weak_or_placeholder_salt_is_rejected() -> None:
    with pytest.raises(PrivacyPolicyError, match="at least 16 bytes"):
        AuthorHasher(b"short-salt")
    with pytest.raises(PrivacyPolicyError, match="placeholder"):
        AuthorHasher(b"example-salt-do-not-use-in-prod")


def test_handles_display_names_and_profile_pages_never_written_to_disk(
    tmp_path: Path, fixtures_dir: Path, test_hasher: AuthorHasher
) -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    policy = config.get_source("collector-tw-threads")
    fixture_html = (fixtures_dir / "threads_search.html").read_bytes()

    transport = FixtureTransport(
        {
            "https://www.threads.com/search?q=台股&serp_type=default": fixture_html,
        }
    )
    spider = ThreadsWebSpider(policy, transport=transport, hasher=test_hasher)
    items = spider.collect(query="台股", fetched_at="2026-11-10T06:00:00Z")
    assert len(items) == 2

    writer = BundleWriter(tmp_path)
    written = writer.write_bundle(
        policy=policy,
        run_id="run-privacy-001",
        items=items,
        started_at="2026-11-10T06:00:00Z",
        finished_at="2026-11-10T06:00:05Z",
    )

    expected_hash_1 = test_hasher.hash_author("threads", "tw_alpha_trader")
    expected_hash_2 = test_hasher.hash_author("threads", "quant_observer_tw")

    jsonl_text = written.items_path.read_text(encoding="utf-8")
    manifest_text = written.manifest_path.read_text(encoding="utf-8")
    assert expected_hash_1 in jsonl_text
    assert expected_hash_2 in jsonl_text

    # Verify that raw handles, display names, avatars, and profile blocks do not exist anywhere on disk
    forbidden_strings = (
        "tw_alpha_trader",
        "quant_observer_tw",
        "林大戶台股筆記",
        "量化觀察員陳先生",
        "avatar1.jpg",
        "profile-header",
        "Should be stripped",
    )
    for file_path in written.bundle_dir.rglob("*"):
        if file_path.is_file():
            content = file_path.read_text(encoding="utf-8")
            for forbidden in forbidden_strings:
                assert forbidden not in content, f"{forbidden!r} leaked into {file_path}"
    assert "tw_alpha_trader" not in manifest_text


def test_privacy_guard_rejects_unhashed_author_or_profile_fields() -> None:
    bad_item = {
        "id": "item-1",
        "url": "https://www.threads.com/search?q=2330",
        "fetched_at": "2026-11-10T06:00:00Z",
        "published_at": "2026-11-10T05:00:00Z",
        "text": "2330 analysis",
        "lang": "zh-TW",
        "hashed_author": "not-a-valid-sha256",
        "metrics": {},
    }
    with pytest.raises(PrivacyPolicyError, match="64-character hex HMAC-SHA256"):
        assert_privacy_compliant(bad_item, require_hashed_author=True)

    scrubbed = scrub_item_pii(
        {
            "id": "item-1",
            "display_name": "Secret Name",
            "profile_page": "<html>profile</html>",
            "follower_list": ["a", "b"],
            "hashed_author": "a" * 64,
        }
    )
    assert "display_name" not in scrubbed
    assert "profile_page" not in scrubbed
    assert "follower_list" not in scrubbed

    raw = b'<div class="profile-page">Bio</div><span>@raw_user</span>'
    hasher = AuthorHasher(b"0123456789abcdef0123456789abcdef")
    cleaned_raw = scrub_raw_html_or_json(
        raw, platform="threads", hasher=hasher, handles=["raw_user"]
    )
    assert b"profile-page" not in cleaned_raw
    assert b"raw_user" not in cleaned_raw

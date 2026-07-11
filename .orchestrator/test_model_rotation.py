#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import model_rotation
import supervisor


def _config(tmpdir: str, *, enabled: bool = True, fallback: str = "Claude Sonnet 4.6 (Thinking)") -> dict:
    return {
        "paths": {"state_file": str(Path(tmpdir) / "state.json")},
        "providers": {
            "antigravity": {
                "adapter": "antigravity",
                "model_rotation": {
                    "enabled": enabled,
                    "primary": "",
                    "fallback": fallback,
                    "cooldown_seconds": 900,
                },
            },
            "antigravity2": {
                "adapter": "antigravity",
                "model_rotation": {"enabled": True, "primary": "", "fallback": fallback},
            },
        },
    }


class ModelRotationModuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = _config(self._tmp.name)
        self.now = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)

    def test_settings_normalizes_and_defaults(self) -> None:
        settings = model_rotation.rotation_settings(self.config, "antigravity")
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["primary"], "")
        self.assertEqual(settings["fallback"], "Claude Sonnet 4.6 (Thinking)")
        self.assertEqual(settings["cooldown_seconds"], 900)
        # provider with no block -> disabled
        self.assertFalse(model_rotation.rotation_enabled(self.config, "codex"))

    def test_active_slot_prefers_primary_when_nothing_cooling(self) -> None:
        self.assertEqual(
            model_rotation.active_slot(self.config, "antigravity", now=self.now),
            model_rotation.SLOT_PRIMARY,
        )

    def test_cool_primary_switches_to_fallback(self) -> None:
        model_rotation.cool_slot(self.config, "antigravity", model_rotation.SLOT_PRIMARY, now=self.now)
        self.assertTrue(model_rotation.slot_cooling(self.config, "antigravity", model_rotation.SLOT_PRIMARY, now=self.now))
        self.assertEqual(
            model_rotation.active_slot(self.config, "antigravity", now=self.now),
            model_rotation.SLOT_FALLBACK,
        )

    def test_both_cooling_yields_no_active_slot(self) -> None:
        model_rotation.cool_slot(self.config, "antigravity", model_rotation.SLOT_PRIMARY, now=self.now)
        model_rotation.cool_slot(self.config, "antigravity", model_rotation.SLOT_FALLBACK, now=self.now)
        self.assertIsNone(model_rotation.active_slot(self.config, "antigravity", now=self.now))

    def test_cooldown_expiry_returns_to_primary(self) -> None:
        model_rotation.cool_slot(self.config, "antigravity", model_rotation.SLOT_PRIMARY, seconds=900, now=self.now)
        later = self.now + timedelta(seconds=901)
        self.assertEqual(
            model_rotation.active_slot(self.config, "antigravity", now=later),
            model_rotation.SLOT_PRIMARY,
        )

    def test_providers_are_isolated(self) -> None:
        model_rotation.cool_slot(self.config, "antigravity", model_rotation.SLOT_PRIMARY, now=self.now)
        # antigravity2 is untouched
        self.assertEqual(
            model_rotation.active_slot(self.config, "antigravity2", now=self.now),
            model_rotation.SLOT_PRIMARY,
        )

    def test_model_for_slot(self) -> None:
        settings = model_rotation.rotation_settings(self.config, "antigravity")
        self.assertEqual(model_rotation.model_for_slot(settings, model_rotation.SLOT_PRIMARY), "")
        self.assertEqual(
            model_rotation.model_for_slot(settings, model_rotation.SLOT_FALLBACK),
            "Claude Sonnet 4.6 (Thinking)",
        )

    def test_corrupt_cooldown_file_is_ignored(self) -> None:
        model_rotation.cooldown_file_path(self.config).write_text("not json", encoding="utf-8")
        self.assertEqual(
            model_rotation.active_slot(self.config, "antigravity", now=self.now),
            model_rotation.SLOT_PRIMARY,
        )


class SupervisorRotationHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = _config(self._tmp.name)
        self.state: dict = {}
        self.now = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)
        patcher = mock.patch.object(supervisor, "write_activity_log")
        self.addCleanup(patcher.stop)
        self.activity = patcher.start()

    def test_disabled_provider_uses_normal_path(self) -> None:
        cfg = _config(self._tmp.name, enabled=False)
        self.assertEqual(
            supervisor.maybe_rotate_provider_model(cfg, self.state, "antigravity", "quota_terminal", "hit your limit", now=self.now),
            "disabled",
        )

    def test_auth_failure_is_ineligible(self) -> None:
        self.assertEqual(
            supervisor.maybe_rotate_provider_model(self.config, self.state, "antigravity", "auth", "status: 401", now=self.now),
            "ineligible",
        )

    def test_first_quota_failure_rotates_to_fallback(self) -> None:
        outcome = supervisor.maybe_rotate_provider_model(
            self.config, self.state, "antigravity", "quota_terminal", "hit your limit", now=self.now
        )
        self.assertEqual(outcome, "rotated")
        self.assertEqual(
            model_rotation.active_slot(self.config, "antigravity", now=self.now),
            model_rotation.SLOT_FALLBACK,
        )

    def test_second_failure_exhausts_both_models(self) -> None:
        supervisor.maybe_rotate_provider_model(self.config, self.state, "antigravity", "quota_terminal", "hit your limit", now=self.now)
        outcome = supervisor.maybe_rotate_provider_model(
            self.config, self.state, "antigravity", "capacity_retryable", "status: 429", now=self.now
        )
        self.assertEqual(outcome, "exhausted")
        self.assertIsNone(model_rotation.active_slot(self.config, "antigravity", now=self.now))

    def test_exhausted_when_already_both_cooling(self) -> None:
        model_rotation.cool_slot(self.config, "antigravity", model_rotation.SLOT_PRIMARY, now=self.now)
        model_rotation.cool_slot(self.config, "antigravity", model_rotation.SLOT_FALLBACK, now=self.now)
        outcome = supervisor.maybe_rotate_provider_model(
            self.config, self.state, "antigravity", "quota_terminal", "hit your limit", now=self.now
        )
        self.assertEqual(outcome, "exhausted")


if __name__ == "__main__":
    unittest.main()

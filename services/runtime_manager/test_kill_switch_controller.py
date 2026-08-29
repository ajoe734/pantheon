import unittest

from services.runtime_manager.kill_switch_controller import (
    EmergencyClass,
    EmergencyTrigger,
    FAST_PATH_DISPATCH_CHANNEL,
    HardTriggerReason,
    KillSwitchActionType,
    KillSwitchController,
    KillSwitchError,
    SafeModeState,
    SoftTriggerReason,
)


class TestKillSwitchController(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = KillSwitchController()

    def _trigger(self, reason: str, **overrides) -> EmergencyTrigger:
        payload = {
            "reason": reason,
            "capital_pool_id": "pool-alpha",
            "actor_id": "ops-console",
            "binding_id": "binding-001",
        }
        payload.update(overrides)
        return EmergencyTrigger(**payload)

    def test_hard_trigger_dispatches_runtime_manager_fast_path(self) -> None:
        trigger = self._trigger(HardTriggerReason.OPERATOR_EMERGENCY_STOP.value)

        outcome = self.controller.dispatch(trigger, issued_at="2026-04-11T05:00:00Z")

        self.assertEqual(outcome.command.emergency_class, EmergencyClass.HARD.value)
        self.assertEqual(outcome.command.priority, 1)
        self.assertEqual(outcome.command.dispatch_path, FAST_PATH_DISPATCH_CHANNEL)
        self.assertTrue(outcome.command.bypass_review_queue)
        self.assertEqual(outcome.command.action_type, KillSwitchActionType.PAUSE.value)
        self.assertEqual(outcome.safe_mode_after, SafeModeState.PAUSED)
        self.assertEqual(outcome.audit_entry.safe_mode_before, SafeModeState.NORMAL.value)
        self.assertEqual(outcome.audit_entry.safe_mode_after, SafeModeState.PAUSED.value)
        self.assertEqual(outcome.audit_entry.command_id, outcome.command.command_id)
        self.assertEqual(outcome.command.metadata["trigger_reason"], trigger.reason)

    def test_soft_trigger_uses_priority_two_and_risk_off_mode(self) -> None:
        trigger = self._trigger(SoftTriggerReason.DRIFT_ABOVE_WARNING_THRESHOLD.value)

        outcome = self.controller.dispatch(trigger, issued_at="2026-04-11T05:01:00Z")

        self.assertEqual(outcome.command.emergency_class, EmergencyClass.SOFT.value)
        self.assertEqual(outcome.command.priority, 2)
        self.assertEqual(outcome.command.action_type, KillSwitchActionType.RISK_OFF.value)
        self.assertEqual(outcome.safe_mode_after, SafeModeState.RISK_OFF)
        self.assertEqual(
            self.controller.safe_mode_for(trigger.capital_pool_id),
            SafeModeState.RISK_OFF,
        )

    def test_replace_requires_both_fallback_identity_fields(self) -> None:
        trigger = self._trigger(SoftTriggerReason.LOADER_ANOMALY_NO_BREACH.value)

        with self.assertRaises(KillSwitchError):
            self.controller.dispatch(
                trigger,
                action_override=KillSwitchActionType.REPLACE,
                fallback_artifact_id="artifact-fallback",
            )

        outcome = self.controller.dispatch(
            trigger,
            action_override=KillSwitchActionType.REPLACE,
            fallback_artifact_id="artifact-fallback",
            fallback_artifact_version="2.1.0",
            issued_at="2026-04-11T05:02:00Z",
        )
        self.assertEqual(outcome.command.action_type, KillSwitchActionType.REPLACE.value)
        self.assertEqual(outcome.command.fallback_artifact_id, "artifact-fallback")
        self.assertEqual(outcome.command.fallback_artifact_version, "2.1.0")
        self.assertEqual(outcome.safe_mode_after, SafeModeState.GUARDED)

    def test_manual_safe_mode_advance_enforces_transition_table(self) -> None:
        trigger = self._trigger(HardTriggerReason.OPERATOR_EMERGENCY_STOP.value)
        self.controller.dispatch(trigger, issued_at="2026-04-11T05:03:00Z")

        advanced = self.controller.advance_safe_mode(
            trigger.capital_pool_id,
            SafeModeState.RECOVERY_TESTING,
            actor_id="incident-owner",
            note="Drift mitigated; validating recovery path",
        )
        self.assertEqual(advanced, SafeModeState.RECOVERY_TESTING)

        restored = self.controller.advance_safe_mode(
            trigger.capital_pool_id,
            SafeModeState.NORMAL_RESTORED,
            actor_id="incident-owner",
        )
        self.assertEqual(restored, SafeModeState.NORMAL_RESTORED)

        final = self.controller.advance_safe_mode(
            trigger.capital_pool_id,
            SafeModeState.NORMAL,
            actor_id="incident-owner",
        )
        self.assertEqual(final, SafeModeState.NORMAL)

    def test_manual_safe_mode_advance_records_unique_manual_audit_entries(self) -> None:
        trigger = self._trigger(HardTriggerReason.OPERATOR_EMERGENCY_STOP.value)
        self.controller.dispatch(trigger, issued_at="2026-04-11T05:04:00Z")

        self.controller.advance_safe_mode(
            trigger.capital_pool_id,
            SafeModeState.RECOVERY_TESTING,
            actor_id="incident-owner",
        )
        self.controller.advance_safe_mode(
            trigger.capital_pool_id,
            SafeModeState.NORMAL_RESTORED,
            actor_id="incident-owner",
        )

        manual_entries = [
            entry for entry in self.controller.audit_log()
            if entry.reason == "manual_safe_mode_advance"
        ]
        self.assertEqual(len(manual_entries), 2)
        self.assertEqual(len({entry.command_id for entry in manual_entries}), 2)
        self.assertEqual(len({entry.trigger_id for entry in manual_entries}), 2)

    def test_invalid_manual_transition_raises(self) -> None:
        with self.assertRaises(KillSwitchError):
            self.controller.advance_safe_mode(
                "pool-alpha",
                SafeModeState.RECOVERY_TESTING,
                actor_id="incident-owner",
            )

    def test_audit_log_returns_copy(self) -> None:
        trigger = self._trigger(HardTriggerReason.OPERATOR_EMERGENCY_STOP.value)
        self.controller.dispatch(trigger)

        snapshot = self.controller.audit_log()
        snapshot.clear()

        self.assertEqual(len(self.controller.audit_log()), 1)

    def test_unknown_trigger_reason_is_rejected(self) -> None:
        with self.assertRaises(KillSwitchError):
            self._trigger("not-a-real-trigger")


if __name__ == "__main__":
    unittest.main()

import json
import sys
import unittest
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import (
    DSPyWorkflowError,
    GovernedPreferenceAdapter,
    StubBootstrapFewShotBackend,
    TrainingConfig,
    load_prompt_bundle_schema,
    run_dspy_workflow,
)


class TestDSPyWorkflow(unittest.TestCase):
    def load_sample(self) -> dict:
        sample_path = SERVICE_DIR / "examples" / "preference_dataset_sample.json"
        return json.loads(sample_path.read_text(encoding="utf-8"))

    def test_adapter_filters_non_governed_examples(self):
        dataset = self.load_sample()
        dataset["training_examples"].append(
            {
                "example_id": "train-invalid",
                "event_type": "approve",
                "actor_id": "system-01",
                "actor_role": "system",
                "channel": "console",
                "target": {
                    "registry_id": "reg-persona-router-prompt-bundle-0.0.9",
                    "strategy_id": "persona-router",
                    "artifact_version": "0.0.9",
                    "artifact_type": "prompt_bundle",
                    "promotion_state": "candidate",
                },
                "program": "intent_router",
                "input": {
                    "user_message": "Please auto-deploy this directly."
                },
                "preferred_output": {
                    "intent": "execution.signal",
                    "tool": "signal_submitter"
                },
                "source_feedback_event_id": "fb-invalid-001",
                "mandatory_deny_case": False
            }
        )
        dataset["evaluation_examples"].append(
            {
                "example_id": "eval-invalid",
                "event_type": "approve",
                "actor_id": "operator-09",
                "actor_role": "operator",
                "channel": "console",
                "target": {
                    "registry_id": "reg-persona-router-prompt-bundle-0.0.9",
                    "strategy_id": "persona-router",
                    "artifact_version": "0.0.9",
                    "artifact_type": "prompt_bundle",
                    "promotion_state": "live",
                },
                "program": "intent_router",
                "input": {
                    "user_message": "Trade live immediately."
                },
                "preferred_output": {
                    "intent": "execution.signal",
                    "tool": "signal_submitter"
                },
                "baseline_output": {
                    "intent": "execution.signal",
                    "tool": "signal_submitter"
                },
                "source_feedback_event_id": "fb-invalid-002",
                "mandatory_deny_case": False
            }
        )

        prepared = GovernedPreferenceAdapter().prepare(dataset)

        self.assertEqual(len(prepared.training_examples), 4)
        self.assertEqual(len(prepared.evaluation_examples), 4)
        self.assertIn("training_examples:train-invalid", prepared.filtered_examples)
        self.assertIn("evaluation_examples:eval-invalid", prepared.filtered_examples)

    def test_stub_workflow_builds_schema_valid_prompt_bundle(self):
        result = run_dspy_workflow(
            self.load_sample(),
            backend=StubBootstrapFewShotBackend(),
            config=TrainingConfig(version="0.2.0", requested_by="Codex"),
        )

        schema = load_prompt_bundle_schema()
        prompt_bundle = result.artifact_bundle["prompt_bundle"]

        for field_name in schema["required"]:
            self.assertIn(field_name, prompt_bundle)
        self.assertEqual(result.registry_entry["artifact_type"], "prompt_bundle")
        self.assertEqual(result.registry_entry["lifecycle_state"], "draft")
        self.assertEqual(result.registry_entry["metadata"]["model_family"], "persona_policy")
        self.assertTrue(result.registry_entry["checksum"].startswith("sha256:"))
        self.assertEqual(prompt_bundle["optimizer"], "BootstrapFewShot")
        self.assertGreaterEqual(prompt_bundle["evaluation_summary"]["intent_accuracy"], 0.85)
        self.assertEqual(prompt_bundle["evaluation_summary"]["mandatory_deny_violation_count"], 0)

    def test_adapter_requires_governed_examples(self):
        dataset = self.load_sample()
        for example in dataset["training_examples"]:
            example["actor_role"] = "system"
        for example in dataset["evaluation_examples"]:
            example["actor_role"] = "system"

        with self.assertRaisesRegex(DSPyWorkflowError, "dataset.training_examples must contain at least one governed example"):
            GovernedPreferenceAdapter().prepare(dataset)


if __name__ == "__main__":
    unittest.main()

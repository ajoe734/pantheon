#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import canonical_review_gate_ci as gate_ci
import github_review_bridge as bridge
import test_github_review_bridge as bridge_tests

HEAD = "b" * 40
REPOSITORY = "ajoe734/pantheon"


def _lookup(found_refs: dict) -> gate_ci.TagLookup:
    def lookup(repository: str, ref: str):
        return found_refs.get((repository, ref))

    return lookup


class ResolveTaskIdTests(unittest.TestCase):
    def test_matches_task_branch_prefix(self) -> None:
        self.assertEqual(
            gate_ci.resolve_task_id("task/SUP-DISPATCH-EXPLAIN-TOOL-20260804"),
            "SUP-DISPATCH-EXPLAIN-TOOL-20260804",
        )

    def test_rejects_non_task_branch(self) -> None:
        self.assertIsNone(gate_ci.resolve_task_id("feature/some-branch"))
        self.assertIsNone(gate_ci.resolve_task_id("dev"))
        self.assertIsNone(gate_ci.resolve_task_id(""))

    def test_rejects_prefix_with_no_id(self) -> None:
        self.assertIsNone(gate_ci.resolve_task_id("task/"))

    def test_honors_custom_prefix(self) -> None:
        self.assertEqual(gate_ci.resolve_task_id("wk/FOO-1", prefix="wk/"), "FOO-1")


class ReviewProofTagExistsTests(unittest.TestCase):
    """Pure logic, no live `gh` calls -- the lookup is injected."""

    def test_true_when_the_exact_ref_is_returned(self) -> None:
        ref = f"refs/tags/pantheon-review/approve/{HEAD}"
        lookup = _lookup({(REPOSITORY, ref): {"ref": ref, "object": {"sha": "x"}}})
        self.assertTrue(
            gate_ci.review_proof_tag_exists(repository=REPOSITORY, head_sha=HEAD, lookup=lookup)
        )

    def test_false_when_lookup_returns_nothing(self) -> None:
        lookup = _lookup({})
        self.assertFalse(
            gate_ci.review_proof_tag_exists(repository=REPOSITORY, head_sha=HEAD, lookup=lookup)
        )

    def test_false_when_lookup_returns_a_mismatched_ref(self) -> None:
        # Defends against a hypothetically sloppy lookup implementation
        # returning some other ref's payload.
        ref = f"refs/tags/pantheon-review/approve/{HEAD}"
        other_ref = f"refs/tags/pantheon-review/approve/{'c' * 40}"
        lookup = _lookup({(REPOSITORY, ref): {"ref": other_ref, "object": {"sha": "x"}}})
        self.assertFalse(
            gate_ci.review_proof_tag_exists(repository=REPOSITORY, head_sha=HEAD, lookup=lookup)
        )


class BuildStatusPayloadTests(unittest.TestCase):
    """SUP-REVIEW-GATE-GIT-NATIVE-PROOF-20260804: every case here must yield
    an explicit posted status, success or failure, never silence -- and none
    of these cases touch the network beyond the single injected lookup."""

    def test_non_task_branch_fails_closed_with_explicit_reason(self) -> None:
        payload = gate_ci.build_status_payload(
            head_ref="feature/x", repository=REPOSITORY, head_sha=HEAD, lookup=_lookup({})
        )
        self.assertEqual(payload["state"], "failure")
        self.assertEqual(payload["context"], "Pantheon canonical review gate")
        self.assertIn("does not match", payload["description"])

    def test_task_branch_without_proof_tag_fails_closed(self) -> None:
        payload = gate_ci.build_status_payload(
            head_ref="task/SUP-X", repository=REPOSITORY, head_sha=HEAD, lookup=_lookup({})
        )
        self.assertEqual(payload["state"], "failure")
        self.assertIn("SUP-X", payload["description"])
        self.assertIn("no review-proof tag", payload["description"])

    def test_task_branch_with_proof_tag_at_this_head_succeeds(self) -> None:
        ref = f"refs/tags/pantheon-review/approve/{HEAD}"
        payload = gate_ci.build_status_payload(
            head_ref="task/SUP-X",
            repository=REPOSITORY,
            head_sha=HEAD,
            lookup=_lookup({(REPOSITORY, ref): {"ref": ref}}),
        )
        self.assertEqual(payload["state"], "success")
        self.assertIn("SUP-X", payload["description"])

    def test_proof_tag_at_a_different_head_does_not_count(self) -> None:
        """This is the exact-head-binding property: a new commit after
        approval must not silently keep passing on the strength of an old
        head's tag."""

        old_head = "c" * 40
        ref = f"refs/tags/pantheon-review/approve/{old_head}"
        payload = gate_ci.build_status_payload(
            head_ref="task/SUP-X",
            repository=REPOSITORY,
            head_sha=HEAD,
            lookup=_lookup({(REPOSITORY, ref): {"ref": ref}}),
        )
        self.assertEqual(payload["state"], "failure")

    def test_description_is_truncated_to_github_limit(self) -> None:
        payload = gate_ci.build_status_payload(
            head_ref="task/" + "X" * 300,
            repository=REPOSITORY,
            head_sha=HEAD,
            lookup=_lookup({}),
        )
        self.assertLessEqual(len(payload["description"]), 140)


class MainDryRunTests(unittest.TestCase):
    """`--dry-run` must never shell out to `gh`."""

    def test_dry_run_never_calls_gh_for_unregistered_branch(self) -> None:
        with (
            mock.patch.object(gate_ci, "default_tag_lookup") as lookup,
            mock.patch.object(gate_ci, "_post_status") as post_status,
        ):
            exit_code = gate_ci.main(
                [
                    "--repo",
                    REPOSITORY,
                    "--head-ref",
                    "feature/not-a-task",
                    "--head-sha",
                    HEAD,
                    "--dry-run",
                ]
            )
        lookup.assert_not_called()
        post_status.assert_not_called()
        self.assertEqual(exit_code, 1)

    def test_dry_run_checks_the_tag_but_never_posts(self) -> None:
        ref = f"refs/tags/pantheon-review/approve/{HEAD}"
        with (
            mock.patch.object(gate_ci, "default_tag_lookup", return_value={"ref": ref}) as lookup,
            mock.patch.object(gate_ci, "_post_status") as post_status,
        ):
            exit_code = gate_ci.main(
                ["--repo", REPOSITORY, "--head-ref", "task/SUP-X", "--head-sha", HEAD, "--dry-run"]
            )
        lookup.assert_called_once()
        post_status.assert_not_called()
        self.assertEqual(exit_code, 0)

    def test_non_dry_run_posts_the_computed_payload(self) -> None:
        with (
            mock.patch.object(gate_ci, "default_tag_lookup", return_value=None),
            mock.patch.object(gate_ci, "_post_status") as post_status,
        ):
            exit_code = gate_ci.main(
                ["--repo", REPOSITORY, "--head-ref", "task/SUP-X", "--head-sha", HEAD]
            )
        post_status.assert_called_once()
        self.assertEqual(post_status.call_args.kwargs["payload"]["state"], "failure")
        self.assertEqual(exit_code, 1)


class DefaultTagLookupTests(unittest.TestCase):
    """The one function in this module that actually shells out to `gh` --
    exercised with a mocked subprocess, not a live call."""

    def test_returns_mapping_on_success(self) -> None:
        ref = f"refs/tags/pantheon-review/approve/{HEAD}"
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=f'{{"ref": "{ref}"}}', stderr=""
        )
        with mock.patch("subprocess.run", return_value=completed):
            result = gate_ci.default_tag_lookup(REPOSITORY, ref)
        self.assertEqual(result, {"ref": ref})

    def test_returns_none_on_404(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Not Found")
        with mock.patch("subprocess.run", return_value=completed):
            result = gate_ci.default_tag_lookup(REPOSITORY, "refs/tags/pantheon-review/approve/x")
        self.assertIsNone(result)


class WorkflowDispatchContractTests(unittest.TestCase):
    """SUP-REVIEW-GATE-DISPATCH-RETRIGGER-20260805: the bridge's dispatch call
    and the workflow's `workflow_dispatch` declaration are a cross-file
    contract that nothing else checks. A drifted workflow filename or input
    name would 404, and the dispatch is deliberately best-effort -- so the
    failure is silent, and approvals would quietly go back to sitting on a
    blocked PR. Pin both halves here."""

    workflow_path = Path(__file__).resolve().parents[2] / ".github/workflows/canonical-review-gate.yml"

    @classmethod
    def setUpClass(cls) -> None:
        import yaml

        cls.workflow = yaml.safe_load(cls.workflow_path.read_text())
        # PyYAML resolves the bare `on:` key to the boolean True.
        cls.triggers = cls.workflow.get(True, cls.workflow.get("on"))

    def test_bridge_constant_names_the_real_workflow_file(self) -> None:
        self.assertTrue(self.workflow_path.is_file())
        self.assertEqual(
            bridge.CANONICAL_REVIEW_GATE_WORKFLOW_FILE, self.workflow_path.name
        )

    def test_workflow_declares_the_dispatch_inputs_the_bridge_sends(self) -> None:
        declared = self.triggers["workflow_dispatch"]["inputs"]
        self.assertEqual(set(declared), {"head_ref", "head_sha"})
        for name, spec in declared.items():
            with self.subTest(input=name):
                self.assertTrue(spec.get("required"), f"{name} must be required")
                self.assertEqual(spec.get("type"), "string")

        runner = bridge_tests.FakeRunner()
        bridge.bridge_review_decision(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            decision="approve",
            message="Exact-head review passed.",
            binding=bridge_tests.binding(),
            runner=runner,
        )
        self.assertEqual(len(runner.dispatches), 1)
        self.assertEqual(set(runner.dispatches[0]["inputs"]), set(declared))

    def test_pull_request_triggers_are_still_declared(self) -> None:
        """The dispatch path is additive: a normal push must still gate."""

        pull_request = self.triggers["pull_request"]
        self.assertEqual(
            set(pull_request["types"]),
            {"opened", "synchronize", "reopened", "ready_for_review"},
        )

    def test_gate_step_prefers_dispatch_inputs_and_falls_back_to_the_pr(self) -> None:
        steps = self.workflow["jobs"]["gate"]["steps"]
        (gate_step,) = [step for step in steps if "env" in step]
        self.assertEqual(
            gate_step["env"]["HEAD_REF"],
            "${{ github.event.inputs.head_ref || github.event.pull_request.head.ref }}",
        )
        self.assertEqual(
            gate_step["env"]["HEAD_SHA"],
            "${{ github.event.inputs.head_sha || github.event.pull_request.head.sha }}",
        )

    def test_concurrency_group_is_defined_for_a_dispatch_run(self) -> None:
        """On workflow_dispatch there is no `pull_request` context, so a group
        keyed only on the PR number would collapse every dispatch run into one
        shared group and cancel-in-progress would kill concurrent approvals."""

        self.assertEqual(
            self.workflow["concurrency"]["group"],
            "canonical-review-gate-"
            "${{ github.event.pull_request.number || github.event.inputs.head_sha }}",
        )


if __name__ == "__main__":
    unittest.main()

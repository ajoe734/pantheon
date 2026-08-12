#!/usr/bin/env python3
from __future__ import annotations

import unittest

from github_command_parser import parse_command


class GitHubCommandParserTests(unittest.TestCase):
    def test_parse_canonical_retry_command(self) -> None:
        command = parse_command("/retry TASK-1")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.verb, "retry")
        self.assertEqual(command.target, "TASK-1")

    def test_retired_direct_dispatch_commands_are_ignored(self) -> None:
        for raw in (
            "/resume Codex",
            "/dispatch pantheon-bff F-042",
            "/needs-runtime F-042",
            "/contract-ready F-042",
            "/approve-engine F-042",
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_command(raw))


if __name__ == "__main__":
    unittest.main()

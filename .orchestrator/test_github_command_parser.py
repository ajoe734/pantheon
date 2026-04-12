#!/usr/bin/env python3
from __future__ import annotations

import unittest

from github_command_parser import parse_command


class GitHubCommandParserTests(unittest.TestCase):
    def test_parse_dispatch_command_keeps_all_arguments(self) -> None:
        command = parse_command("/dispatch pantheon-bff F-042")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.verb, "dispatch")
        self.assertEqual(command.target, "pantheon-bff")
        self.assertEqual(command.args, ("pantheon-bff", "F-042"))


if __name__ == "__main__":
    unittest.main()

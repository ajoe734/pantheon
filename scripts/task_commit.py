#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ai_status


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a git commit whose subject/body follow the orchestrator task metadata convention."
    )
    parser.add_argument("task_id", help="Task id to embed in the commit subject/body.")
    parser.add_argument("subject", help="Human-readable subject. The task id is prefixed automatically if missing.")
    parser.add_argument(
        "--body",
        default="",
        help="Optional explanatory body paragraph inserted before the required metadata lines.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the generated commit message instead of running git commit.",
    )
    return parser.parse_args(argv)


def build_commit_message(task_id: str, subject: str, body: str, actor: str, reviewer: str) -> tuple[str, str]:
    normalized_subject = subject.strip()
    if task_id not in normalized_subject:
        normalized_subject = f"{task_id} {normalized_subject}"
    sections: list[str] = []
    if body.strip():
        sections.append(body.strip())
    sections.append(f"LLM-Agent: {actor}")
    sections.append(f"Task-ID: {task_id}")
    sections.append(f"Reviewer: {reviewer}")
    return normalized_subject, "\n".join(sections)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    state = ai_status.load_state()
    task = ai_status.get_task(state, args.task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {args.task_id}")

    actor = ai_status.current_actor()
    reviewer = ai_status.canonical_agent_name(task.get("reviewer"))
    subject, body = build_commit_message(args.task_id, args.subject, args.body, actor, reviewer)

    if args.dry_run:
        print(subject)
        print()
        print(body)
        return 0

    result = subprocess.run(
        ["git", "commit", "-m", subject, "-m", body],
        cwd=ai_status.ROOT,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

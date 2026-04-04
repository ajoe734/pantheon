#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prompt_builder import build_review_prompt, write_prompt_file


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entries.append(json.loads(line))
    return entries


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"tasks": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def make_job(
    *,
    queue_dir: Path,
    prompt_text: str,
    prompt_path: Path,
    task: dict[str, Any],
    reviewer_name: str,
    reviewer_cfg: dict[str, Any],
    project_id: str,
    auto_send: bool = False,
) -> Path:
    stamp = iso_now().replace(":", "-")
    job_id = f"{stamp}-{task['id']}-{reviewer_name}".replace("/", "-")
    job = {
        "schema_version": "1.0",
        "job_id": job_id,
        "created_at": iso_now(),
        "project_id": project_id,
        "task_id": task["id"],
        "task_title": task.get("title", task["id"]),
        "reviewer": reviewer_name,
        "target_profile": reviewer_cfg["target_profile"],
        "target_app": reviewer_cfg.get("chatbox_label"),
        "prompt_text": prompt_text,
        "prompt_path": str(prompt_path),
        "auto_send": auto_send,
        "metadata": {
            "owner": task.get("owner"),
            "reviewer": task.get("reviewer"),
            "status": task.get("status"),
            "artifacts": task.get("artifacts") or [],
            "depends_on": task.get("depends_on") or [],
        },
    }
    queue_dir.mkdir(parents=True, exist_ok=True)
    target = queue_dir / f"{job_id}.json"
    target.write_text(json.dumps(job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def process_once(args: argparse.Namespace) -> int:
    status = load_json(args.status)
    config = load_json(args.config)
    current_work_text = args.current.read_text(encoding="utf-8") if args.current.exists() else ""
    activity_entries = load_jsonl(args.activity)
    state_file = args.state_file or Path(config.get("state_file", "./tools/pantheon-local/.watcher-state.json"))
    state = load_state(state_file)

    task_state = state.setdefault("tasks", {})
    prompt_dir = args.prompt_dir or Path(config.get("prompt_output_dir", "./tools/pantheon-local/review-jobs"))
    project_id = config.get("project_id", status.get("project", "unknown-project"))
    reviewers = config.get("reviewers", {})
    created = 0

    for task in status.get("tasks", []):
        task_id = task.get("id")
        reviewer_name = task.get("reviewer")
        if not task_id or reviewer_name not in reviewers:
            continue

        previous_status = task_state.get(task_id, {}).get("status")
        current_status = task.get("status")
        signature = {
            "status": current_status,
            "last_update": task.get("last_update"),
        }

        should_trigger = previous_status != "review" and current_status == "review"

        if should_trigger:
            reviewer_cfg = reviewers[reviewer_name]
            stamp = iso_now().replace(":", "-")
            prompt_text = build_review_prompt(
                task=task,
                project_id=project_id,
                reviewer_name=reviewer_name,
                reviewer_cfg=reviewer_cfg,
                current_work_text=current_work_text,
                activity_entries=activity_entries,
                status_path=str(args.status),
                current_path=str(args.current),
                activity_path=str(args.activity),
            )
            prompt_path = write_prompt_file(prompt_dir, reviewer_name, task_id, prompt_text, stamp)
            job_path = make_job(
                queue_dir=args.queue_dir,
                prompt_text=prompt_text,
                prompt_path=prompt_path,
                task=task,
                reviewer_name=reviewer_name,
                reviewer_cfg=reviewer_cfg,
                project_id=project_id,
                auto_send=args.auto_send,
            )
            print(f"[review-job] created {job_path} for {reviewer_name} -> {task_id}")
            created += 1

        task_state[task_id] = signature

    save_state(state_file, state)
    return created


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch ai-status.json and emit review jobs on non-review -> review transitions.")
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--activity", type=Path, required=True)
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--prompt-dir", type=Path)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--auto-send", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.once:
        process_once(args)
        return 0

    print("[watch-review] started")
    while True:
        try:
            process_once(args)
        except KeyboardInterrupt:
            print("\n[watch-review] stopped")
            return 0
        except Exception as exc:
            print(f"[watch-review] error: {exc}")
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

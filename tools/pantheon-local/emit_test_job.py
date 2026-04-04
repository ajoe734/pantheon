#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit a smoke-test review job without reading ai-status.json")
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--target-profile", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--reviewer", default="Claude")
    parser.add_argument("--project-id", default="pantheon-local")
    parser.add_argument("--auto-send", action="store_true")
    args = parser.parse_args()

    stamp = iso_now().replace(":", "-")
    job_id = f"{stamp}-{args.task_id}-{args.reviewer}"
    prompt = f"""這是一個 smoke test reviewer prompt。\n\nTask: {args.task_id}\nTitle: {args.title}\nReviewer: {args.reviewer}\n\n如果你看得到這段內容，代表本機 queue -> 通知 -> 視窗切換 -> 貼上流程已經跑通。"""
    job = {
        "schema_version": "1.0",
        "job_id": job_id,
        "created_at": iso_now(),
        "project_id": args.project_id,
        "task_id": args.task_id,
        "task_title": args.title,
        "reviewer": args.reviewer,
        "target_profile": args.target_profile,
        "target_app": args.target_profile,
        "prompt_text": prompt,
        "prompt_path": None,
        "auto_send": args.auto_send,
        "metadata": {"smoke_test": True},
    }
    args.queue_dir.mkdir(parents=True, exist_ok=True)
    target = args.queue_dir / f"{job_id}.json"
    target.write_text(json.dumps(job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from pathlib import Path
from typing import Any


def _format_list(items: list[str] | None, empty: str = "- 無") -> str:
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)


def _recent_task_activity(entries: list[dict[str, Any]], task_id: str, limit: int = 5) -> list[dict[str, Any]]:
    matches = [entry for entry in entries if entry.get("task_id") == task_id]
    return matches[-limit:]


def build_review_prompt(
    *,
    task: dict[str, Any],
    project_id: str,
    reviewer_name: str,
    reviewer_cfg: dict[str, Any],
    current_work_text: str,
    activity_entries: list[dict[str, Any]],
    status_path: str,
    current_path: str,
    activity_path: str,
) -> str:
    summary = task.get("summary_zh") or task.get("title") or "(未提供中文說明)"
    review_notes = task.get("review_notes_zh") or []
    artifacts = task.get("artifacts") or []
    depends_on = task.get("depends_on") or []
    recent = _recent_task_activity(activity_entries, task.get("id", ""), limit=5)
    instructions = reviewer_cfg.get("instructions") or []

    recent_text = "\n".join(
        f"- {entry.get('ts', '-')}: {entry.get('agent', '-')}: {entry.get('message', '')}"
        for entry in recent
    ) or "- 無最近活動"

    prompt = f"""請先讀這些檔案，再開始工作：
- {status_path}
- {current_path}
- {activity_path}

你是 {reviewer_name}。
你現在優先要完成的是 review task，不要先跳去做別的題目。

## Task
- ID: `{task.get('id', '-')}`
- Title: {task.get('title', '-')}
- 中文說明: {summary}
- Owner: {task.get('owner', '-')}
- Reviewer: {task.get('reviewer', '-')}
- 狀態: {task.get('status', '-')}
- 依賴: {', '.join(depends_on) if depends_on else '無'}
- 下一步: {task.get('next', '-')}

## Review Notes
{_format_list(review_notes, empty='- 目前沒有額外 review notes')}

## Relevant Artifacts
{_format_list(artifacts)}

## Reviewer Instructions
{_format_list(instructions, empty='- 依 ai-status.json 與 current-work.md 判斷')}

## Recent Activity For This Task
{recent_text}

## Current Work Snapshot
以下是 current-work.md 摘要，方便你快速對齊：

```markdown
{current_work_text[:6000]}
```

請先完成 review，並把結果寫回現有的狀態流程。
如果 review 通過，請明確說明通過原因；
如果 review 不通過，請列出具體修正項，並避免只給抽象建議。

Project: {project_id}
"""
    return prompt


def write_prompt_file(prompt_dir: Path, reviewer_name: str, task_id: str, prompt_text: str, stamp: str) -> Path:
    target_dir = prompt_dir / reviewer_name
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{task_id}-{stamp}.md"
    target.write_text(prompt_text, encoding="utf-8")
    return target

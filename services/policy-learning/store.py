from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class PolicyLearningStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_path = self.data_dir / "policy_learning_jobs.json"

    def _read_jobs(self) -> Dict[str, Dict[str, Any]]:
        if not self.jobs_path.exists():
            return {}
        text = self.jobs_path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        payload = json.loads(text)
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items() if isinstance(value, dict)}

    def _write_jobs(self, jobs: Dict[str, Dict[str, Any]]) -> None:
        self.jobs_path.parent.mkdir(parents=True, exist_ok=True)
        self.jobs_path.write_text(json.dumps(jobs, indent=2, ensure_ascii=True), encoding="utf-8")

    def list_jobs(self) -> List[Dict[str, Any]]:
        return list(self._read_jobs().values())

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._read_jobs().get(job_id)

    def put_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        job_id = str(job.get("job_id") or job.get("id") or "").strip()
        if not job_id:
            raise ValueError("job_id is required")
        records = self._read_jobs()
        record = json.loads(json.dumps(job))
        record["job_id"] = job_id
        record["id"] = job_id
        records[job_id] = record
        self._write_jobs(records)
        return record

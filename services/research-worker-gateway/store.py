from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class ResearchWorkerGatewayStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_path = self.data_dir / "worker_jobs.json"
        self.events_path = self.data_dir / "worker_events.jsonl"
        self.outputs_path = self.data_dir / "worker_outputs.json"

    def _read_map(self, path: Path) -> Dict[str, Dict[str, Any]]:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        payload = json.loads(text)
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items() if isinstance(value, dict)}

    def _write_map(self, path: Path, payload: Dict[str, Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    def list_jobs(self) -> List[Dict[str, Any]]:
        return list(self._read_map(self.jobs_path).values())

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._read_map(self.jobs_path).get(job_id)

    def put_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        job_id = str(job.get("job_id") or job.get("id") or "").strip()
        if not job_id:
            raise ValueError("job_id is required")
        job["id"] = job_id
        job["job_id"] = job_id
        jobs = self._read_map(self.jobs_path)
        jobs[job_id] = json.loads(json.dumps(job))
        self._write_map(self.jobs_path, jobs)
        return jobs[job_id]

    def append_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(event))
        if not str(record.get("event_id") or "").strip():
            raise ValueError("event_id is required")
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            handle.write("\n")
        return record

    def list_events(self, job_id: str | None = None) -> List[Dict[str, Any]]:
        if not self.events_path.exists():
            return []
        records: List[Dict[str, Any]] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                records.append(payload)
        if job_id is not None:
            records = [record for record in records if record.get("job_id") == job_id]
        return records

    def put_output(self, output: Dict[str, Any]) -> Dict[str, Any]:
        output_id = str(output.get("output_id") or output.get("id") or "").strip()
        if not output_id:
            raise ValueError("output_id is required")
        output["id"] = output_id
        output["output_id"] = output_id
        outputs = self._read_map(self.outputs_path)
        outputs[output_id] = json.loads(json.dumps(output))
        self._write_map(self.outputs_path, outputs)
        return outputs[output_id]

    def list_outputs(self, job_id: str | None = None) -> List[Dict[str, Any]]:
        outputs = list(self._read_map(self.outputs_path).values())
        if job_id is not None:
            outputs = [output for output in outputs if output.get("job_id") == job_id]
        return outputs

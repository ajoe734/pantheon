from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class TrainingSessionStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_path = self.data_dir / "teaching_sessions.json"
        self.events_path = self.data_dir / "teaching_events.jsonl"
        self.controls_path = self.data_dir / "trainer_controls.json"
        self.previews_path = self.data_dir / "trainer_previews.json"
        self.replays_path = self.data_dir / "trainer_replays.json"

    def _read_map(self, path: Path) -> Dict[str, Dict[str, Any]]:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        payload = json.loads(text)
        if not isinstance(payload, dict):
            return {}
        return {str(k): v for k, v in payload.items() if isinstance(v, dict)}

    def _write_map(self, path: Path, payload: Dict[str, Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    def _read_jsonl(self, path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        records: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                records.append(payload)
        return records

    def list_sessions(self) -> List[Dict[str, Any]]:
        return list(self._read_map(self.sessions_path).values())

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._read_map(self.sessions_path).get(session_id)

    def put_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        session_id = str(session.get("session_id") or session.get("id") or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        records = self._read_map(self.sessions_path)
        records[session_id] = json.loads(json.dumps(session))
        self._write_map(self.sessions_path, records)
        return records[session_id]

    def append_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(event))
        if not str(record.get("session_id") or "").strip():
            raise ValueError("session_id is required")
        if not str(record.get("event_id") or "").strip():
            raise ValueError("event_id is required")
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            handle.write("\n")
        return record

    def list_event_log(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        records = self._read_jsonl(self.events_path)
        if session_id is not None:
            records = [record for record in records if record.get("session_id") == session_id]
        return records

    def list_controls(self) -> List[Dict[str, Any]]:
        return list(self._read_map(self.controls_path).values())

    def get_controls(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._read_map(self.controls_path).get(session_id)

    def put_controls(self, session_id: str, controls: Dict[str, Any]) -> Dict[str, Any]:
        records = self._read_map(self.controls_path)
        record = json.loads(json.dumps(controls))
        record["session_id"] = session_id
        records[session_id] = record
        self._write_map(self.controls_path, records)
        return record

    def list_previews(self) -> List[Dict[str, Any]]:
        return list(self._read_map(self.previews_path).values())

    def get_preview_bundle(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._read_map(self.previews_path).get(session_id)

    def put_preview_bundle(self, session_id: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
        records = self._read_map(self.previews_path)
        record = json.loads(json.dumps(bundle))
        record["session_id"] = session_id
        records[session_id] = record
        self._write_map(self.previews_path, records)
        return record

    def list_replays(self) -> List[Dict[str, Any]]:
        return list(self._read_map(self.replays_path).values())

    def get_replay(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._read_map(self.replays_path).get(session_id)

    def put_replay(self, session_id: str, replay: Dict[str, Any]) -> Dict[str, Any]:
        records = self._read_map(self.replays_path)
        record = json.loads(json.dumps(replay))
        record["session_id"] = session_id
        records[session_id] = record
        self._write_map(self.replays_path, records)
        return record

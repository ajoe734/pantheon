"""Stateful Server-Sent Event substrate owned by the Events domain.

The BFF assembly layer supplies its existing buffers when it is ready to cut
over.  Until then, this module remains independently constructable so the
domain router has no import-time dependency on ``main``.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, Iterable, Optional, Sequence

from starlette.responses import StreamingResponse


MAX_SSE_EVENTS = 500

# Keep the compatibility channels accepted by the already-delivered Events
# router as well as the BFF substrate's current catalog.  This lets the
# prepared router preserve its public contract before main.py is cut over.
DEFAULT_SSE_CHANNEL_CATALOG: tuple[str, ...] = (
    "approval", "ask", "artifact", "runtime", "mcp", "skill", "channel",
    "tool", "ranking", "rebalance", "evolution", "research", "signal",
    "inbox", "journal", "postmortem", "loop", "sentinel", "intervention",
    "audit", "system", "telemetry", "alerts", "trading", "governance",
    "command_center", "kpi", "approvals", "feed", "signals", "decisions",
    "risk", "backtest",
)

DEFAULT_SSE_RESYNC_ROUTES: Dict[str, tuple[str, ...]] = {
    "approval": ("/bff/approvals", "/bff/v5/interventions"),
    "ask": (
        "/bff/management/ai/conversations",
        "/bff/management/ai/conversations/{id}",
        "/bff/agora/ask/sessions/{id}",
        "/bff/agora/committee/sessions/{id}",
    ),
}


class SseReplayUnavailableError(Exception):
    """The requested Last-Event-ID has fallen outside the replay window."""


class EventStreamService:
    """Own SSE buffers, replay, subscriptions, and internal event delivery.

    ``buffers`` and ``subscribers`` are explicit ports.  A later assembly
    change can therefore inject the live BFF substrate without this domain
    importing or introspecting ``main``.
    """

    def __init__(
        self,
        *,
        channels: Optional[Iterable[str]] = None,
        buffers: Optional[Dict[str, deque]] = None,
        subscribers: Optional[Dict[str, list[asyncio.Queue]]] = None,
        incident_buffer: Optional[deque] = None,
        incident_subscribers: Optional[list[asyncio.Queue]] = None,
        data_dir: Optional[str] = None,
        max_events: int = MAX_SSE_EVENTS,
        resync_routes: Optional[Dict[str, Sequence[str]]] = None,
    ) -> None:
        configured_channels = tuple(channels or DEFAULT_SSE_CHANNEL_CATALOG)
        self.channels = tuple(dict.fromkeys(configured_channels))
        self.channel_set = set(self.channels)
        self.max_events = max_events
        self.buffers = buffers if buffers is not None else {
            channel: deque(maxlen=max_events) for channel in self.channels
        }
        self.subscribers = subscribers if subscribers is not None else {
            channel: [] for channel in self.channels
        }
        for channel in self.channels:
            self.buffers.setdefault(channel, deque(maxlen=max_events))
            self.subscribers.setdefault(channel, [])
        self.incident_buffer = incident_buffer if incident_buffer is not None else deque(maxlen=max_events)
        self.incident_subscribers = incident_subscribers if incident_subscribers is not None else []
        self.data_dir = data_dir or os.getenv("PANTHEON_BFF_DATA_DIR", "data")
        routes = resync_routes or DEFAULT_SSE_RESYNC_ROUTES
        self.resync_routes = {channel: tuple(values) for channel, values in routes.items()}

    @staticmethod
    def _shared_replay_enabled() -> bool:
        mode = os.getenv("PANTHEON_BFF_SSE_REPLAY_STORE", "memory").strip().lower()
        return mode in {"1", "true", "file", "jsonl", "shared", "shared-file"}

    def replay_store_label(self, channel: str) -> str:
        return "file" if channel in self.channel_set and self._shared_replay_enabled() else "in-memory"

    def _channel_for_buffer(self, buffer: deque) -> Optional[str]:
        for channel, candidate in self.buffers.items():
            if candidate is buffer:
                return channel
        return None

    def _shared_replay_file(self, channel: str) -> Path:
        if channel not in self.channel_set:
            raise ValueError(f"Unknown SSE channel: {channel}")
        replay_dir = Path(self.data_dir) / "sse_replay"
        replay_dir.mkdir(parents=True, exist_ok=True)
        return replay_dir / f"{channel}.jsonl"

    def _read_shared_events(self, channel: str) -> list[dict[str, Any]]:
        path = self._shared_replay_file(channel)
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    event = json.loads(line)
                    if isinstance(event, dict):
                        events.append(event)
        except (OSError, json.JSONDecodeError) as exc:
            raise SseReplayUnavailableError("Shared SSE replay store is unreadable") from exc
        return events[-self.max_events:]

    def _append_shared_event(self, channel: Optional[str], event: dict[str, Any]) -> None:
        if not channel or not self._shared_replay_enabled():
            return
        path = self._shared_replay_file(channel)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        try:
            lines = [line for line in path.read_text(encoding="utf-8").splitlines(True) if line.strip()]
            if len(lines) > self.max_events:
                path.write_text("".join(lines[-self.max_events:]), encoding="utf-8")
        except OSError as exc:
            raise SseReplayUnavailableError("Shared SSE replay store is unreadable") from exc

    @staticmethod
    def _make_event_id(prefix: str = "evt") -> str:
        return f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def format_event(event: dict[str, Any]) -> str:
        return (
            f"id: {event['id']}\n"
            f"event: {event['type']}\n"
            f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        )

    def replay_headers(self, channel: str) -> Dict[str, str]:
        headers = {
            "X-SSE-Channel": channel,
            "X-SSE-Replay-Supported": "true",
            "X-SSE-Replay-Window-Events": str(self.max_events),
            "X-SSE-Buffer-Size": str(self.max_events),
            "X-SSE-Replay-Store": self.replay_store_label(channel),
        }
        if routes := self.resync_routes.get(channel):
            headers["X-SSE-Resync-Routes"] = ",".join(routes)
        return headers

    def publish(self, buffer: deque, subscribers: list[asyncio.Queue], event_type: str, data: dict[str, Any]) -> str:
        event_id = self._make_event_id()
        # Match ``SseEventEnvelope[Dict[str, Any]].model_dump(mode="json")``
        # without coupling this prepared domain module to the BFF import root.
        event = {
            "id": event_id,
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": dict(data or {}),
        }
        buffer.append((event_id, event))
        self._append_shared_event(self._channel_for_buffer(buffer), event)
        for queue in list(subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                continue
        return event_id

    @staticmethod
    def _replay_from_events(
        events: Sequence[dict[str, Any]], last_event_id: Optional[str], *, source_label: str,
    ) -> list[dict[str, Any]]:
        if not last_event_id:
            return list(events)
        found = False
        result: list[dict[str, Any]] = []
        for event in events:
            if found:
                result.append(event)
            elif event.get("id") == last_event_id:
                found = True
        if not found:
            raise SseReplayUnavailableError(
                f"Event ID {last_event_id} is no longer in the {source_label}"
            )
        return result

    def replay(self, channel: str, buffer: deque, last_event_id: Optional[str]) -> list[dict[str, Any]]:
        if self._shared_replay_enabled() and channel in self.channel_set:
            return self._replay_from_events(
                self._read_shared_events(channel), last_event_id, source_label="replay store",
            )
        return self._replay_from_events(
            [event for _, event in buffer], last_event_id, source_label="buffer",
        )

    async def stream(
        self,
        channel: str,
        buffer: deque,
        subscribers: list[asyncio.Queue],
        last_event_id: Optional[str],
    ) -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        subscribers.append(queue)
        try:
            for event in self.replay(channel, buffer, last_event_id):
                yield self.format_event(event)
            while True:
                try:
                    yield self.format_event(await asyncio.wait_for(queue.get(), timeout=30.0))
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            if queue in subscribers:
                subscribers.remove(queue)

    def stream_response(
        self,
        channel: str,
        last_event_id: Optional[str],
        *,
        bff_error: Callable[..., Exception],
        conflict_code: Any,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> StreamingResponse:
        buffer = self.buffers[channel]
        subscribers = self.subscribers[channel]
        try:
            self.replay(channel, buffer, last_event_id)
        except SseReplayUnavailableError as exc:
            error = bff_error(
                409,
                conflict_code,
                str(exc),
                "SSE_REPLAY_HISTORY_MISSING",
                suggestion="Resync canonical state via GET routes before reconnecting to the stream",
                details_extra={
                    "channel": channel,
                    "lastEventId": last_event_id,
                    "replaySupported": True,
                    "replayWindowEvents": self.max_events,
                    "replayStore": self.replay_store_label(channel),
                    "resyncRoutes": list(self.resync_routes.get(channel, ())),
                },
            )
            error.headers = self.replay_headers(channel)
            raise error from exc
        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            **self.replay_headers(channel),
        }
        if extra_headers:
            headers.update(extra_headers)
        return StreamingResponse(
            self.stream(channel, buffer, subscribers, last_event_id),
            media_type="text/event-stream",
            headers=headers,
        )

    def publish_internal(
        self,
        *,
        event_type: str,
        channel: Optional[str],
        runtime_id: Optional[str],
        incident_id: Optional[str],
        payload: Dict[str, Any],
        bff_error: Callable[..., Exception],
        validation_code: Any,
    ) -> str:
        resolved_channel = channel or self._infer_channel(event_type)
        event_payload = dict(payload or {})
        if resolved_channel == "journal" and event_type.startswith("incident"):
            event_id = self.publish(
                self.incident_buffer,
                self.incident_subscribers,
                event_type,
                {"incident_id": incident_id, **event_payload},
            )
            if "journal" in self.channel_set:
                self.publish(
                    self.buffers["journal"],
                    self.subscribers["journal"],
                    event_type,
                    {"incident_id": incident_id, **event_payload},
                )
            return event_id
        if resolved_channel not in self.channel_set:
            raise bff_error(
                400,
                validation_code,
                f"Unknown SSE channel: {resolved_channel}",
                f"Channel must be one of {list(self.channels)}",
            )
        if resolved_channel == "runtime" and runtime_id:
            event_payload["runtime_id"] = runtime_id
        return self.publish(
            self.buffers[resolved_channel],
            self.subscribers[resolved_channel],
            event_type,
            event_payload,
        )

    @staticmethod
    def _infer_channel(event_type: str) -> str:
        if event_type.startswith("runtime"):
            return "runtime"
        if event_type.startswith("incident"):
            return "journal"
        if event_type.startswith("kill_switch"):
            return "system"
        if event_type.startswith("approval"):
            return "approval"
        if event_type.startswith("ask"):
            return "ask"
        return "system"

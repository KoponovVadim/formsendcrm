from collections import defaultdict
from typing import Any


class EventBus:
    """In-memory pub/sub for broadcasting realtime events to websockets."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[Any]] = defaultdict(set)

    async def publish(self, topic: str, payload: dict) -> None:
        subscribers = list(self._subscribers.get(topic, set()))
        for ws in subscribers:
            try:
                await ws.send_json({"topic": topic, "payload": payload})
            except Exception:
                self.unsubscribe(topic, ws)

    def subscribe(self, topic: str, websocket: Any) -> None:
        self._subscribers[topic].add(websocket)

    def unsubscribe(self, topic: str, websocket: Any) -> None:
        if topic in self._subscribers:
            self._subscribers[topic].discard(websocket)


event_bus = EventBus()

"""Async pub/sub bus for codoc live events (activity, proposal, accept, reject, reflect_done)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class BusEvent:
    topic: str  # "activity" | "proposal" | "accept" | "reject" | "reflect_done"
    data: dict


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def publish(self, event: BusEvent) -> None:
        dead: list[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)


# Module-level singleton — imported by routes.py
bus = EventBus()

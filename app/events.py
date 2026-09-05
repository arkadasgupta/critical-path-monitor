"""Append-only event log with a monotonic sequence number.

The UI polls with the last seq it saw rather than holding a socket open.
"""

from collections import deque

from pydantic import BaseModel, ConfigDict


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    at_min: float
    message: str
    job_id: str | None = None


class EventLog:
    def __init__(self, capacity: int = 500) -> None:
        self._events: deque[Event] = deque(maxlen=capacity)
        self._seq = 0

    @property
    def seq(self) -> int:
        return self._seq

    def emit(self, at_min: float, message: str, job_id: str | None = None) -> None:
        self._seq += 1
        self._events.append(
            Event(seq=self._seq, at_min=at_min, message=message, job_id=job_id)
        )

    def since(self, seq: int) -> list[Event]:
        return [e for e in self._events if e.seq > seq]

    def clear(self) -> None:
        self._events.clear()

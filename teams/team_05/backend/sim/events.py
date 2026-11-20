"""Event helpers. OWNER: sim agent. Event text is Russian (see CONTRACTS.md section 1)."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from core.models import Event, EventType


log = logging.getLogger("snegoplan.events")


class EventLog:
    """Append-only log with id generation ("E-0001"), subscribers and a drain cursor (ws broadcast).
    Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[Event] = []
        self._drained = 0
        self._subs: list[Callable[[Event], None]] = []

    def add(self, t: float, type: EventType, payload: dict, text: str) -> Event:
        with self._lock:
            ev = Event(id=f"E-{len(self._events) + 1:04d}", t=t, type=type, payload=payload, text=text)
            self._events.append(ev)
            subs = list(self._subs)
        log.info("EVENT %s t=%.0f %s: %s", ev.id, t, getattr(type, "value", type), text)
        for cb in subs:
            try:
                cb(ev)
            except Exception:  # subscriber errors must never break the sim
                log.exception("event subscriber failed")
        return ev

    def subscribe(self, cb: Callable[[Event], None]) -> None:
        with self._lock:
            self._subs.append(cb)

    def recent(self, n: int = 20) -> list[Event]:
        with self._lock:
            return list(self._events[-n:]) if n > 0 else []

    def all(self) -> list[Event]:
        with self._lock:
            return list(self._events)

    def drain_new(self) -> list[Event]:
        """Events added since last drain (for the ws broadcaster)."""
        with self._lock:
            new = self._events[self._drained:]
            self._drained = len(self._events)
            return list(new)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._drained = 0


def breakdown_text(vehicle_id: str, street: str | None) -> str:
    return f"Поломка: {vehicle_id} остановился" + (f" на {street}" if street else "")


def request_text(street: str | None, task_id: str) -> str:
    return f"Новая заявка {task_id}: расчистить {street or 'участок'}"


def task_done_text(vehicle_id: str, task_id: str) -> str:
    return f"{vehicle_id} завершил участок {task_id}"


def plan_applied_text(proposal_id: str) -> str:
    return f"Применён новый план ({proposal_id})"


def proposal_created_text(proposal_id: str) -> str:
    return f"Предложен новый план ({proposal_id})"


def proposal_rejected_text(proposal_id: str) -> str:
    return f"Предложение {proposal_id} отклонено"


def at_depot_text(vehicle_id: str) -> str:
    return f"{vehicle_id} вернулся на базу"


def at_dump_finish_text(vehicle_id: str) -> str:
    return f"{vehicle_id} прибыл на снегоотвал (финиш)"


def fmt_km(x: float) -> str:
    """Russian decimal comma: 12,4 км."""
    return f"{x:.1f}".replace(".", ",") + " км"


def fmt_signed_km(x: float) -> str:
    return ("+" if x >= 0 else "−") + f"{abs(x):.1f}".replace(".", ",") + " км"


def fmt_duration_min(minutes: float) -> str:
    """'2 ч 05 мин' / '35 мин'."""
    m = int(round(abs(minutes)))
    h, mm = divmod(m, 60)
    return f"{h} ч {mm:02d} мин" if h else f"{mm} мин"


def fmt_clock(t: float, start_hour: int = 6) -> str:
    """Sim seconds since scenario start -> 'HH:MM' (scenario starts 06:00)."""
    total = int(t // 60) + start_hour * 60
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"

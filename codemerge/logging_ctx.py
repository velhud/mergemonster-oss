from __future__ import annotations
from contextvars import ContextVar

RUN_ID: ContextVar[str] = ContextVar("run_id", default="-")
EVENT_WRITER: ContextVar[object | None] = ContextVar("event_writer", default=None)

def set_run_id(value: str) -> None:
    RUN_ID.set(value)

def get_run_id() -> str:
    return RUN_ID.get()

def set_event_writer(writer) -> None:
    EVENT_WRITER.set(writer)

def get_event_writer():
    return EVENT_WRITER.get()

from __future__ import annotations

from app.enums import EventType


def event_payload(message: str, /, **extra):
    payload = {"summary": message}
    if "summary" in extra and "detail_summary" not in extra:
        extra["detail_summary"] = extra.pop("summary")
    payload.update(extra)
    return payload


def event_type_name(event_type: EventType | str) -> str:
    return event_type.value if isinstance(event_type, EventType) else event_type

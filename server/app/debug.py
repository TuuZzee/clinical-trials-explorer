"""DEBUG events inherit Uvicorn's error logger level and handlers; no global config."""

import json
import logging
import re
from contextvars import ContextVar
from unicodedata import category

logger = logging.getLogger("uvicorn.error.query_viz")
request_id: ContextVar[str] = ContextVar("query_viz_request_id", default="-")
MAX_TEXT = 256


def _safe(value):
    if isinstance(value, str):
        text = "".join("?" if category(char).startswith("C") else char for char in value)
        text = re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", text)
        return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "..."
    if isinstance(value, dict):
        return {_safe(key): _safe(item) for key, item in list(value.items())[:16]}
    if value is None or type(value) in {int, float, bool}:
        return value
    return "[omitted]"


def debug(event: str, **fields) -> None:
    """Emit bounded, sanitized metadata. Broken logging must not break a request."""
    try:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        details = {"request_id": request_id.get(), **_safe(fields)}
        logger.debug(
            "%s %s",
            event,
            json.dumps(details, ensure_ascii=True),
            extra={"event": event, "details": details},
        )
    except Exception:
        # Logging handlers/streams are external to request correctness.
        pass

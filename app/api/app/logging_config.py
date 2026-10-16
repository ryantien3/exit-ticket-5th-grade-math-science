"""JSON-lines logging to stdout, in a shape Cloud Logging understands.

Cloud Logging reads the "severity" field to set each entry's log level.
To attach structured data to a log line, pass it as extra={"json_fields": {...}};
those fields are merged into the JSON object instead of being squashed into "message".
"""

import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        json_fields = getattr(record, "json_fields", None)
        if json_fields:
            entry.update(json_fields)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Uvicorn sets up its own plain-text handlers; remove them so its logs flow
    # up to the root logger and come out as JSON too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True

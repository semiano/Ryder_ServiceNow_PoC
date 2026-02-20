import json
import logging
import os
from typing import Any


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level, logging.INFO))
    return logger


def log_event(
    logger: logging.Logger,
    level: str,
    message: str,
    correlation_id: str,
    ticket_id: str | None,
    **kwargs: Any,
) -> None:
    payload: dict[str, Any] = {
        "message": message,
        "correlationId": correlation_id,
        "ticketId": ticket_id,
    }
    payload.update(kwargs)
    log_fn = getattr(logger, level.lower(), logger.info)
    log_fn(json.dumps(payload, default=str, sort_keys=True))

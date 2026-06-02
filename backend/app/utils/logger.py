import logging
import uuid
from contextvars import ContextVar

import structlog

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

def add_request_id(logger, method, event_dict):
    # WHY: pulls request_id from ContextVar so every log line in a request
    # gets the ID automatically — no need to pass it at every call site
    event_dict["request_id"] = request_id_var.get()
    return event_dict


def configure(log_format: str, log_level: str) -> None:
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        add_request_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=shared_processors + [
            structlog.processors.format_exc_info,  # WHY: renders exception tracebacks into log output; without this exc_info=True is silently swallowed
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            # WHY: converts string level ("INFO") to the stdlib int constant
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )

def get_logger(name: str = __name__):
    # WHY: thin wrapper so callers do `from app.utils.logger import get_logger`
    # rather than importing structlog directly — one place to change if we
    # ever swap the logging library
    return structlog.get_logger(name)

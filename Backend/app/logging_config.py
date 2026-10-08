import json
import logging


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Only explicitly selected fields are logged; never request bodies/headers.
        return json.dumps({"level": record.levelname, "event": record.getMessage(), **getattr(record, "safe_fields", {})})


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("crashpulse")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger

import logging
import re
import sys

from pythonjsonlogger import jsonlogger

_PATTERNS = [
    (re.compile(r"(apiKey|api_key)=([^&\s]+)", re.I), r"\1=[REDACTED]"),
    (re.compile(r"(Authorization:\s*)(\S+\s+)?(\S+)", re.I), r"\1[REDACTED]"),
    (re.compile(r"([A-Z\-]*(?:SIGNATURE|ACCESS-KEY)[A-Z\-]*:\s*)(\S+)", re.I), r"\1[REDACTED]"),
]


def redact(text: str) -> str:
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.getMessage()))
        record.args = ()
        if record.exc_info:
            exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_text = redact(exc_text)
            record.exc_info = None
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactionFilter())
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel("WARNING")

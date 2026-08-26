"""
Pipeline de logging estructurado y no bloqueante de TritonMonitor.
"""

from __future__ import annotations

import gzip
import json
import logging
import logging.config
import logging.handlers
import os
import queue
import tempfile
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

LOG_QUEUE: queue.Queue[logging.LogRecord] = queue.Queue()


class AsyncJSONFormatter(logging.Formatter):
    """Formatea LogRecord como JSON estructurado y forense."""

    _STANDARD_FIELDS = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "process",
            "processName",
            "taskName",
            "message",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        """Serializa un LogRecord completo como JSON."""
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process": record.process,
            "thread": record.thread,
            "threadName": record.threadName,
            "processName": record.processName,
            "taskName": getattr(record, "taskName", None),
        }

        payload.update(self._extract_dynamic_fields(record))

        if record.exc_info is not None:
            payload["exception"] = self._serialize_exception(record.exc_info[1])

        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _extract_dynamic_fields(
        self,
        record: logging.LogRecord,
    ) -> dict[str, Any]:
        """Extrae metadatos personalizados agregados mediante extra."""
        fields: dict[str, Any] = {}

        for key, value in record.__dict__.items():
            if key in self._STANDARD_FIELDS or key.startswith("_"):
                continue

            fields[key] = self._make_json_safe(value)

        if "task_name" in fields:
            fields["taskName"] = fields.pop("task_name")

        return fields

    def _make_json_safe(self, value: Any) -> Any:
        """Convierte valores no JSON en representaciones seguras."""
        if value is None or isinstance(
            value,
            (str, int, float, bool),
        ):
            return value

        if isinstance(value, dict):
            return {str(key): self._make_json_safe(item) for key, item in value.items()}

        if isinstance(value, (list, tuple, set)):
            return [self._make_json_safe(item) for item in value]

        return repr(value)

    def _serialize_exception(
        self,
        exception: BaseException,
    ) -> dict[str, Any]:
        """
        Serializa recursivamente excepciones, grupos, causas, contextos
        y notas dinámicas.
        """
        data: dict[str, Any] = {
            "type": type(exception).__name__,
            "module": type(exception).__module__,
            "message": str(exception),
            "traceback": "".join(
                traceback.format_exception(
                    type(exception),
                    exception,
                    exception.__traceback__,
                )
            ),
        }

        if isinstance(exception, httpx.HTTPStatusError):
            response = exception.response
            request = exception.request

            data["http"] = {
                "method": request.method,
                "url": str(request.url),
                "status_code": response.status_code,
                "reason": response.reason_phrase,
                "response_headers": dict(response.headers),
                "response_body": response.text,
            }

        notes = getattr(exception, "__notes__", None)

        if notes:
            data["notes"] = list(notes)

        if isinstance(exception, BaseExceptionGroup):
            data["exceptions"] = [
                self._serialize_exception(child) for child in exception.exceptions
            ]

        if exception.__cause__ is not None:
            data["cause"] = self._serialize_exception(exception.__cause__)

        if exception.__context__ is not None and not exception.__suppress_context__:
            data["context"] = self._serialize_exception(exception.__context__)

        return data


class PreservingQueueHandler(logging.handlers.QueueHandler):
    """
    QueueHandler que conserva la información de excepción para el
    procesamiento posterior del QueueListener.
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        """Copia el LogRecord sin destruir exc_info."""
        prepared = logging.makeLogRecord(record.__dict__.copy())
        prepared.message = record.getMessage()

        return prepared


def _gzip_namer(filename: str) -> str:
    """Genera el nombre final del archivo comprimido."""
    return f"{filename}.gz"


def _gzip_rotator(source: str, destination: str) -> None:
    """
    Comprime el archivo rotado y reemplaza el destino de forma atómica.
    """
    destination_path = Path(destination)
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination_path.name}.",
            suffix=".tmp",
            dir=destination_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

            with (
                gzip.open(
                    temporary_file,
                    mode="wb",
                ) as gzip_file,
                open(source, "rb") as source_file,
            ):
                while chunk := source_file.read(64 * 1024):
                    gzip_file.write(chunk)

            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, destination_path)
        temporary_path = None

        os.remove(source)

    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def create_queue_pipeline(
    filename: str,
    level: int | str = logging.INFO,
    max_bytes: int = 2 * 1024 * 1024,
    backup_count: int = 3,
) -> PreservingQueueHandler:
    """
    Crea el pipeline completo de logging.

    Este factory es invocado directamente por dictConfig. El único
    RotatingFileHandler creado aquí pertenece al QueueListener.
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper())

    log_path = Path(filename)
    log_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,
    )

    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(AsyncJSONFormatter())
    file_handler.namer = _gzip_namer
    file_handler.rotator = _gzip_rotator

    queue_handler = PreservingQueueHandler(LOG_QUEUE)
    queue_handler.setLevel(logging.DEBUG)

    listener = logging.handlers.QueueListener(
        LOG_QUEUE,
        file_handler,
        respect_handler_level=True,
    )

    queue_handler.listener = listener
    listener.start()

    return queue_handler


LOGGING_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": ("triton_telemetry.logging_engine.AsyncJSONFormatter"),
        },
    },
    "handlers": {
        "queue": {
            "()": ("triton_telemetry.logging_engine.create_queue_pipeline"),
            "filename": "logs/triton_services.log",
            "level": "INFO",
            "max_bytes": 2 * 1024 * 1024,
            "backup_count": 3,
        },
    },
    "loggers": {
        "triton_monitor": {
            "level": "INFO",
            "handlers": ["queue"],
            "propagate": False,
        },
    },
}


def setup_logging(
    log_file: str | Path = "logs/triton_services.log",
    level: int = logging.INFO,
) -> tuple[
    logging.Logger,
    logging.handlers.QueueListener,
]:
    """
    Configura el pipeline mediante logging.config.dictConfig.
    """
    log_path = Path(log_file)
    log_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    config = {
        "version": LOGGING_CONFIG["version"],
        "disable_existing_loggers": (LOGGING_CONFIG["disable_existing_loggers"]),
        "formatters": LOGGING_CONFIG["formatters"].copy(),
        "handlers": {
            "queue": {
                "()": ("triton_telemetry.logging_engine.create_queue_pipeline"),
                "filename": str(log_path),
                "level": level,
                "max_bytes": 2 * 1024 * 1024,
                "backup_count": 3,
            }
        },
        "loggers": {
            "triton_monitor": {
                "level": level,
                "handlers": ["queue"],
                "propagate": False,
            }
        },
    }

    logging.config.dictConfig(config)

    logger = logging.getLogger("triton_monitor")

    queue_handler = next(
        handler
        for handler in logger.handlers
        if isinstance(
            handler,
            PreservingQueueHandler,
        )
    )

    listener = queue_handler.listener

    return logger, listener

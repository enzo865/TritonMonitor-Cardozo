"""
Validador forense de los logs de TritonMonitor.

Comprueba la integridad de los registros JSON y de los archivos
históricos comprimidos mediante Gzip.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

LOG_DIRECTORY = Path("logs")
ACTIVE_LOG = LOG_DIRECTORY / "triton_services.log"


def validate_json_lines(log_path: Path) -> int:
    """
    Valida todos los registros JSON de un archivo de log.

    Returns:
        Cantidad de registros procesados.
    """
    records = 0

    with log_path.open(
        mode="r",
        encoding="utf-8",
    ) as log_file:
        for line_number, line in enumerate(
            log_file,
            start=1,
        ):
            line = line.strip()

            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssertionError(
                    f"JSON inválido en {log_path}:{line_number}"
                ) from exc

            required_fields = {
                "timestamp",
                "level",
                "logger",
                "process",
                "threadName",
                "taskName",
            }

            missing_fields = required_fields - record.keys()

            if missing_fields:
                raise AssertionError(
                    f"Faltan campos {sorted(missing_fields)} "
                    f"en {log_path}:{line_number}"
                )

            records += 1

            if record.get("level") == "ERROR":
                _validate_exception_record(
                    record,
                    log_path,
                    line_number,
                )

    return records


def _validate_exception_record(
    record: dict,
    log_path: Path,
    line_number: int,
) -> None:
    """Valida la estructura forense de un registro de error."""
    exception = record.get("exception")

    if not isinstance(exception, dict):
        raise TypeError(
            f"El registro ERROR no contiene un objeto "
            f"'exception' válido en {log_path}:{line_number}"
        )

    if exception.get("type") != "ExceptionGroup":
        raise AssertionError(f"Se esperaba ExceptionGroup en {log_path}:{line_number}")

    children = exception.get("exceptions")

    if not isinstance(children, list) or not children:
        raise AssertionError(
            f"ExceptionGroup sin excepciones hijas en {log_path}:{line_number}"
        )

    for child in children:
        _validate_exception_node(child)


def _validate_exception_node(
    exception: dict,
) -> None:
    """Valida recursivamente un nodo de excepción."""
    if not isinstance(exception, dict):
        raise TypeError(
            "Un nodo de excepción no es un objeto JSON."
        )

    required_fields = {
        "type",
        "module",
        "message",
        "traceback",
    }

    missing_fields = required_fields - exception.keys()

    if missing_fields:
        raise AssertionError(f"Faltan campos de excepción: {sorted(missing_fields)}")

    if exception.get("type") in {
        "ProviderTimeoutError",
        "CorruptedPayloadError",
        "NetworkPeeringError",
    }:
        notes = exception.get("notes")

        if not isinstance(notes, list) or not notes:
            raise AssertionError(
                f"La excepción {exception.get('type')} no contiene notas forenses."
            )

    if exception.get("type") == "CorruptedPayloadError":
        cause = exception.get("cause")

        if not isinstance(cause, dict):
            raise AssertionError("CorruptedPayloadError sin causa.")

        if cause.get("type") == "HTTPStatusError":
            http_data = cause.get("http")

            if not isinstance(http_data, dict):
                raise AssertionError("HTTPStatusError sin metadatos HTTP.")

            required_http_fields = {
                "method",
                "url",
                "status_code",
                "reason",
                "response_headers",
                "response_body",
            }

            missing_http_fields = required_http_fields - http_data.keys()

            if missing_http_fields:
                raise AssertionError(
                    f"Faltan metadatos HTTP: {sorted(missing_http_fields)}"
                )

    cause = exception.get("cause")

    if isinstance(cause, dict):
        _validate_exception_node(cause)

    context = exception.get("context")

    if isinstance(context, dict):
        _validate_exception_node(context)

    children = exception.get("exceptions")

    if isinstance(children, list):
        for child in children:
            _validate_exception_node(child)


def validate_gzip_files() -> int:
    """
    Valida todos los backups Gzip del directorio de logs.

    Returns:
        Cantidad de archivos Gzip comprobados.
    """
    gzip_files = sorted(LOG_DIRECTORY.glob("*.gz"))

    if len(gzip_files) > 3:
        raise AssertionError("Se encontraron más de tres backups Gzip.")

    for gzip_path in gzip_files:
        _validate_gzip_file(gzip_path)

    return len(gzip_files)


def _validate_gzip_file(gzip_path: Path) -> None:
    """Descomprime un backup y verifica que contenga JSON válido."""
    with gzip.open(
        gzip_path,
        mode="rt",
        encoding="utf-8",
    ) as gzip_file:
        content = gzip_file.read()

    if not content.strip():
        raise AssertionError(f"El archivo Gzip está vacío: {gzip_path}")

    for line_number, line in enumerate(
        content.splitlines(),
        start=1,
    ):
        line = line.strip()

        if not line:
            continue

        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"JSON inválido dentro de {gzip_path}, línea {line_number}"
            ) from exc


def main() -> None:
    """Ejecuta todas las validaciones forenses."""
    if not ACTIVE_LOG.exists():
        raise FileNotFoundError(f"No existe el log activo: {ACTIVE_LOG}")

    record_count = validate_json_lines(ACTIVE_LOG)
    gzip_count = validate_gzip_files()

    print(
        f"Validación correcta: "
        f"{record_count} registros JSON, "
        f"{gzip_count} backups Gzip."
    )


if __name__ == "__main__":
    main()

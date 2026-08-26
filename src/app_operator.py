"""
Punto de entrada CLI oficial de TritonMonitor.
"""

from __future__ import annotations

import asyncio
import logging

from triton_telemetry.core import scan_all_providers
from triton_telemetry.exceptions import (
    CorruptedPayloadError,
    NetworkPeeringError,
    ProviderTimeoutError,
)
from triton_telemetry.logging_engine import setup_logging
from triton_telemetry.sanitizer import parse_args


def _print_exception_group(
    title: str,
    exception_group: ExceptionGroup,
) -> None:
    """
    Imprime en consola los errores contenidos en un ExceptionGroup.

    Las notas agregadas mediante Exception.add_note() también se
    imprimen para conservar el contexto forense del incidente.
    """
    print(f"\nERROR: {title}")

    for index, exception in enumerate(
        exception_group.exceptions,
        start=1,
    ):
        print(f"  [{index}] {type(exception).__name__}: {exception}")

        notes = getattr(exception, "__notes__", [])

        for note in notes:
            print(f"      Nota: {note}")


def _print_results(results: list) -> None:
    """Imprime en consola el reporte nominal de telemetría."""
    print("\n=== TritonMonitor - Reporte de Telemetría ===")

    for result in results:
        print(
            f"{result.provider:<6} | "
            f"HTTP {result.status_code:<3} | "
            f"{result.elapsed_ms:>8.2f} ms | "
            f"{result.cluster_id}"
        )


def main() -> None:
    """
    Ejecuta el monitor CLI.
    """
    args = parse_args()

    logger, listener = setup_logging(
        log_file="logs/triton_services.log",
        level=logging.DEBUG if args.mode == "debug" else logging.INFO,
    )

    try:
        results = asyncio.run(
            scan_all_providers(
                providers=args.providers,
                cluster_id=args.cluster,
                timeout=args.timeout,
                chaos=args.chaos,
            )
        )

    except* ProviderTimeoutError as group:
        _print_exception_group(
            "Se detectaron timeouts concurrentes.",
            group,
        )

        logger.error(
            "Fallo concurrente de timeout.",
            exc_info=(
                type(group),
                group,
                group.__traceback__,
            ),
        )

    except* CorruptedPayloadError as group:
        _print_exception_group(
            "Se detectaron respuestas HTTP o payloads inválidos.",
            group,
        )

        logger.error(
            "Fallo concurrente por respuesta HTTP o payload corrupto.",
            exc_info=(
                type(group),
                group,
                group.__traceback__,
            ),
        )

    except* NetworkPeeringError as group:
        _print_exception_group(
            "Se detectaron fallos de red o resolución de hosts.",
            group,
        )

        logger.error(
            "Fallo concurrente de red o peering.",
            exc_info=(
                type(group),
                group,
                group.__traceback__,
            ),
        )

    else:
        if not args.quiet:
            _print_results(results)

        for result in results:
            logger.info(
                "Telemetría obtenida correctamente.",
                extra={
                    "provider": result.provider,
                    "cluster_id": result.cluster_id,
                    "url": result.url,
                    "status_code": result.status_code,
                    "elapsed_ms": round(
                        result.elapsed_ms,
                        2,
                    ),
                    "content_type": result.content_type,
                    "mode": args.mode,
                    "chaos": args.chaos,
                },
            )

        if args.verbose:
            print("\nDetalles:")
            for result in results:
                print(f"  {result.provider}: {result.url}")
                print(f"    Content-Type: {result.content_type}")

    finally:
        listener.stop()


if __name__ == "__main__":
    main()

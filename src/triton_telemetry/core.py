"""
Núcleo asíncrono de telemetría de TritonMonitor.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Final

import httpx

from .exceptions import (
    CorruptedPayloadError,
    NetworkPeeringError,
    ProviderTimeoutError,
)

NOMINAL_URLS: Final[dict[str, str]] = {
    "AWS": "https://jsonplaceholder.typicode.com/posts/1",
    "Azure": "https://jsonplaceholder.typicode.com/posts/2",
    "GCP": "https://jsonplaceholder.typicode.com/posts/3",
}

CHAOS_URLS: Final[dict[str, str]] = {
    "AWS": "https://httpbin.org/delay/3",
    "Azure": "https://httpbin.org/status/504",
    "GCP": "https://httpbin.org/xml",
}


@dataclass(frozen=True, slots=True)
class TelemetryResult:
    """Representa el resultado exitoso de una consulta."""

    provider: str
    cluster_id: str
    url: str
    status_code: int
    elapsed_ms: float
    content_type: str
    payload: object


async def _query_provider(
    client: httpx.AsyncClient,
    provider: str,
    cluster_id: str,
    url: str,
    timeout: float,
    chaos: bool,
) -> TelemetryResult:
    """
    Ejecuta una consulta HTTP asíncrona contra un proveedor.

    Las excepciones nativas de httpx se convierten en excepciones
    semánticas de TritonMonitor mediante encadenamiento explícito.
    """
    try:
        response = await client.get(url)
        response.raise_for_status()

    except httpx.TimeoutException as exc:
        error = ProviderTimeoutError(
            f"Timeout del proveedor {provider} para el clúster {cluster_id!r}."
        )
        error.add_note("Timeout superado en el nodo de telemetría de respaldo.")
        error.add_note(f"URL afectada: {url}")
        error.add_note(f"Timeout configurado: {timeout:.1f} segundos.")
        raise error from exc

    except httpx.ConnectError as exc:
        error = NetworkPeeringError(
            f"No fue posible establecer conexión con {provider} "
            f"para el clúster {cluster_id!r}."
        )
        error.add_note("Posible fallo de DNS, resolución de host o peering.")
        error.add_note(f"URL afectada: {url}")
        raise error from exc

    except httpx.HTTPStatusError as exc:
        error = CorruptedPayloadError(
            f"Estatus HTTP no esperado recibido desde {provider}: "
            f"{exc.response.status_code}."
        )
        error.add_note(f"Método HTTP: {exc.request.method}")
        error.add_note(f"Código HTTP: {exc.response.status_code}")
        error.add_note(f"URL afectada: {exc.request.url}")
        raise error from exc

    except httpx.RequestError as exc:
        error = NetworkPeeringError(
            f"Error de red al consultar {provider} para el clúster {cluster_id!r}."
        )
        error.add_note(f"Tipo de error httpx: {type(exc).__name__}")
        error.add_note(f"URL afectada: {url}")
        raise error from exc

    payload: object

    try:
        payload = response.json()

    except (json.JSONDecodeError, ValueError) as exc:
        error = CorruptedPayloadError(f"Payload no válido recibido desde {provider}.")
        error.add_note(
            f"Content-Type recibido: "
            f"{response.headers.get('content-type', 'desconocido')}"
        )
        error.add_note(f"Método HTTP: {response.request.method}")
        error.add_note(f"Código HTTP: {response.status_code}")

        if chaos:
            error.add_note("Fallo provocado mediante el escenario de caos.")

        raise error from exc

    return TelemetryResult(
        provider=provider,
        cluster_id=cluster_id,
        url=url,
        status_code=response.status_code,
        elapsed_ms=response.elapsed.total_seconds() * 1000,
        content_type=response.headers.get(
            "content-type",
            "desconocido",
        ),
        payload=payload,
    )


async def scan_all_providers(
    providers: list[str],
    cluster_id: str,
    timeout: float,
    chaos: bool = False,
) -> list[TelemetryResult]:
    """
    Ejecuta las consultas de todos los proveedores en paralelo.

    TaskGroup se utiliza como orquestador concurrente. Las excepciones
    semánticas son almacenadas individualmente para permitir que todas
    las tareas finalicen y posteriormente se construya un único
    ExceptionGroup con todos los incidentes ocurridos.
    """
    urls = CHAOS_URLS if chaos else NOMINAL_URLS

    results: list[TelemetryResult] = []
    errors: list[Exception] = []

    timeout_config = httpx.Timeout(timeout)

    async with httpx.AsyncClient(
        timeout=timeout_config,
        follow_redirects=True,
    ) as client:

        async def run_provider(provider: str) -> None:
            current_task = asyncio.current_task()
            task_name = current_task.get_name() if current_task is not None else None

            logging.getLogger("triton_monitor").debug(
                "Iniciando consulta de proveedor.",
                extra={
                    "provider": provider,
                    "cluster_id": cluster_id,
                    "url": urls[provider],
                    "mode": "chaos" if chaos else "nominal",
                    "task_name": task_name,
                },
            )

            try:
                result = await _query_provider(
                    client=client,
                    provider=provider,
                    cluster_id=cluster_id,
                    url=urls[provider],
                    timeout=timeout,
                    chaos=chaos,
                )
            except (
                ProviderTimeoutError,
                CorruptedPayloadError,
                NetworkPeeringError,
            ) as exc:
                errors.append(exc)
            else:
                results.append(result)

        async with asyncio.TaskGroup() as task_group:
            for provider in providers:
                task_group.create_task(run_provider(provider))

    if errors:
        raise ExceptionGroup(
            "Fallos concurrentes de telemetría.",
            errors,
        )

    return results

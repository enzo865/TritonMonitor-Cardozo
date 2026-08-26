"""
Validación de argumentos de línea de comandos de TritonMonitor.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence

SUPPORTED_PROVIDERS = ("AWS", "Azure", "GCP")
SUPPORTED_MODES = ("nominal", "debug", "emergency")

CLUSTER_PATTERN = re.compile(
    r"^cluster-[a-z]+(?:-[a-z0-9]+)*-\d+$",
)


def validate_timeout(value: str) -> float:
    """
    Valida que el timeout esté entre 0.1 y 5.0 segundos.

    Args:
        value: Valor recibido desde argparse.

    Returns:
        El timeout convertido a float.

    Raises:
        argparse.ArgumentTypeError: Si el valor no es numérico o está
            fuera del rango permitido.
    """
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("el timeout debe ser un número.") from exc

    if not 0.1 <= timeout <= 5.0:
        raise argparse.ArgumentTypeError(
            "el timeout debe estar entre 0.1 y 5.0 segundos."
        )

    return timeout


def validate_cluster_id(value: str) -> str:
    """
    Valida el formato del identificador de clúster.

    El formato esperado es:
        cluster-<region>-<numero>

    Ejemplo:
        cluster-us-east-01
    """
    if not CLUSTER_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "el ID de clúster debe seguir el formato "
            "'cluster-<region>-<numero>', por ejemplo "
            "'cluster-us-east-01'."
        )

    return value


def build_parser() -> argparse.ArgumentParser:
    """
    Construye el parser oficial de TritonMonitor.
    """
    parser = argparse.ArgumentParser(
        prog="triton-monitor",
        description=("Monitor CLI asíncrono de telemetría de Triton Cloud Services."),
    )

    parser.add_argument(
        "providers",
        nargs="+",
        choices=SUPPORTED_PROVIDERS,
        metavar="PROVEEDOR",
        help="Proveedores a consultar: AWS, Azure o GCP.",
    )

    parser.add_argument(
        "-c",
        "--cluster",
        type=validate_cluster_id,
        default="cluster-us-east-01",
        help=("Identificador del clúster. Formato: cluster-<region>-<numero>."),
    )

    parser.add_argument(
        "-t",
        "--timeout",
        type=validate_timeout,
        default=3.0,
        help="Timeout en segundos. Rango permitido: 0.1-5.0.",
    )

    parser.add_argument(
        "--chaos",
        action="store_true",
        help="Activa la inyección controlada de fallos de red.",
    )

    parser.add_argument(
        "--mode",
        choices=SUPPORTED_MODES,
        default="nominal",
        help=("Modo operativo: nominal, debug o emergency. Por defecto: nominal."),
    )

    output_group = parser.add_mutually_exclusive_group()

    output_group.add_argument(
        "--quiet",
        action="store_true",
        help="Desactiva el reporte nominal en consola.",
    )

    output_group.add_argument(
        "--verbose",
        action="store_true",
        help="Muestra información detallada en consola.",
    )

    return parser


def parse_args(
    argv: Sequence[str] | None = None,
) -> argparse.Namespace:
    """
    Analiza y valida los argumentos de línea de comandos.
    """
    parser = build_parser()
    return parser.parse_args(argv)

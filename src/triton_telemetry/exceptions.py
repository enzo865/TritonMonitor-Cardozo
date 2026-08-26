"""
Excepciones semánticas de TritonMonitor.
"""


class TritonError(Exception):
    """Excepción base para errores propios de TritonMonitor."""


class ProviderTimeoutError(TritonError):
    """Indica que una consulta a un proveedor excedió el timeout."""


class CorruptedPayloadError(TritonError):
    """Indica una respuesta HTTP o un payload no válido."""


class NetworkPeeringError(TritonError):
    """Indica un fallo de red, DNS o resolución del proveedor."""

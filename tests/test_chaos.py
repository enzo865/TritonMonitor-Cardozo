"""
Pruebas de resiliencia y caos para TritonMonitor.

Las pruebas verifican que los fallos de timeout y resolución de hosts
se transformen correctamente en las excepciones semánticas de Triton.
"""

from __future__ import annotations

import asyncio
import unittest

import httpx

from triton_telemetry import core
from triton_telemetry.exceptions import (
    NetworkPeeringError,
    ProviderTimeoutError,
)


class ChaosIntegrationTests(unittest.TestCase):
    """Suite de pruebas de resiliencia de TritonMonitor."""

    def test_concurrent_timeout(self) -> None:
        """Verifica un timeout real contra httpbin."""

        async def run_test() -> None:
            original_urls = core.CHAOS_URLS.copy()

            try:
                core.CHAOS_URLS.update(
                    {
                        "AWS": "https://httpbin.org/delay/3",
                        "GCP": "https://httpbin.org/delay/3",
                    }
                )

                with self.assertRaises(ExceptionGroup) as context:
                    await core.scan_all_providers(
                        providers=["AWS", "GCP"],
                        cluster_id="cluster-us-east-01",
                        timeout=0.1,
                        chaos=True,
                    )

                group = context.exception

                timeout_errors = [
                    error
                    for error in group.exceptions
                    if isinstance(error, ProviderTimeoutError)
                ]

                self.assertEqual(
                    len(timeout_errors),
                    2,
                    "Se esperaban dos ProviderTimeoutError.",
                )

                for error in timeout_errors:
                    self.assertTrue(error.__cause__)
                    self.assertIsInstance(
                        error.__cause__,
                        httpx.TimeoutException,
                    )

                    notes = getattr(
                        error,
                        "__notes__",
                        [],
                    )

                    self.assertTrue(notes)

            finally:
                core.CHAOS_URLS.clear()
                core.CHAOS_URLS.update(original_urls)

        asyncio.run(run_test())

    def test_network_peering_failure(self) -> None:
        """Verifica la transformación de un fallo de resolución de host."""

        async def run_test() -> None:
            original_urls = core.CHAOS_URLS.copy()

            try:
                core.CHAOS_URLS.update(
                    {
                        "AWS": "https://host-inexistente-triton.invalid/",
                        "GCP": "https://host-inexistente-triton.invalid/",
                    }
                )

                with self.assertRaises(ExceptionGroup) as context:
                    await core.scan_all_providers(
                        providers=["AWS", "GCP"],
                        cluster_id="cluster-us-west-02",
                        timeout=1.0,
                        chaos=True,
                    )

                group = context.exception

                peering_errors = [
                    error
                    for error in group.exceptions
                    if isinstance(error, NetworkPeeringError)
                ]

                self.assertEqual(
                    len(peering_errors),
                    2,
                    "Se esperaban dos NetworkPeeringError.",
                )

                for error in peering_errors:
                    self.assertTrue(error.__cause__)
                    self.assertIsInstance(
                        error.__cause__,
                        httpx.RequestError,
                    )

                    notes = getattr(
                        error,
                        "__notes__",
                        [],
                    )

                    self.assertTrue(notes)

            finally:
                core.CHAOS_URLS.clear()
                core.CHAOS_URLS.update(original_urls)


if __name__ == "__main__":
    unittest.main()

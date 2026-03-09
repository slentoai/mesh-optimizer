"""Connection-pooled HTTP client with retry and circuit breaker."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)


class MeshClient:
    """Shared aiohttp client with exponential backoff retry and circuit breaker.

    Usage::

        async with MeshClient() as client:
            status, data = await client.get("https://controller:8401/health")
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        circuit_threshold: int = 5,
        circuit_reset_s: float = 30.0,
        timeout: float = 10.0,
        ssl_context=None,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.circuit_threshold = circuit_threshold
        self.circuit_reset_s = circuit_reset_s
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.ssl_context = ssl_context
        self._session: Optional[aiohttp.ClientSession] = None
        self._circuits: Dict[str, Tuple[int, float]] = {}

    async def __aenter__(self) -> MeshClient:
        connector = None
        if self.ssl_context is not None:
            connector = aiohttp.TCPConnector(ssl=self.ssl_context)
        self._session = aiohttp.ClientSession(
            timeout=self.timeout,
            connector=connector,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _host_from_url(self, url: str) -> str:
        """Extract host:port from URL for circuit breaker tracking."""
        after_scheme = url.split("://", 1)[-1]
        return after_scheme.split("/", 1)[0]

    def _is_circuit_open(self, host: str) -> bool:
        """Check if circuit breaker is open (should skip requests)."""
        if host not in self._circuits:
            return False
        failures, last_time = self._circuits[host]
        if failures >= self.circuit_threshold:
            if time.monotonic() - last_time < self.circuit_reset_s:
                return True
            self._circuits[host] = (0, 0.0)
        return False

    def _record_failure(self, host: str) -> None:
        failures, _ = self._circuits.get(host, (0, 0.0))
        self._circuits[host] = (failures + 1, time.monotonic())

    def _record_success(self, host: str) -> None:
        if host in self._circuits:
            self._circuits[host] = (0, 0.0)

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = None
            if self.ssl_context is not None:
                connector = aiohttp.TCPConnector(ssl=self.ssl_context)
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                connector=connector,
            )
        return self._session

    async def _request(
        self, method: str, url: str, **kwargs
    ) -> Tuple[int, dict]:
        """Execute HTTP request with retry and circuit breaker."""
        host = self._host_from_url(url)

        if self._is_circuit_open(host):
            logger.debug("Circuit open for %s, skipping request", host)
            return 503, {"error": f"Circuit breaker open for {host}"}

        session = await self._ensure_session()
        last_exc = None

        for attempt in range(self.max_retries):
            try:
                async with session.request(method, url, **kwargs) as resp:
                    try:
                        data = await resp.json()
                    except Exception:
                        data = {"text": await resp.text()}
                    self._record_success(host)
                    return resp.status, data
            except Exception as e:
                last_exc = e
                self._record_failure(host)
                if attempt < self.max_retries - 1:
                    delay = min(
                        self.base_delay * (2 ** attempt),
                        self.max_delay,
                    )
                    logger.debug(
                        "Request to %s failed (attempt %d/%d): %s, retrying in %.1fs",
                        url, attempt + 1, self.max_retries, e, delay,
                    )
                    await asyncio.sleep(delay)

        logger.warning(
            "Request to %s failed after %d attempts: %s",
            url, self.max_retries, last_exc,
        )
        return 503, {"error": str(last_exc)}

    async def get(self, url: str, **kwargs) -> Tuple[int, dict]:
        return await self._request("GET", url, **kwargs)

    async def post(self, url: str, json: Any = None, **kwargs) -> Tuple[int, dict]:
        return await self._request("POST", url, json=json, **kwargs)

    async def delete(self, url: str, **kwargs) -> Tuple[int, dict]:
        return await self._request("DELETE", url, **kwargs)

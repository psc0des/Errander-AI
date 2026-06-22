"""Prometheus HTTP adapter — Layer A read-only data source.

Queries the standard Prometheus instant query API for node_exporter metrics.
Returns human-readable strings per VM; empty list on any error.

Never blocks probe or OperatorAssistant — all failures are best-effort.
Disabled when prometheus_base_url is empty (the default).
"""

from __future__ import annotations

import logging

import aiohttp

logger = logging.getLogger(__name__)

# Short timeout so a slow/unreachable Prometheus never stalls a probe run.
_TIMEOUT = aiohttp.ClientTimeout(total=5)


class PrometheusClient:
    """Read-only Prometheus instant query client.

    Queries standard node_exporter metrics per VM by matching the Prometheus
    `instance` label against the VM's IP / hostname from inventory.

    All query failures are silently swallowed — the caller always gets a
    list[str] (possibly empty), never an exception.
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def fetch_vm_metrics(self, host: str) -> list[str]:
        """Return human-readable metric strings for one VM.

        Matches `instance=~"HOST:.*"` so it works regardless of exporter port.

        Returns [] when Prometheus is unreachable, the metric is absent, or
        any other error occurs.
        """
        label = f'instance=~"{host}:.*"'
        queries: list[tuple[str, str]] = [
            (
                f"100 - (avg(rate(node_cpu_seconds_total{{mode='idle',{label}}}[5m])) * 100)",
                "CPU (5m): {:.1f}%",
            ),
            (
                f"(1 - node_memory_MemAvailable_bytes{{{label}}}"
                f" / node_memory_MemTotal_bytes{{{label}}}) * 100",
                "Memory: {:.1f}%",
            ),
            (
                f"node_load5{{{label}}}",
                "Load(5m): {:.2f}",
            ),
        ]
        results: list[str] = []
        for promql, fmt in queries:
            value = await self._query_instant(promql)
            if value is not None:
                results.append(fmt.format(value))
        return results

    async def _query_instant(self, promql: str) -> float | None:
        """Execute one instant PromQL query. Returns first result value or None."""
        try:
            session = await self._get_session()
            url = f"{self._base_url}/api/v1/query"
            async with session.get(url, params={"query": promql}, timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    logger.debug("Prometheus returned HTTP %d for query", resp.status)
                    return None
                raw: object = await resp.json()
                if not isinstance(raw, dict):
                    return None
                data_block = raw.get("data")
                if not isinstance(data_block, dict):
                    return None
                rows = data_block.get("result")
                if not isinstance(rows, list) or not rows:
                    return None
                first = rows[0]
                if isinstance(first, dict):
                    value_pair = first.get("value")
                    if isinstance(value_pair, list) and len(value_pair) >= 2:
                        return float(str(value_pair[1]))
                return None
        except Exception as exc:
            logger.debug("Prometheus query failed: %s", exc)
            return None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the underlying aiohttp session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()

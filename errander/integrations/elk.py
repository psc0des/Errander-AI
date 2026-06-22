"""ELK/Elasticsearch adapter — Layer A read-only log source.

Queries the Elasticsearch search API for recent ERROR/WARN log entries
per VM. Returns human-readable error pattern strings.

Disabled when elk_base_url is empty (the default).
All failures are best-effort — never blocks investigation.

Supports Elastic Common Schema (ECS) format as used by Filebeat 7+/8.
Falls back gracefully on non-ECS index schemas.
"""

from __future__ import annotations

import logging

import aiohttp

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=5)


class ElkClient:
    """Read-only Elasticsearch client for Layer A log investigation.

    Queries error/warn log events per VM using ECS field names (host.name,
    agent.hostname, beat.hostname). Falls back gracefully when message.keyword
    is not mapped.

    All query failures are silently swallowed — the caller always gets a
    list[str] (possibly empty), never an exception.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        index_pattern: str = "filebeat-*,logstash-*",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._index_pattern = index_pattern
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"ApiKey {self._api_key}"
            self._session = aiohttp.ClientSession(headers=headers, timeout=_TIMEOUT)
        return self._session

    async def fetch_vm_errors(
        self,
        host: str,
        window_hours: int = 24,
        max_patterns: int = 5,
    ) -> list[str]:
        """Return top error/warn patterns for one host in the last window_hours.

        Returns [] when ELK unreachable, index absent, or any parse error.
        Each string is like: "[ERROR] 47x Cannot connect to database"
        """
        query = {
            "size": 0,
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"@timestamp": {"gte": f"now-{window_hours}h"}}},
                        {"terms": {"log.level": [
                            "error", "warn", "ERROR", "WARN", "warning", "WARNING",
                        ]}},
                        {"bool": {
                            "should": [
                                {"term": {"host.name": host}},
                                {"term": {"agent.hostname": host}},
                                {"term": {"beat.hostname": host}},
                            ],
                            "minimum_should_match": 1,
                        }},
                    ],
                },
            },
            "aggs": {
                "top_errors": {
                    "terms": {
                        "field": "message.keyword",
                        "size": max_patterns,
                        "min_doc_count": 1,
                    },
                },
            },
        }

        url = f"{self._base_url}/{self._index_pattern}/_search"
        try:
            session = self._get_session()
            async with session.post(url, json=query) as resp:
                if resp.status >= 400:
                    logger.debug(
                        "ELK query returned HTTP %d for host %s", resp.status, host
                    )
                    return []
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug("ELK fetch failed for host %s: %s", host, exc)
            return []

        try:
            buckets = data["aggregations"]["top_errors"]["buckets"]
        except (KeyError, TypeError):
            # message.keyword not available — fall back to hit count
            try:
                total = data.get("hits", {}).get("total", {})
                count = total.get("value", 0) if isinstance(total, dict) else int(total)
                if count:
                    return [
                        f"ELK returned {count} error events "
                        f"(message.keyword not indexed — check ELK mapping)"
                    ]
            except Exception:
                pass
            return []

        results: list[str] = []
        for bucket in buckets:
            try:
                msg = str(bucket.get("key", ""))[:120]
                count = int(bucket.get("doc_count", 0))
                if msg:
                    results.append(f"[ERROR] {count}x {msg}")
            except Exception:
                continue

        return results

    async def search(
        self,
        host: str,
        query_terms: list[str],
        window_hours: int = 24,
        level: str | None = None,
        max_results: int = 20,
    ) -> list[str]:
        """Arbitrary-terms read-only log search (investigation-agent tool path).

        Unlike fetch_vm_errors (one fixed aggregation), this lets the caller
        supply free-text search terms. The query is always built
        programmatically against the configured index pattern's `_search`
        endpoint — caller input only ever shapes `bool.filter`/`bool.should`
        clause *content* (query_terms, level, host), never the URL, the
        index, or the request method. Returns [] on any error; never raises.

        Args:
            host: VM hostname to scope the search to (matched the same way
                as fetch_vm_errors: host.name / agent.hostname / beat.hostname).
            query_terms: Free-text terms to match against the log message.
            window_hours: Trailing time window in hours.
            level: Optional log level filter (e.g. "error"); None = any level.
            max_results: Caps the number of hits returned.
        """
        max_results = min(max_results, 50)
        must: list[dict[str, object]] = []
        if query_terms:
            must.append({
                "multi_match": {
                    "query": " ".join(str(t) for t in query_terms[:20]),
                    "fields": ["message", "message.keyword"],
                },
            })
        filter_clauses: list[dict[str, object]] = [
            {"range": {"@timestamp": {"gte": f"now-{window_hours}h"}}},
            {"bool": {
                "should": [
                    {"term": {"host.name": host}},
                    {"term": {"agent.hostname": host}},
                    {"term": {"beat.hostname": host}},
                ],
                "minimum_should_match": 1,
            }},
        ]
        if level:
            filter_clauses.append({"terms": {"log.level": [level.lower(), level.upper()]}})

        query: dict[str, object] = {
            "size": max_results,
            "query": {"bool": {"must": must, "filter": filter_clauses}},
            "sort": [{"@timestamp": {"order": "desc"}}],
        }

        url = f"{self._base_url}/{self._index_pattern}/_search"
        try:
            session = self._get_session()
            async with session.post(url, json=query) as resp:
                if resp.status >= 400:
                    logger.debug("ELK search() returned HTTP %d for host %s", resp.status, host)
                    return []
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug("ELK search() failed for host %s: %s", host, exc)
            return []

        try:
            hits = data["hits"]["hits"]
        except (KeyError, TypeError):
            return []

        results: list[str] = []
        for hit in hits:
            try:
                source = hit.get("_source", {})
                ts = source.get("@timestamp", "")
                msg = str(source.get("message", ""))[:200]
                if msg:
                    results.append(f"[{ts}] {msg}")
            except Exception:
                continue
        return results

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

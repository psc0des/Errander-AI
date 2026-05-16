"""Tests for per-environment Prometheus/ELK URL resolution in main.py.

Resolution rules:
- env.prometheus_url takes priority over settings.prometheus_base_url
- env.elk_url / elk_api_key / elk_index_pattern each individually override
  the corresponding global settings value
- env=None (e.g. --ask without --env) falls through to global settings only
"""

from __future__ import annotations

from errander.config.schema import EnvironmentSchema, TargetSchema
from errander.config.settings import Settings
from errander.main import _resolve_elk_config, _resolve_prometheus_url


def _make_target() -> TargetSchema:
    return TargetSchema(host="10.0.0.1", name="vm-01", os_family="ubuntu")


def _make_env(**kwargs: object) -> EnvironmentSchema:
    return EnvironmentSchema(targets=[_make_target()], **kwargs)  # type: ignore[arg-type]


def _make_settings(**kwargs: object) -> Settings:
    return Settings(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _resolve_prometheus_url
# ---------------------------------------------------------------------------

class TestResolvePrometheusUrl:
    def test_env_override_wins(self) -> None:
        env = _make_env(prometheus_url="http://prom-prod:9090")
        settings = _make_settings(prometheus_base_url="http://prom-global:9090")
        assert _resolve_prometheus_url(env, settings) == "http://prom-prod:9090"

    def test_falls_back_to_global_when_env_unset(self) -> None:
        env = _make_env()  # prometheus_url=None
        settings = _make_settings(prometheus_base_url="http://prom-global:9090")
        assert _resolve_prometheus_url(env, settings) == "http://prom-global:9090"

    def test_env_none_uses_global(self) -> None:
        settings = _make_settings(prometheus_base_url="http://prom-global:9090")
        assert _resolve_prometheus_url(None, settings) == "http://prom-global:9090"

    def test_both_empty_returns_empty(self) -> None:
        env = _make_env()
        settings = _make_settings(prometheus_base_url="")
        assert _resolve_prometheus_url(env, settings) == ""

    def test_env_override_empty_string_falls_back(self) -> None:
        """Empty string is falsy — treated as 'not set', global wins."""
        env = _make_env(prometheus_url="")
        settings = _make_settings(prometheus_base_url="http://prom-global:9090")
        assert _resolve_prometheus_url(env, settings) == "http://prom-global:9090"


# ---------------------------------------------------------------------------
# _resolve_elk_config
# ---------------------------------------------------------------------------

class TestResolveElkConfig:
    def test_elk_url_env_override_wins(self) -> None:
        env = _make_env(elk_url="http://elk-prod:9200")
        settings = _make_settings(elk_base_url="http://elk-global:9200")
        url, _, _ = _resolve_elk_config(env, settings)
        assert url == "http://elk-prod:9200"

    def test_elk_url_falls_back_to_global(self) -> None:
        env = _make_env()
        settings = _make_settings(elk_base_url="http://elk-global:9200")
        url, _, _ = _resolve_elk_config(env, settings)
        assert url == "http://elk-global:9200"

    def test_elk_api_key_env_override_wins(self) -> None:
        env = _make_env(elk_url="http://elk-prod:9200", elk_api_key="env-key")
        settings = _make_settings(elk_base_url="http://elk-global:9200", elk_api_key="global-key")
        _, key, _ = _resolve_elk_config(env, settings)
        assert key == "env-key"

    def test_elk_api_key_falls_back_to_global(self) -> None:
        env = _make_env(elk_url="http://elk-prod:9200")  # elk_api_key=None
        settings = _make_settings(elk_base_url="http://elk-global:9200", elk_api_key="global-key")
        _, key, _ = _resolve_elk_config(env, settings)
        assert key == "global-key"

    def test_elk_index_pattern_env_override_wins(self) -> None:
        env = _make_env(elk_url="http://elk-prod:9200", elk_index_pattern="prod-logs-*")
        settings = _make_settings(elk_base_url="http://elk-global:9200", elk_index_pattern="filebeat-*")
        _, _, idx = _resolve_elk_config(env, settings)
        assert idx == "prod-logs-*"

    def test_elk_index_pattern_falls_back_to_global(self) -> None:
        env = _make_env(elk_url="http://elk-prod:9200")
        settings = _make_settings(
            elk_base_url="http://elk-global:9200",
            elk_index_pattern="filebeat-*,logstash-*",
        )
        _, _, idx = _resolve_elk_config(env, settings)
        assert idx == "filebeat-*,logstash-*"

    def test_partial_override_only_api_key(self) -> None:
        """Env overrides api_key but not url — both resolve correctly."""
        env = _make_env(elk_api_key="env-key")
        settings = _make_settings(
            elk_base_url="http://elk-global:9200",
            elk_api_key="global-key",
            elk_index_pattern="filebeat-*",
        )
        url, key, idx = _resolve_elk_config(env, settings)
        assert url == "http://elk-global:9200"
        assert key == "env-key"
        assert idx == "filebeat-*"

    def test_env_none_uses_all_global(self) -> None:
        settings = _make_settings(
            elk_base_url="http://elk-global:9200",
            elk_api_key="global-key",
            elk_index_pattern="filebeat-*",
        )
        url, key, idx = _resolve_elk_config(None, settings)
        assert url == "http://elk-global:9200"
        assert key == "global-key"
        assert idx == "filebeat-*"

    def test_all_env_overrides_applied(self) -> None:
        env = _make_env(
            elk_url="http://elk-staging:9200",
            elk_api_key="staging-key",
            elk_index_pattern="staging-logs-*",
        )
        settings = _make_settings(
            elk_base_url="http://elk-global:9200",
            elk_api_key="global-key",
            elk_index_pattern="filebeat-*",
        )
        url, key, idx = _resolve_elk_config(env, settings)
        assert url == "http://elk-staging:9200"
        assert key == "staging-key"
        assert idx == "staging-logs-*"

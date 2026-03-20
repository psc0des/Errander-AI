"""Entry point for the AutoMaint agent.

Long-lived process that:
1. Loads configuration and validates inventory
2. Starts APScheduler for maintenance window scheduling
3. Starts Slack poller for approval reactions and slash commands
4. Exposes Prometheus /metrics and /health endpoints
5. Runs LangGraph batch orchestrator on schedule or on-demand
"""

from __future__ import annotations


def main() -> None:
    """Start the AutoMaint agent process."""
    raise NotImplementedError("Agent entry point not yet implemented")


if __name__ == "__main__":
    main()

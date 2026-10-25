"""Telemetria pelo servidor MCP do SigNoz, só com ferramentas de leitura de uma lista fechada."""

from datetime import datetime

from grolla.application.investigation import Signal, ToolResult


class SignozTelemetry:
    def __init__(self, mcp_url: str, base_url: str, timeout_seconds: float = 20.0) -> None:
        self._mcp_url = mcp_url
        self._base_url = base_url  # links "ver no SigNoz"
        self._timeout_seconds = timeout_seconds

    async def query_metric(
        self, service: str, signal: Signal, start: datetime, end: datetime
    ) -> ToolResult:
        raise NotImplementedError

    async def search_logs(
        self, service: str, text: str | None, start: datetime, end: datetime
    ) -> ToolResult:
        raise NotImplementedError

    async def find_traces(
        self, service: str, only_errors: bool, start: datetime, end: datetime
    ) -> ToolResult:
        raise NotImplementedError

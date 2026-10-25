"""Telemetria pelo servidor MCP do SigNoz, só com ferramentas de leitura de uma lista fechada.

O servidor (`signoz-mcp-server`) roda em container ao lado do agente, com a API key dele no
próprio ambiente: o adapter não manda chave. Cada chamada abre uma sessão MCP curta, com timeout
no todo; qualquer falha vira `ToolUnavailableError`.

Os sinais neutros viram agregações sobre os spans do serviço (`signoz_aggregate_traces`), os
logs de erro vêm de `signoz_search_logs` e os traces de `signoz_search_traces` (com erro) ou
`signoz_get_service_top_operations` (lentos). As respostas são do Query Builder v5 do SigNoz.
"""

import asyncio
import json
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from grolla.application.investigation import Signal, ToolResult, ToolUnavailableError

# Lista fechada: só leitura. Qualquer outra ferramenta (criar dashboard, apagar alerta...) é
# recusada antes de chegar ao servidor, mesmo que a API key permita.
READ_TOOLS = frozenset(
    {
        "signoz_aggregate_traces",
        "signoz_search_logs",
        "signoz_search_traces",
        "signoz_get_service_top_operations",
    }
)

SERIES_POINTS = 6  # pontos da série devolvida ao modelo
MIN_STEP_SECONDS = 60
ROW_LIMIT = 100
TOP = 3
MESSAGE_CHARS = 160
ERROR_SEVERITY = "severity_number >= 17"  # ERROR e FATAL na escala do OpenTelemetry
NANOS_PER_MS = 1_000_000

_SIGNAL_QUERIES: dict[Signal, dict[str, str]] = {
    "taxa_de_erro": {"aggregation": "count", "groupBy": "has_error"},
    "latencia_p99": {"aggregation": "p99", "aggregateOn": "duration_nano"},
    "vazao": {"aggregation": "rate"},
}
_SIGNAL_LABELS: dict[Signal, str] = {
    "taxa_de_erro": "taxa de erro",
    "latencia_p99": "latência p99",
    "vazao": "vazão",
}


class SignozTelemetry:
    def __init__(
        self,
        mcp_url: str,
        base_url: str,
        timeout_seconds: float = 20.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._mcp_url = mcp_url
        self._base_url = base_url.rstrip("/")  # links "ver no SigNoz"
        self._timeout_seconds = timeout_seconds
        self._transport = transport  # testes injetam o servidor MCP em processo

    async def query_metric(
        self, service: str, signal: Signal, start: datetime, end: datetime
    ) -> ToolResult:
        step = max(MIN_STEP_SECONDS, int((end - start).total_seconds()) // SERIES_POINTS)
        arguments = {
            **_SIGNAL_QUERIES[signal],
            "service": service,
            "requestType": "time_series",
            "stepInterval": str(step),
            **_window(start, end),
        }
        envelope = _parse(
            _TimeSeriesEnvelope, await self.call("signoz_aggregate_traces", arguments)
        )
        series = [
            s
            for result in envelope.results()
            for aggregation in result.aggregations or []
            for s in aggregation.series or []
        ]
        points = _error_rate(series) if signal == "taxa_de_erro" else _first(series)
        label = f"{_SIGNAL_LABELS[signal]} de {service} entre {_hhmm(start)} e {_hhmm(end)}"
        return ToolResult(_series_summary(label, signal, points, step), self._service_url(service))

    async def search_logs(
        self, service: str, text: str | None, start: datetime, end: datetime
    ) -> ToolResult:
        arguments = {
            "service": service,
            "filter": ERROR_SEVERITY,
            "limit": str(ROW_LIMIT),
            **_window(start, end),
        }
        if text:
            arguments["searchText"] = text
        envelope = _parse(_RawEnvelope, await self.call("signoz_search_logs", arguments))
        rows = [row for result in envelope.results() for row in result.rows or []]
        where = f"de {service} entre {_hhmm(start)} e {_hhmm(end)}"
        if text:
            where += f' com o texto "{text}"'
        url = f"{self._base_url}/logs/logs-explorer"
        if not rows:
            return ToolResult(f"nenhum log de erro {where}", url)
        messages = Counter(_message(str(row.data.get("body", ""))) for row in rows)
        top = "; ".join(f'{count}x "{message}"' for message, count in messages.most_common(TOP))
        summary = f"{_count(len(rows))} logs de erro {where}. Mais frequentes: {top}"
        return ToolResult(summary, url)

    async def find_traces(
        self, service: str, only_errors: bool, start: datetime, end: datetime
    ) -> ToolResult:
        if only_errors:
            return await self._error_traces(service, start, end)
        return await self._slow_operations(service, start, end)

    async def call(self, tool: str, arguments: Mapping[str, Any]) -> Any:
        """Chama uma ferramenta de leitura e devolve o JSON da resposta.

        Ferramenta fora da lista de leitura é recusada sem chamar o servidor.
        """
        if tool not in READ_TOOLS:
            raise ToolUnavailableError(f"ferramenta fora da lista de leitura: {tool}")
        try:
            async with (
                asyncio.timeout(self._timeout_seconds),
                httpx2.AsyncClient(
                    transport=self._transport, timeout=self._timeout_seconds
                ) as http,
                # "legacy": o servidor do SigNoz fala o handshake de initialize, sem discover.
                Client(
                    streamable_http_client(self._mcp_url, http_client=http),
                    mode="legacy",
                    cache=None,
                ) as client,
            ):
                result = await client.call_tool(tool, dict(arguments))
        except TimeoutError as error:
            raise ToolUnavailableError(
                f"{tool}: sem resposta em {self._timeout_seconds:g} s"
            ) from error
        except Exception as error:  # rede, protocolo, servidor: a fonte está indisponível
            raise ToolUnavailableError(f"{tool}: {type(error).__name__}") from error
        text = next((c.text for c in result.content if isinstance(c, TextContent)), "")
        if result.is_error:
            raise ToolUnavailableError(f"{tool}: {text[:MESSAGE_CHARS]}")
        try:
            return json.loads(text)
        except ValueError as error:
            raise ToolUnavailableError(f"{tool}: resposta fora de JSON") from error

    async def _error_traces(self, service: str, start: datetime, end: datetime) -> ToolResult:
        arguments = {
            "service": service,
            "error": True,
            "limit": str(ROW_LIMIT),
            **_window(start, end),
        }
        envelope = _parse(_RawEnvelope, await self.call("signoz_search_traces", arguments))
        rows = [row for result in envelope.results() for row in result.rows or []]
        where = f"de {service} entre {_hhmm(start)} e {_hhmm(end)}"
        if not rows:
            return ToolResult(f"nenhum span com erro {where}", f"{self._base_url}/traces-explorer")
        groups = Counter(
            (str(row.data.get("name", "?")), _message(str(row.data.get("status_message") or "")))
            for row in rows
        )
        top = "; ".join(
            f"{count}x {name}" + (f' ("{status}")' if status else "")
            for (name, status), count in groups.most_common(TOP)
        )
        trace_id = str(rows[0].data.get("trace_id", ""))
        url = f"{self._base_url}/trace/{quote(trace_id)}" if trace_id else None
        return ToolResult(f"{_count(len(rows))} spans com erro {where}: {top}", url)

    async def _slow_operations(self, service: str, start: datetime, end: datetime) -> ToolResult:
        arguments = {"service": service, **_window(start, end)}
        answer = _parse(
            _OPERATIONS, await self.call("signoz_get_service_top_operations", arguments)
        )
        operations = answer.data if isinstance(answer, _WrappedOperations) else answer
        where = f"de {service} entre {_hhmm(start)} e {_hhmm(end)}"
        url = self._service_url(service)
        if not operations:
            return ToolResult(f"nenhuma operação com traces {where}", url)
        slowest = sorted(operations, key=lambda o: o.p99, reverse=True)[:TOP]
        top = "; ".join(
            f"{o.name} {_ms(o.p99)} ({o.num_calls} chamadas, {o.error_count} com erro)"
            for o in slowest
        )
        return ToolResult(f"operações mais lentas (p99) {where}: {top}", url)

    def _service_url(self, service: str) -> str:
        return f"{self._base_url}/services/{quote(service)}"


# ---- Respostas do Query Builder v5: {"status", "data": {"type", "data": {"results": [...]}}}


class _Key(BaseModel):
    name: str


class _Label(BaseModel):
    key: _Key
    value: Any = None


class _Point(BaseModel):
    timestamp: int  # ms
    value: float


class _Series(BaseModel):
    labels: list[_Label] | None = None
    values: list[_Point] | None = None


class _Aggregation(BaseModel):
    series: list[_Series] | None = None


class _TimeSeriesResult(BaseModel):
    aggregations: list[_Aggregation] | None = None


class _Row(BaseModel):
    data: dict[str, Any]


class _RawResult(BaseModel):
    rows: list[_Row] | None = None


class _Results[T: BaseModel](BaseModel):
    results: list[T] | None = None


class _Data[T: BaseModel](BaseModel):
    data: _Results[T]


class _Envelope[T: BaseModel](BaseModel):
    data: _Data[T]

    def results(self) -> list[T]:
        return self.data.data.results or []


_TimeSeriesEnvelope = _Envelope[_TimeSeriesResult]
_RawEnvelope = _Envelope[_RawResult]


class _Operation(BaseModel):
    name: str
    p99: float  # ns
    num_calls: int = Field(alias="numCalls")
    error_count: int = Field(default=0, alias="errorCount")


class _WrappedOperations(BaseModel):  # algumas versões embrulham a lista em {"data": [...]}
    data: list[_Operation] = []


_OPERATIONS = TypeAdapter(list[_Operation] | _WrappedOperations)


def _parse[T](model: type[T] | TypeAdapter[T], payload: Any) -> T:
    adapter = model if isinstance(model, TypeAdapter) else TypeAdapter(model)
    try:
        return adapter.validate_python(payload)
    except ValidationError as error:
        raise ToolUnavailableError("resposta do SigNoz em formato inesperado") from error


# ---- Séries e resumos


def _first(series: list[_Series]) -> list[tuple[int, float]]:
    if not series:
        return []
    return sorted((p.timestamp, p.value) for p in series[0].values or [])


def _error_rate(series: list[_Series]) -> list[tuple[int, float]]:
    """Contagens por `has_error` viram a porcentagem de spans com erro em cada intervalo."""
    totals: Counter[int] = Counter()
    errors: Counter[int] = Counter()
    for s in series:
        failed = any(
            label.key.name == "has_error" and str(label.value).lower() == "true"
            for label in s.labels or []
        )
        for point in s.values or []:
            totals[point.timestamp] += int(point.value)
            if failed:
                errors[point.timestamp] += int(point.value)
    return [(ts, 100 * errors[ts] / total) for ts, total in sorted(totals.items()) if total]


def _series_summary(label: str, signal: Signal, points: list[tuple[int, float]], step: int) -> str:
    if not points:
        return f"sem dados de {label}"
    peak_ts, peak = max(points, key=lambda p: p[1])
    peak_at = _hhmm(datetime.fromtimestamp(peak_ts / 1000, UTC))
    shown = " · ".join(_value(signal, v) for _, v in points[-SERIES_POINTS * 2 :])
    return (
        f"{label}: {_value(signal, points[0][1])} no início, {_value(signal, points[-1][1])} "
        f"no fim, máximo {_value(signal, peak)} às {peak_at}"
        f". Série a cada {step // 60} min: {shown}"
    )


def _value(signal: Signal, value: float) -> str:
    if signal == "taxa_de_erro":
        return f"{_decimal(value)}%"
    if signal == "latencia_p99":
        return _ms(value)
    return f"{_decimal(value)} chamadas/s"


def _ms(nanos: float) -> str:
    return f"{nanos / NANOS_PER_MS:,.0f} ms".replace(",", ".")


def _decimal(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _count(n: int) -> str:
    return f"{n}+" if n >= ROW_LIMIT else str(n)


def _message(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first[:MESSAGE_CHARS]


def _window(start: datetime, end: datetime) -> dict[str, str]:
    return {"start": str(int(start.timestamp() * 1000)), "end": str(int(end.timestamp() * 1000))}


def _hhmm(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%H:%M UTC")

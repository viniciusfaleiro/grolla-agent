"""Telemetria pelo servidor MCP do SigNoz, só com ferramentas de leitura de uma lista fechada.

O servidor (`signoz-mcp-server`) roda em container ao lado do agente, com a API key dele no
próprio ambiente: o adapter não manda chave. Cada chamada abre uma sessão MCP curta, com timeout
no todo; qualquer falha vira `ToolUnavailableError`.

Os sinais neutros viram agregações sobre os spans do serviço (`signoz_aggregate_traces`), os
logs WARN ou acima vêm de `signoz_search_logs` e os traces de `signoz_search_traces` (com erro) ou
`signoz_get_service_top_operations` (lentos). As respostas são do Query Builder v5 do SigNoz.

Para a memória: as regras de alerta vêm de `signoz_list_alert_rules`, com a condição de cada uma
por `signoz_get_alert` (a lista não a traz); os serviços ativos de `signoz_list_services`; e as
dependências de um serviço agregando os spans de saída (client e producer) por `peer.service`,
`db.system`/`db.name` e `server.address`.
"""

import asyncio
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from grolla.application.investigation import (
    NO_DEPENDENCIES,
    AlertRule,
    Signal,
    ToolResult,
    ToolUnavailableError,
)
from grolla.domain.incidents import UNKNOWN_SERVICE

# Lista fechada: só leitura. Qualquer outra ferramenta (criar dashboard, apagar alerta...) é
# recusada antes de chegar ao servidor, mesmo que a API key permita.
READ_TOOLS = frozenset(
    {
        "signoz_aggregate_traces",
        "signoz_search_logs",
        "signoz_search_traces",
        "signoz_get_service_top_operations",
        "signoz_list_alert_rules",
        "signoz_get_alert",
        "signoz_list_services",
    }
)

STEP_SECONDS = 60  # pontos de 1 min: o minuto da mudança se compara ao horário dos deploys
SERIES_POINTS = 10  # só os últimos pontos vão para o modelo, para economizar tokens
BASELINE_POINTS = 5  # o patamar do início da janela é a média dos primeiros pontos
CHANGE_FACTOR = 3.0  # mudança de patamar: mais de 3x (ou menos de 1/3) do patamar
CHANGE_POINTS = 5.0  # na taxa de erro: mais de 5 pontos percentuais
ROW_LIMIT = 100
TOP = 3
TOP_LOGS = 5
MESSAGE_CHARS = 160
WARN_SEVERITY = "severity_number >= 13"  # WARN e acima na escala do OpenTelemetry
NANOS_PER_MS = 1_000_000
PAGE_SIZE = 1000  # máximo das listagens do MCP; acima disso, pagina
MAX_DEPENDENCIES = 20
OUTGOING_SPANS = "kind_string IN ('Client', 'Producer')"  # spans de saída do serviço
DEPENDENCY_FIELDS = ("peer.service", "db.system", "db.name", "server.address")

# Variações da mesma mensagem viram uma só: identificadores, endereços e números longos.
_VARIABLE_PARTS = (
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
    re.compile(r"\b0x[0-9a-f]+\b", re.I),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"),
    re.compile(r"\d{4,}"),
)

_SIGNAL_QUERIES: dict[Signal, dict[str, str]] = {
    # Total e só os com erro, em duas consultas: agrupar por has_error não devolve a série dos
    # spans sem erro (o SigNoz real só agrupa os que têm o campo verdadeiro).
    "taxa_de_erro": {"aggregation": "count"},
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
        service_labels: Sequence[str] = ("service_name", "service.name"),
    ) -> None:
        self._mcp_url = mcp_url
        self._service_labels = tuple(service_labels)  # os do YAML: o serviço de cada regra
        self._base_url = base_url.rstrip("/")  # links "ver no SigNoz"
        self._timeout_seconds = timeout_seconds
        self._transport = transport  # testes injetam o servidor MCP em processo

    async def query_metric(
        self, service: str, signal: Signal, start: datetime, end: datetime
    ) -> ToolResult:
        arguments = {
            **_SIGNAL_QUERIES[signal],
            "service": service,
            "requestType": "time_series",
            "stepInterval": str(STEP_SECONDS),
            **_window(start, end),
        }
        series = await self._aggregate(arguments)
        if signal == "taxa_de_erro":
            points = _error_rate(series, await self._aggregate({**arguments, "error": True}))
        else:
            points = _first(series)
        label = f"{_SIGNAL_LABELS[signal]} de {service} entre {_hhmm(start)} e {_hhmm(end)}"
        return ToolResult(_series_summary(label, signal, points), self._service_url(service))

    async def search_logs(
        self, service: str, text: str | None, start: datetime, end: datetime
    ) -> ToolResult:
        arguments = {
            "service": service,
            "filter": WARN_SEVERITY,
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
            return ToolResult(f"nenhum log WARN ou acima {where}", url)
        groups: Counter[tuple[str, str]] = Counter()
        shown: dict[tuple[str, str], str] = {}  # a primeira mensagem de cada grupo, como veio
        for row in rows:
            message = _message(str(row.data.get("body", "")))
            level = str(row.data.get("severity_text") or "?").upper()
            key = (level, _normalized(message))
            groups[key] += 1
            shown.setdefault(key, message)
        top = "; ".join(
            f"{count}x [{level}] {shown[(level, normalized)]}"
            for (level, normalized), count in groups.most_common(TOP_LOGS)
        )
        summary = f"{_count(len(rows))} logs WARN ou acima {where}. Mais frequentes: {top}"
        return ToolResult(summary, url)

    async def find_traces(
        self, service: str, only_errors: bool, start: datetime, end: datetime
    ) -> ToolResult:
        if only_errors:
            return await self._error_traces(service, start, end)
        return await self._slow_operations(service, start, end)

    async def alert_rules(self) -> list[AlertRule]:
        """Regras ligadas (as desligadas nunca disparam), cada uma com a condição legível."""
        listed = await self._pages("signoz_list_alert_rules", {}, _RulePage)
        rules: list[AlertRule] = []
        for summary in listed:
            if summary.disabled:
                continue
            rule = summary
            if rule.condition is None:  # a lista não traz a condição: busca a definição
                answer = await self.call("signoz_get_alert", {"id": summary.rule_id})
                rule = _parse(_RuleAnswer, answer).data
            rules.append(self._rule(summary.rule_id, rule))
        return rules

    async def active_services(self, since: datetime) -> list[str]:
        window = _window(since, datetime.now(UTC))
        services = await self._pages("signoz_list_services", window, _ServicePage)
        return list(dict.fromkeys(s.service_name for s in services))

    async def dependencies(self, service: str, start: datetime, end: datetime) -> ToolResult:
        arguments = {
            "service": service,
            "filter": OUTGOING_SPANS,
            "groupBy": ", ".join(DEPENDENCY_FIELDS),
            "requestType": "scalar",
            "limit": str(MAX_DEPENDENCIES),
            **_window(start, end),
        }
        calls, errors, p99 = await asyncio.gather(
            self._scalar({**arguments, "aggregation": "count"}),
            self._scalar({**arguments, "aggregation": "count", "error": True}),
            self._scalar({**arguments, "aggregation": "p99", "aggregateOn": "duration_nano"}),
        )
        where = f"de {service} entre {_hhmm(start)} e {_hhmm(end)}"
        url = f"{self._base_url}/traces-explorer"
        seen = sorted(
            ((key, count) for key, count in calls.items() if count),
            key=lambda item: item[1],
            reverse=True,
        )
        if not seen:
            return ToolResult(f"{NO_DEPENDENCIES} ({where})", url)
        lines = "; ".join(
            f"{_dependency(key)}: {count:.0f} chamadas, "
            f"{_decimal(100 * errors.get(key, 0) / count)}% com erro"
            + (f", p99 {_ms(p99[key])}" if key in p99 else "")
            for key, count in seen
        )
        return ToolResult(f"{len(seen)} dependência(s) vistas nos traces {where}: {lines}", url)

    def _rule(self, rule_id: str, rule: "_Rule") -> AlertRule:
        labels = {key: str(value) for key, value in rule.labels.items()}
        service = next(
            (labels[label] for label in self._service_labels if labels.get(label)),
            UNKNOWN_SERVICE,
        )
        return AlertRule(
            name=rule.alert,
            condition=_condition(rule),
            severity=rule.severity or labels.get("severity", "?"),
            labels=labels,
            service=service,
            url=f"{self._base_url}/alerts/overview?ruleId={quote(rule_id)}",
        )

    async def _pages[T: BaseModel](
        self, tool: str, arguments: Mapping[str, str], page: "type[_Page[T]]"
    ) -> list[T]:
        """Todas as páginas de uma listagem, seguindo `pagination.nextOffset`."""
        found: list[T] = []
        offset = 0
        while True:
            answer = await self.call(
                tool, {**arguments, "limit": str(PAGE_SIZE), "offset": str(offset)}
            )
            current = _parse(page, answer)
            found += current.data
            more = current.pagination
            if more is None or not more.has_more or more.next_offset <= offset:
                return found
            offset = more.next_offset

    async def _scalar(self, arguments: Mapping[str, Any]) -> "dict[_DependencyKey, float]":
        """Valor da agregação por combinação dos campos de dependência."""
        envelope = _parse(_ScalarEnvelope, await self.call("signoz_aggregate_traces", arguments))
        values: dict[_DependencyKey, float] = {}
        for result in envelope.results():
            columns = result.columns or []
            names = [c.name for c in columns]
            value_at = next(
                (i for i, c in enumerate(columns) if c.column_type == "aggregation"),
                len(columns) - 1,
            )
            for row in result.data or []:
                cells = dict(zip(names, row, strict=False))
                key = tuple(_text(cells.get(name)) for name in DEPENDENCY_FIELDS)
                value = row[value_at] if 0 <= value_at < len(row) else None
                if isinstance(value, int | float):
                    values[key] = values.get(key, 0.0) + float(value)
        return values

    async def _aggregate(self, arguments: Mapping[str, Any]) -> "list[_Series]":
        envelope = _parse(
            _TimeSeriesEnvelope, await self.call("signoz_aggregate_traces", arguments)
        )
        return [
            s
            for result in envelope.results()
            for aggregation in result.aggregations or []
            for s in aggregation.series or []
        ]

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


class _Column(BaseModel):
    name: str
    column_type: str = Field(default="", alias="columnType")


class _ScalarResult(BaseModel):
    columns: list[_Column] | None = None
    data: list[list[Any]] | None = None


_TimeSeriesEnvelope = _Envelope[_TimeSeriesResult]
_ScalarEnvelope = _Envelope[_ScalarResult]
_DependencyKey = tuple[str | None, ...]  # valores de DEPENDENCY_FIELDS, na ordem
_RawEnvelope = _Envelope[_RawResult]


class _Operation(BaseModel):
    name: str
    p99: float  # ns
    num_calls: int = Field(alias="numCalls")
    error_count: int = Field(default=0, alias="errorCount")


class _WrappedOperations(BaseModel):  # algumas versões embrulham a lista em {"data": [...]}
    data: list[_Operation] = []


_OPERATIONS = TypeAdapter(list[_Operation] | _WrappedOperations)


# ---- Listagens paginadas: {"data": [...], "pagination": {"hasMore", "nextOffset", ...}}


class _Pagination(BaseModel):
    has_more: bool = Field(default=False, alias="hasMore")
    next_offset: int = Field(default=-1, alias="nextOffset")


class _Page[T: BaseModel](BaseModel):
    data: list[T] = []
    pagination: _Pagination | None = None


class _Rule(BaseModel):
    """Regra de alerta, resumida (lista) ou completa (`signoz_get_alert`)."""

    rule_id: str = Field(default="", alias="ruleId")
    alert: str
    severity: str | None = None
    disabled: bool = False
    labels: dict[str, Any] = Field(default_factory=dict[str, Any])
    annotations: dict[str, Any] = Field(default_factory=dict[str, Any])
    condition: dict[str, Any] | None = None
    eval_window: str | None = Field(default=None, alias="evalWindow")


class _RuleAnswer(BaseModel):
    data: _Rule


class _Service(BaseModel):
    service_name: str = Field(alias="serviceName")


_RulePage = _Page[_Rule]
_ServicePage = _Page[_Service]


def _parse[T](model: type[T] | TypeAdapter[T], payload: Any) -> T:
    adapter = model if isinstance(model, TypeAdapter) else TypeAdapter(model)
    try:
        return adapter.validate_python(payload)
    except ValidationError as error:
        raise ToolUnavailableError("resposta do SigNoz em formato inesperado") from error


# ---- Regras de alerta e dependências

# Códigos da condição de limite do SigNoz (`op` e `matchType`).
_OPERATORS = {"1": "acima de", "2": "abaixo de", "3": "igual a", "4": "diferente de"}
_MATCHES = {
    "1": "pelo menos uma vez",
    "2": "o tempo todo",
    "3": "na média",
    "4": "no total",
    "5": "no último valor",
}
_SIGNALS = {"traces": "spans", "logs": "logs", "metrics": "métricas"}


def _condition(rule: _Rule) -> str:
    """Texto legível: consulta, operador, limite e janela, mais o resumo da regra."""
    condition = rule.condition or {}
    parts = [_query(condition)]
    operator = _OPERATORS.get(str(condition.get("op", "")))
    if operator and condition.get("target") is not None:
        match = _MATCHES.get(str(condition.get("matchType", "")))
        parts.append(f"{operator} {condition['target']}" + (f" ({match})" if match else ""))
    if rule.eval_window:
        parts.append(f"na janela de {_duration(rule.eval_window)}")
    text = " ".join(parts)
    summary = str(rule.annotations.get("summary") or "").strip()
    return f"{text}. Resumo: {summary}" if summary else text


def _query(condition: Mapping[str, Any]) -> str:
    """A consulta selecionada da condição v5: do builder, PromQL ou SQL."""
    composite = _mapping(condition.get("compositeQuery"))
    specs = [_mapping(_mapping(q).get("spec")) for q in _items(composite.get("queries"))]
    if not specs:
        return "condição em formato não reconhecido"
    selected = condition.get("selectedQueryName")
    spec = next((s for s in specs if s.get("name") == selected), specs[0])
    if spec.get("query"):  # PromQL ou SQL
        return str(spec["query"])
    expressions = ", ".join(
        str(_mapping(a).get("expression", "?")) for a in _items(spec.get("aggregations"))
    )
    signal = str(spec.get("signal", ""))
    text = " de ".join(part for part in (expressions, _SIGNALS.get(signal, signal)) if part)
    expression = _mapping(spec.get("filter")).get("expression")
    return f"{text} com {expression}" if expression else text


def _mapping(value: Any) -> dict[str, Any]:
    """O JSON da condição varia entre versões: o que não é objeto vira objeto vazio."""
    return value if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownVariableType] — JSON livre


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []  # pyright: ignore[reportUnknownVariableType] — JSON livre


def _duration(window: str) -> str:
    """'5m0s' -> '5m'; '1h0m0s' -> '1h'."""
    return re.sub(r"(?<=[hm])0m$", "", re.sub(r"(?<=[hm])0s$", "", window)) or window


def _text(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def _dependency(key: _DependencyKey) -> str:
    """Nome da dependência para o modelo: banco, serviço ou host, com o endereço se houver."""
    peer, system, database, address = key
    if system:
        name = f"banco {system}" + (f" {database}" if database else "")
        where = peer or address
    elif peer:
        name, where = f"serviço {peer}", address
    elif address:
        name, where = f"host {address}", None
    else:
        name, where = "destino sem identificação", None
    return f"{name} ({where})" if where else name


# ---- Séries e resumos


def _first(series: list[_Series]) -> list[tuple[int, float]]:
    if not series:
        return []
    return sorted((p.timestamp, p.value) for p in series[0].values or [])


def _error_rate(total: list[_Series], failed: list[_Series]) -> list[tuple[int, float]]:
    """Spans com erro ÷ total em cada intervalo; intervalo sem nenhum erro é 0%."""
    totals: Counter[int] = Counter()
    errors: Counter[int] = Counter()
    for counter, series in ((totals, total), (errors, failed)):
        for s in series:
            for point in s.values or []:
                counter[point.timestamp] += int(point.value)
    return [(ts, 100 * errors[ts] / count) for ts, count in sorted(totals.items()) if count]


def _series_summary(label: str, signal: Signal, points: list[tuple[int, float]]) -> str:
    if not points:
        return f"sem dados de {label}"
    peak_ts, peak = max(points, key=lambda p: p[1])
    shown = " · ".join(_value(signal, v) for _, v in points[-SERIES_POINTS:])
    return (
        f"{label}: {_value(signal, points[0][1])} no início, {_value(signal, points[-1][1])} "
        f"no fim, máximo {_value(signal, peak)} às {_at(peak_ts)}. {_change(signal, points)}. "
        f"Últimos {min(SERIES_POINTS, len(points))} pontos, a cada 1 min: {shown}"
    )


def _change(signal: Signal, points: list[tuple[int, float]]) -> str:
    """O primeiro ponto que sai do patamar do início da janela (média dos primeiros pontos)."""
    baseline = sum(v for _, v in points[:BASELINE_POINTS]) / len(points[:BASELINE_POINTS])
    for ts, value in points:
        if signal == "taxa_de_erro":
            changed = abs(value - baseline) > CHANGE_POINTS
        else:
            changed = value > baseline * CHANGE_FACTOR or value * CHANGE_FACTOR < baseline
        if changed:
            before, after = _value(signal, baseline), _value(signal, value)
            return f"Mudou às {_at(ts)}, de {before} para {after}"
    return "Sem mudança de patamar na janela"


def _at(timestamp_ms: int) -> str:
    return _hhmm(datetime.fromtimestamp(timestamp_ms / 1000, UTC))


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


def _normalized(message: str) -> str:
    for pattern in _VARIABLE_PARTS:
        message = pattern.sub("#", message)
    return message


def _message(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first[:MESSAGE_CHARS]


def _window(start: datetime, end: datetime) -> dict[str, str]:
    return {"start": str(int(start.timestamp() * 1000)), "end": str(int(end.timestamp() * 1000))}


def _hhmm(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%H:%M UTC")

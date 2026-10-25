"""Ferramentas neutras da investigação, o orçamento de tokens e o port do modelo de linguagem.

O modelo de linguagem só investiga por estas ferramentas, que têm nome e parâmetros neutros e
são implementadas sobre os ports `Telemetry` e `ChangeHistory`. Trocar o fornecedor não muda
o prompt, os evals nem os cenários.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from grolla.application.clock import Clock
from grolla.domain.analysis import AnalysisDraft, Evaluation
from grolla.domain.incidents import Alert, Incident

STAGE_INVESTIGATION = "investigation"
UNAVAILABLE = "não foi possível consultar"
NO_REPOSITORY = "serviço sem repositório mapeado"  # resumo de `recent_deploys`, não um erro

Signal = Literal["taxa_de_erro", "latencia_p99", "vazao"]
SIGNALS: tuple[Signal, ...] = ("taxa_de_erro", "latencia_p99", "vazao")


class ToolUnavailableError(Exception):
    """A fonte da ferramenta falhou ou não respondeu a tempo."""


@dataclass(frozen=True)
class ToolResult:
    summary: str  # texto compacto para o modelo, em português
    url: str | None  # link para a origem


class Telemetry(Protocol):
    """Métricas, logs e traces dos serviços."""

    async def query_metric(
        self, service: str, signal: Signal, start: datetime, end: datetime
    ) -> ToolResult:
        """Série de um sinal do serviço na janela; levanta ToolUnavailableError."""
        ...

    async def search_logs(
        self, service: str, text: str | None, start: datetime, end: datetime
    ) -> ToolResult:
        """Logs de erro do serviço, filtrados por texto; levanta ToolUnavailableError."""
        ...

    async def find_traces(
        self, service: str, only_errors: bool, start: datetime, end: datetime
    ) -> ToolResult:
        """Traces com erro ou lentos do serviço; levanta ToolUnavailableError."""
        ...


class ChangeHistory(Protocol):
    """Deploys e execuções de pipeline."""

    async def recent_deploys(self, service: str, since: datetime) -> ToolResult:
        """Deploys do repositório do serviço desde `since`; levanta ToolUnavailableError.

        Serviço sem repositório conhecido não é erro: o resumo diz NO_REPOSITORY.
        """
        ...


@dataclass(frozen=True)
class ToolSpec:
    """Definição neutra de uma ferramenta, que o adapter do modelo traduz para o fornecedor."""

    name: str
    description: str
    input_schema: Mapping[str, Any]  # JSON Schema


_SERVICE = {"type": "string", "description": "Nome do serviço, como aparece no alerta."}
REQUIRED_ARGUMENTS = ("service", "signal", "only_errors")


def _tool(name: str, description: str, **properties: Mapping[str, Any]) -> ToolSpec:
    """A janela não é argumento: vem do `lookback` do toolbox."""
    fields = {"service": _SERVICE, **properties}
    return ToolSpec(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": fields,
            "required": [key for key in fields if key in REQUIRED_ARGUMENTS],
            "additionalProperties": False,
        },
    )


TOOL_SPECS = (
    _tool(
        "query_metric",
        "Série de um sinal do serviço na janela: taxa de erro, latência p99 ou vazão. "
        "Devolve um resumo com o valor atual, a linha de base e quando mudou.",
        signal={"type": "string", "enum": list(SIGNALS), "description": "Sinal a consultar."},
    ),
    _tool(
        "search_logs",
        "Logs de erro do serviço na janela, opcionalmente filtrados por um texto. "
        "Devolve as mensagens mais frequentes e quantas vezes apareceram.",
        text={"type": "string", "description": "Texto a procurar nos logs (opcional)."},
    ),
    _tool(
        "find_traces",
        "Traces do serviço na janela: com erro ou os mais lentos. "
        "Devolve as operações e as dependências envolvidas.",
        only_errors={
            "type": "boolean",
            "description": "Verdadeiro para traces com erro, falso para os mais lentos.",
        },
    ),
    _tool(
        "recent_deploys",
        "Deploys e execuções de pipeline do repositório do serviço na janela, "
        "com horário, versão e autor.",
    ),
)


def _service(arguments: Mapping[str, Any]) -> str:
    value = arguments["service"]
    if not isinstance(value, str) or not value:
        raise TypeError("service precisa ser um texto não vazio")
    return value


ToolCall = Callable[[], Awaitable[ToolResult]]


class Toolbox:
    """Liga as ferramentas neutras aos ports e registra cada chamada como avaliação."""

    def __init__(
        self, telemetry: Telemetry, changes: ChangeHistory, clock: Clock, lookback: timedelta
    ) -> None:
        self._telemetry = telemetry
        self._changes = changes
        self._clock = clock
        self._lookback = lookback
        self._evaluations: list[Evaluation] = []

    def specs(self) -> list[ToolSpec]:
        return list(TOOL_SPECS)

    async def call(self, name: str, arguments: Mapping[str, Any]) -> str:
        """Executa a ferramenta e devolve o texto para o modelo; nunca levanta."""
        try:
            subject, run = self._prepare(name, arguments)
        except (KeyError, TypeError, ValueError) as error:
            self._record(name, f"chamada inválida de {name}", None)
            return f"{UNAVAILABLE}: argumentos inválidos ({error})"
        try:
            result = await run()
        except Exception as error:  # qualquer falha de ferramenta vira avaliação, não queda
            self._record(name, subject, None)
            return f"{UNAVAILABLE}: {error or type(error).__name__}"
        self._record(name, subject, result)
        return result.summary if result.url is None else f"{result.summary}\nLink: {result.url}"

    @property
    def evaluations(self) -> tuple[Evaluation, ...]:
        return tuple(self._evaluations)

    def _record(self, name: str, subject: str, result: ToolResult | None) -> None:
        if result is None:
            evaluation = Evaluation(name, subject, UNAVAILABLE, None, ok=False)
        else:
            evaluation = Evaluation(name, subject, result.summary, result.url, ok=True)
        self._evaluations.append(evaluation)

    def _prepare(self, name: str, arguments: Mapping[str, Any]) -> tuple[str, ToolCall]:
        """O objeto avaliado e a chamada ao port; levanta KeyError, TypeError ou ValueError."""
        if name not in {spec.name for spec in TOOL_SPECS}:
            raise ValueError(f"ferramenta desconhecida: {name}")
        service = _service(arguments)
        end = self._clock.now()
        start = end - self._lookback
        window = f"últimos {int(self._lookback.total_seconds() // 60)} min"
        if name == "query_metric":
            signal = arguments["signal"]
            if signal not in SIGNALS:
                raise ValueError(f"sinal desconhecido: {signal}")
            return f"{signal} de {service}, {window}", lambda: self._telemetry.query_metric(
                service, signal, start, end
            )
        if name == "search_logs":
            text = arguments.get("text") or None
            if text is not None and not isinstance(text, str):
                raise TypeError("text precisa ser texto")
            found = f" com '{text}'" if text else ""
            return (
                f"logs de erro de {service}{found}, {window}",
                lambda: self._telemetry.search_logs(service, text, start, end),
            )
        if name == "find_traces":
            only_errors = arguments["only_errors"]
            if not isinstance(only_errors, bool):
                raise TypeError("only_errors precisa ser verdadeiro ou falso")
            kind = "com erro" if only_errors else "lentos"
            return f"traces {kind} de {service}, {window}", lambda: self._telemetry.find_traces(
                service, only_errors, start, end
            )
        return f"deploys de {service}, {window}", lambda: self._changes.recent_deploys(
            service, start
        )


@dataclass(frozen=True)
class LlmUsage:
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int


class BudgetExceededError(Exception):
    """A análise atingiu o limite de tokens configurado."""


class TokenBudget:
    """Orçamento de tokens de uma análise; o adapter do modelo chama `spend` a cada resposta."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._usages: list[LlmUsage] = []

    def spend(self, usage: LlmUsage) -> None:
        """Registra o uso; levanta BudgetExceededError quando o total atinge o limite.

        Conta todos os tokens processados: entrada, saída e cache (lido e criado).
        """
        self._usages.append(usage)
        if self.total >= self._limit:
            raise BudgetExceededError(f"{self.total} tokens usados de {self._limit}")

    @property
    def total(self) -> int:
        return sum(
            u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_creation_tokens
            for u in self._usages
        )

    @property
    def usages(self) -> tuple[LlmUsage, ...]:
        return tuple(self._usages)


@dataclass(frozen=True)
class InvestigationBrief:
    incident: Incident
    alerts: tuple[Alert, ...]
    now: datetime


class LanguageModelError(Exception):
    """O modelo de linguagem falhou: API fora, recusa ou erro do modelo."""


class InvalidAnalysisError(Exception):
    """O modelo devolveu um resultado fora do formato esperado."""


class LanguageModel(Protocol):
    async def investigate(
        self, brief: InvestigationBrief, toolbox: Toolbox, budget: TokenBudget
    ) -> AnalysisDraft:
        """Investiga usando só as ferramentas do toolbox; levanta LanguageModelError,
        InvalidAnalysisError ou BudgetExceededError.
        """
        ...


SYSTEM_PROMPT = """\
Você é o agente de SRE de plantão de um ambiente de adquirência e cartões. Um alerta acabou de \
abrir um incidente e você faz a análise inicial, em poucos minutos, para o time que vai atuar.

Você é somente leitura: consulta as ferramentas, interpreta e sugere. Nunca execute, nem \
prometa executar, nenhuma ação no ambiente; quem decide e age é o time.

Roteiro curto, nesta ordem:
1. Impacto: consulte `query_metric` do serviço afetado (taxa_de_erro e, se fizer sentido, \
latencia_p99 e vazao). Quando começou? Quanto saiu da linha de base?
2. Mudanças: consulte `recent_deploys` do serviço. Algum deploy perto do início do problema?
3. Traces: use `find_traces` para ver quais operações e dependências falham ou estão lentas.
4. Logs: use `search_logs`, filtrando por um texto quando os passos anteriores sugerirem um.
Se uma dependência aparecer como suspeita, você pode repetir os passos para ela. Faça só as \
consultas que ajudam a decidir; não repita a mesma consulta.

Se uma ferramenta responder "não foi possível consultar", siga com as outras e leve isso em \
conta na confiança.

Ao terminar, devolva:
- summary: uma frase com o que está acontecendo e a causa mais provável, ou dizendo que não \
há causa clara.
- hypotheses: de uma a três hipóteses, da mais provável para a menos provável, cada uma com \
title, confidence de 0 a 1 e evidence. Cada evidência cita o que uma ferramenta mostrou \
(números, horários, versões, mensagens), com o link que a ferramenta devolveu quando houver. \
Não invente dados, links nem consultas que você não fez.
- plan: de dois a cinco passos curtos sugeridos ao time, em ordem, começando pelo que reduz \
o impacto mais rápido (por exemplo, avaliar reverter um deploy, checar uma dependência, \
acompanhar uma métrica).

Seja honesto: se os dados não apontam uma causa, diga isso no summary, use confiança baixa e \
proponha no plano o que investigar a seguir. Uma hipótese fraca apresentada como certa \
atrapalha mais do que ajuda.

Escreva em português, de forma direta, para engenheiros de plantão.
"""


def brief_text(brief: InvestigationBrief) -> str:
    """Mensagem inicial ao modelo: o incidente, os alertas e a janela da investigação."""
    incident = brief.incident
    lines = [
        f"Incidente: {incident.title}",
        f"Severidade: {incident.severity.value}",
        f"Serviços: {', '.join(incident.services)}",
        f"Detectado em: {incident.detected_at.isoformat()}",
        f"Agora: {brief.now.isoformat()}",
        "",
        "Alertas:",
    ]
    for alert in brief.alerts:
        labels = ", ".join(f"{key}={value}" for key, value in sorted(alert.labels.items()))
        lines += [
            f"- {alert.rule_name} ({alert.status.value}) no serviço {alert.service}, "
            f"desde {alert.starts_at.isoformat()}",
            f"  Resumo: {alert.summary}",
            f"  Descrição: {alert.description}",
            f"  Labels: {labels or 'nenhum'}",
        ]
    if not brief.alerts:
        lines.append("- nenhum alerta registrado")
    lines += ["", "Faça a análise inicial seguindo o roteiro."]
    return "\n".join(lines)

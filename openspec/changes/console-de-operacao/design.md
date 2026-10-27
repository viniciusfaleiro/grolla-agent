## Context

O chat do war room já tem fila, contexto, teto de tokens, `remember`, memória e RAG, mas está
amarrado a um incidente. O console reaproveita esse desenho com sessões próprias, gráficos e um
caminho manual de abertura de incidente.

## Goals / Non-Goals

**Goals:** os da proposta. **Non-Goals:** os da proposta.

## Contratos

```python
# grolla.domain.console (novo)
CHART_SIGNALS = ("taxa_de_erro", "latencia_p99", "vazao")


@dataclass(frozen=True)
class ChartRequest:
    service: str
    signal: str
    minutes: int  # 5 a 1440


@dataclass(frozen=True)
class ConsoleDraft:
    text: str
    charts: tuple[ChartRequest, ...]  # até 2


@dataclass(frozen=True)
class ConsoleSession:
    id: UUID
    title: str
    author: str
    created_at: datetime
    updated_at: datetime
    incident_id: UUID | None = None  # incidente aberto a partir da conversa


@dataclass(frozen=True)
class ConsoleMessage:
    id: int
    session_id: UUID
    author: str
    text: str
    at: datetime
    charts: tuple[ChartRequest, ...] = ()
    evaluations: tuple[Evaluation, ...] = ()
    reply_status: ReplyStatus | None = None  # na pergunta
    reply_to: int | None = None  # na resposta


def session_title(question: str) -> str: ...
def valid_charts(
    charts,
) -> tuple[ChartRequest, ...]: ...  # descarta sinal desconhecido, corta a janela


# grolla.domain.incidents (acréscimos)
# EventType.INCIDENT_OPENED_MANUALLY = "incident_opened_manually"
def decide_manual(title, service, severity, author, origin, now, page_url, new_id) -> Decision: ...


# grolla.application.console (novo)
class ConsoleStore(Protocol):
    async def create_session(self, session, question: ConsoleMessage) -> ConsoleMessage: ...
    async def add_question(self, question: ConsoleMessage) -> ConsoleMessage: ...
    async def sessions(self, limit: int) -> list[ConsoleSession]: ...
    async def session(self, session_id) -> ConsoleSession | None: ...
    async def messages(self, session_id) -> list[ConsoleMessage]: ...
    async def link_incident(self, session_id, incident_id) -> None: ...
    async def claim(self, now) -> ConsoleJob | None: ...
    async def stuck(self, started_before) -> list[ConsoleJob]: ...
    async def answer(self, job, reply, usages, at) -> None: ...
    async def tokens_since(self, since) -> int: ...  # etapa console


@dataclass(frozen=True)
class ConsolePolicy:
    max_tokens: int = 60_000
    daily_max_tokens: int = 500_000
    timeout: timedelta = timedelta(minutes=2)
    lookback: timedelta = timedelta(minutes=60)


async def ask_console(*, store, session_id: UUID | None, author, text, clock) -> ConsoleSession: ...
async def run_next_console_reply(
    *, store, model, telemetry, changes, cloud, clock, policy, memory=None, embedder=None
) -> bool: ...
async def reap_stuck_console_replies(store, clock, policy) -> int: ...
async def open_incident_from_console(
    *, store, repository, session_id, title, service, severity, author, clock, page_url
) -> Incident: ...
async def chart_series(telemetry, chart, clock) -> list[tuple[datetime, float]]: ...


# grolla.application.investigation
STAGE_CONSOLE = "console"


# Telemetry ganha:
async def series(self, service, signal, start, end) -> list[tuple[datetime, float]]: ...
# LanguageModel ganha:
async def consult(self, context: str, question: str, toolbox, budget) -> ConsoleDraft: ...


# CONSOLE_PROMPT e console_text(context, question).
```

**Schema (migração 0012):**
- `console_sessions`: `id`, `title`, `author`, `created_at`, `updated_at`, `incident_id`.
- `console_messages`, como a `chat_messages`, mais `charts jsonb`.
- O uso vai para `llm_usage` com a etapa `console`, sem incidente.

**Worker:** `run_console_replies`, a cada 3 s, na fila `console` com concorrência 1, separada da
fila `analysis`.

## Decisions

**1. Console separado do chat do war room.** As sessões não pertencem a incidente, e o contexto é
outro: não há análise nem timeline. Reaproveitam-se o padrão da fila, o formato curto e as
ferramentas.
*Alternativa:* um "incidente fantasma" para o console. Descartada porque poluiria lista, métricas
e post mortems.

**2. Gráfico pedido pelo modelo e desenhado com dados reais.** O modelo só diz o que mostrar
(serviço, sinal, janela). A api busca a série no `Telemetry.series` e desenha um SVG no servidor.
Assim o número no gráfico nunca vem do modelo.
*Alternativa:* o modelo devolver os pontos. Descartada porque ele poderia inventar.

**3. Prioridade por fila separada com concorrência 1.** Incidente vem antes: as filas `analysis` e
`default` não esperam o console.

**4. Incidente manual pelo núcleo.** `decide_manual` segue as mesmas regras de estado pela
severidade e o mesmo pedido de análise, com o evento de origem. O agrupamento por alerta não
muda.

## Divisão de trabalho

| Papel | Entrega |
|---|---|
| `grolla-dominio` | `domain.console`, `decide_manual`, `application.console`, prompt, fakes (`InMemoryConsoleStore`, `FakeTelemetry.series`, `ScriptedLanguageModel.consult`) e suítes de contrato, job do worker |
| `grolla-persistencia` | migração 0012 e `PostgresConsoleStore` |
| `grolla-integracoes` | `Telemetry.series` no SigNoz e no fake, `consult` no Anthropic, LLM fake com a etapa `console` |
| `grolla-web` | `/console`, a sessão com chat, gráficos em SVG, "abrir incidente" e o link na barra |
| `grolla-e2e` | cenários: pergunta e resposta com gráfico; abrir incidente a partir da conversa; nota pelo console |

## Risks / Trade-offs

- [Custo do console] → teto por resposta e por dia; fila com concorrência 1.
- [Gráfico lento ao abrir a página] → uma consulta por gráfico, com até 2 por resposta, e falha
  vira "gráfico indisponível".

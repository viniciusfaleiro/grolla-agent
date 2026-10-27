## Context

O S6 entregou o post mortem com ciclo de vida e a seção "Avaliação do agente" em texto livre. A
memória viva aprende com o log no encerramento. O "já vi isso" aparece na análise, mas ninguém
diz se acertou. Faltam três coisas: o post mortem aprovado alimentar a memória, respostas
humanas estruturadas e uma página de métricas.

## Goals / Non-Goals

**Goals:** os da proposta. **Non-Goals:** os da proposta.

## Contratos

```python
# grolla.domain.post_mortem (acréscimos)
@dataclass(frozen=True)
class AgentEvaluation:
    main_hypothesis_correct: bool | None = None
    minutes_to_right: int | None = None
    memory_helped: bool | None = None
    author: str | None = None
    at: datetime | None = None


# PostMortem ganha evaluation: AgentEvaluation = AgentEvaluation()
def evaluated(pm, evaluation: AgentEvaluation, author, now) -> PostMortem:
    """Levanta InvalidPostMortemError em post mortem encerrado ou minutos negativos."""


# grolla.domain.incidents (acréscimos)
# EventType.SEEN_BEFORE_ANSWERED = "seen_before_answered", data {"past": <id>, "same": bool}


# grolla.domain.metrics (novo)
class Granularity(StrEnum):
    WEEK = "semana"
    MONTH = "mes"


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # exclusivo
    label: str  # "out/2026" ou "semana de 20/10"


@dataclass(frozen=True)
class MetricsFacts:
    resolved: tuple[datetime, ...]  # resolved_at dos incidentes resolvidos
    approved: tuple[tuple[datetime, AgentEvaluation], ...]  # status_at e a avaliação
    answers: tuple[tuple[datetime, bool], ...]  # última resposta do "já vi isso" por incidente


@dataclass(frozen=True)
class PeriodMetrics:
    period: Period
    resolved: int
    approved: int
    evaluated: int  # com main_hypothesis_correct respondido
    correct: int
    median_minutes: float | None
    seen_before_confirmed: int
    seen_before_rejected: int
    memory_helped: int


def periods(today: date, granularity: Granularity, count: int) -> tuple[Period, ...]: ...
def aggregate(facts: MetricsFacts, periods: Sequence[Period]) -> tuple[PeriodMetrics, ...]: ...


# grolla.application.metrics (novo)
class MetricsReader(Protocol):
    async def facts(self, since: datetime) -> MetricsFacts: ...


async def agent_metrics(reader, clock, granularity, count=6) -> tuple[PeriodMetrics, ...]: ...
async def answer_seen_before(*, repository, incident_id, same: bool, author, clock) -> None:
    """Grava SEEN_BEFORE_ANSWERED; levanta InvalidActionError sem autor ou sem "já vi isso"."""


async def evaluate_agent(*, store, incident_id, evaluation, author, clock) -> PostMortem: ...


# grolla.application.post_mortem
# move_status ganha repository: IncidentRepository | None = None; ao aprovar, pede o aprendizado
# (uow.request_learning) na transação.
# grolla.application.memory: run_next_learning ganha post_mortems: PostMortemStore | None = None;
# com post mortem aprovado, LearningBrief.post_mortem traz o texto, o episódio fica CONFIRMED com
# a fonte de quem aprovou e o motivo da versão é "post mortem aprovado".
# grolla.application.investigation: LearningBrief ganha post_mortem: str | None = None.
```

**Schema (migração 0010):** `post_mortems.evaluation jsonb`, que pode ser nulo. As respostas
do "já vi isso" ficam nos eventos, sem tabela nova.

## Decisions

**1. Métricas só de respostas humanas e do log.** A pessoa diz se a hipótese principal estava
certa. Isso é auditável e não depende de um segundo modelo julgando o primeiro.

**2. "Já vi isso" respondido como evento.** Fica na timeline, tem autor e entra na agregação pela
última resposta do incidente. Não precisa de tabela.

**3. Reaprender ao aprovar pelo mesmo caminho do encerramento.** `request_learning` volta o pedido
a pendente, e o caso de uso, ao ver o post mortem aprovado, usa o texto dele e confirma o
episódio. Não há fila nova.

**4. `MetricsReader` como port do banco.** A agregação é pura, no domínio. A leitura dos fatos é
uma consulta só no Postgres, sem varrer o log de cada incidente na api.

## Divisão de trabalho

| Papel | Entrega |
|---|---|
| `grolla-dominio` | `AgentEvaluation`/`evaluated`, `domain.metrics`, `application.metrics`, aprender com o post mortem aprovado, fakes (`InMemoryMetricsReader`) e suítes de contrato |
| `grolla-persistencia` | migração 0010, `evaluation` no `PostgresPostMortemStore`, `PostgresMetricsReader` |
| `grolla-integracoes` | a avaliação no PDF (`FpdfDocumentRenderer`) |
| `grolla-web` | botões "era o mesmo" e "não era" no "já vi isso", formulário da avaliação no post mortem, `/metricas` |
| `grolla-e2e` | cenários: responder o "já vi isso", avaliar e ver em `/metricas`; aprovar o post mortem confirma o episódio |

## Desvios de contrato combinados na implementação

**Núcleo:**
- `lessons_text(pm)` monta o texto que ensina a memória: fatores, lições, plano com os itens e
  resumo.
- Só o episódio fica confirmado; os dossiês reescritos a partir do post mortem continuam com a
  fonte do agente e a confiança aprendida.
- Avaliar move o rascunho a em revisão; no aprovado, a avaliação continua permitida.

**Persistência:**
- A última resposta do "já vi isso" segue a ordem de inserção no log.
- Post mortem encerrado conta pelo `status_at` do encerramento.

**Web:**
- Contagem zero aparece como "—".
- Responder o "já vi isso" recarrega a página (303 ou `HX-Redirect`), para a timeline atualizar.

**Code review do pai:** o cenário espera o 303 do "já vi isso" respondido.

## Risks / Trade-offs

- [Pouca resposta humana] → métricas com "—" e o número de avaliados ao lado do percentual.
- [Reaprender ao aprovar custa uma chamada] → só uma por aprovação.

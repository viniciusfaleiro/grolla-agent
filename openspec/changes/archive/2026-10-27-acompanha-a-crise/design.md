## Context

Hoje, depois da análise inicial e da validação, o Grolla só fala quando perguntam no chat. O
war room já tem as ações humanas (IC, mitigar, encerrar) e o log de eventos, e a investigação já
tem ferramentas, memória e RAG. Falta o agente acompanhar o P0/P1 enquanto ele está aberto,
avisar o canal só quando muda, estimar o impacto e propor os próximos passos sem decidir por
ninguém.

## Goals / Non-Goals

**Goals:**
- Ciclos periódicos de acompanhamento em P0/P1, com tendência, taxa de erro e resumo.
- Card só quando muda, ou curto a cada 15 min.
- Ciclo antecipado por alerta agrupado ou por mensagem.
- Proposta de mitigar ou encerrar após 15 min normalizado.
- Volta determinística de mitigado para em tratamento quando o alerta volta.
- Impacto financeiro estimado por configuração.

**Non-Goals:** os da proposta.

## Contratos

```python
# grolla.domain.follow_up (novo)
class Trend(StrEnum):
    WORSE = "piorando"
    STABLE = "estavel"
    BETTER = "melhorando"
    NORMAL = "normalizado"


class Proposal(StrEnum):
    MITIGATE = "mitigar"
    RESOLVE = "encerrar"


@dataclass(frozen=True)
class CycleDraft:  # o que o modelo devolve
    trend: Trend
    error_rate: float | None  # % de erro atual (0 a 100), quando medida
    summary: str
    changed: bool
    evidence: tuple[Evidence, ...]  # até 3


class CycleStatus(StrEnum):
    PENDING = "pendente"
    RUNNING = "em_andamento"
    COMPLETED = "concluido"
    PARTIAL = "parcial"


@dataclass(frozen=True)
class Cycle:
    incident_id: UUID
    number: int  # 1, 2, ...
    status: CycleStatus
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    trend: Trend | None = None
    error_rate: float | None = None
    summary: str = ""
    changed: bool = False
    evidence: tuple[Evidence, ...] = ()
    evaluations: tuple[Evaluation, ...] = ()
    partial_reason: PartialReason | None = None
    posted: bool = False  # o card "Atualização" foi enviado


@dataclass(frozen=True)
class FollowUpPolicy:
    severities: frozenset[Severity]
    cycle: timedelta
    quiet: timedelta  # card curto sem mudança
    stable: timedelta  # normalizado por tanto tempo → proposta
    max_cycles: int
    early: timedelta = timedelta(minutes=1)  # antecipação só se o último começou antes disso


def cycle_due(incident, analysis_done_at, cycles, requested_at, now, policy) -> bool: ...
def completed(job: Cycle, draft: CycleDraft, evaluations, now) -> Cycle: ...
def partial_cycle(job: Cycle, reason: PartialReason, evaluations, now) -> Cycle: ...
def decide_cycle(
    incident, cycle, history, proposals, impact, now, policy, page_url
) -> CycleDecision:
    """O ciclo pronto, `posted` decidido; eventos (CYCLE_COMPLETED/CYCLE_PARTIAL,
    AGENT_PROPOSAL, FOLLOW_UP_STOPPED) e cards (FOLLOW_UP_UPDATE, AGENT_PROPOSAL)."""


@dataclass(frozen=True)
class CycleDecision:
    cycle: Cycle
    events: tuple[IncidentEvent, ...]
    notifications: tuple[Notification, ...]


def estimated_impact(
    cycles: Sequence[Cycle], detected_at: datetime, per_minute: float
) -> float: ...


# grolla.domain.incidents (acréscimos)
# EventType: CYCLE_COMPLETED = "cycle_completed", CYCLE_PARTIAL = "cycle_partial",
#            FOLLOW_UP_STOPPED = "follow_up_stopped", AGENT_PROPOSAL = "agent_proposal"
# NotificationKind: FOLLOW_UP_UPDATE = "follow_up_update", AGENT_PROPOSAL = "agent_proposal",
#                   ALERT_REOPENED = "alert_reopened"
# Notification ganha cycle: Cycle | None = None e impact: str | None = None (texto já formatado)
# decide_firing: alerta disparando num incidente MITIGATED → HANDLING, STATE_CHANGED com
# reason "alerta voltou a disparar" e o card ALERT_REOPENED.


# grolla.application.follow_up (novo)
@dataclass(frozen=True)
class CycleCandidate:
    incident: Incident
    analysis_done_at: datetime | None
    cycles: tuple[Cycle, ...]  # em ordem
    requested_at: datetime | None  # pedido de ciclo antecipado
    tokens: int  # tokens de acompanhamento já usados


class CycleQueue(Protocol):
    async def candidates(self, severities: Collection[Severity]) -> list[CycleCandidate]:
        """Incidentes abertos com essas severidades."""

    async def enqueue(self, incident_id: UUID, number: int, at: datetime) -> bool:
        """Cria o ciclo pendente; False se já existe (corrida entre workers)."""

    async def claim(self, now: datetime) -> Cycle | None: ...
    async def stuck(self, started_before: datetime) -> list[Cycle]: ...
    async def finish(self, cycle: Cycle, events, notifications, usages, at) -> None:
        """Numa transação, só se em andamento; zera o pedido antecipado."""


@dataclass(frozen=True)
class ImpactPolicy:
    currency: str = "BRL"
    per_minute: Mapping[str, float] = field(default_factory=dict)


# Casos de uso: schedule_cycles, run_next_cycle, reap_stuck_cycles, impact_of (página).
# IncidentUnitOfWork ganha request_cycle(incident_id, at), chamado no alerta agrupado e no ask.
# IncidentRepository ganha cycles_of(incident_id) -> list[Cycle].


# grolla.application.investigation
STAGE_FOLLOW_UP = "follow_up"


# LanguageModel ganha:
async def follow_up(
    self,
    brief: InvestigationBrief,
    context: str,
    previous: Cycle | None,
    toolbox: Toolbox,
    budget: TokenBudget,
) -> CycleDraft: ...


# FOLLOW_UP_PROMPT e follow_up_text(context, previous) (do grolla-dominio).
```

**Schema (migração 0008):**
- `cycles`:
  - chave primária `(incident_id, number)`;
  - `status`, `requested_at`, `started_at`, `finished_at`;
  - `trend`, `error_rate`, `summary`, `changed`, `evidence jsonb`, `evaluations jsonb`,
    `partial_reason`, `posted`;
  - índices nos pendentes e nos em andamento.
- `follow_ups`: `incident_id` como chave primária e `requested_at`, para o pedido de ciclo
  antecipado.
- O uso do LLM vai para `llm_usage` com a etapa `follow_up` e o `incident_id`.

**Config:**
- `follow_up`: os campos da spec `team-config`. Sem a seção, não há acompanhamento.
- `impact`: `currency` e `services.<s>.per_minute`.
- `llm.stages.follow_up`, que herda de `investigation`.

**Worker:**
- `schedule_cycles`, a cada 30 s, enfileira os ciclos devidos;
- `run_cycles`, a cada 10 s, na fila `analysis`;
- `reap_stuck_cycles`, junto com os outros reapers.

### Ajustes do commit de contrato

- `cycle_due` e `decide_cycle` recebem os argumentos por nome; `decide_cycle` recebe
  `proposals: Collection[MadeProposal]` (proposta + estado em que foi feita, tirados dos
  eventos `AGENT_PROPOSAL`).
- `FollowUpLimits` (timeout, tokens por ciclo e por incidente) e `impact_text` em
  `application.follow_up`; `run_next_cycle` recebe `limits`, `impact` e `page_url`.
- `Notification.cycle` usa import só para tipo (o módulo `follow_up` importa `incidents`).
- `WorkerContainer` ganha `cycle_queue` (None sem `follow_up`), `follow_up_policy`,
  `follow_up_limits` e `impact_policy`; `ApiContainer` ganha `impact_policy`.
- O Teams já tem títulos provisórios para os três cards novos, para a suíte de contrato passar.

## Decisions

**1. Agendamento por varredura, não por timer por incidente.** A cada 30 s, `schedule_cycles` lê
os candidatos e decide com `cycle_due` (função pura). Sem timers por incidente, um worker que
reinicia não perde nada.
*Mais simples considerada:* um job do procrastinate agendado para daqui a 5 min a cada ciclo.
Descartada porque espalha estado pela fila e complica a antecipação.

**2. Tendência dada pelo modelo, decisão de postar e propor no núcleo.** O modelo devolve dados
(tendência, taxa de erro, mudou?). O domínio decide se posta, quando propõe e calcula o impacto,
e o estado só muda por ação humana ou pela regra determinística do alerta.
*Alternativa:* o modelo decidir o card e a proposta. Descartada porque fica imprevisível e
contradiz "o núcleo decide".

**3. Volta para em tratamento pelo alerta, não pelo modelo.** Um alerta disparando num incidente
mitigado é um fato objetivo. Já o "piorou" de um ciclo é julgamento: vira card e aviso, não
mudança de estado.

**4. Impacto por valor configurado.** `per_minute × taxa de erro × minutos`. É simples, auditável,
e o time ajusta o valor. Métrica de negócio real fica para quando existir no SigNoz.

**5. Ciclo reaproveita o contexto do chat.** A mesma função monta estado, análise, validação,
timeline, chat e memória (`chat_context`), mais o ciclo anterior. Não há segundo montador de
contexto.

**6. Propostas pelos eventos.** A proposta é um evento `AGENT_PROPOSAL` com a proposta e o estado.
"Não repetir no mesmo estado" sai do log, sem tabela nova.

## Divisão de trabalho

| Papel | Entrega |
|---|---|
| `grolla-dominio` | `domain.follow_up`, a regra do alerta no `decide_firing`, `application.follow_up`, `request_cycle` no alerta agrupado e no `ask`, `FOLLOW_UP_PROMPT` e `follow_up_text`, fakes (`InMemoryCycleQueue`, `ScriptedLanguageModel.follow_up`) e suítes de contrato, jobs do worker |
| `grolla-persistencia` | migração 0008, `PostgresCycleQueue`, `request_cycle`, `cycles_of` |
| `grolla-integracoes` | `follow_up` no Anthropic, cards no Teams (atualização, sem mudança, proposta, alerta voltou), LLM fake com a etapa `follow_up` |
| `grolla-web` | seção "Acompanhamento" no war room, impacto no cabeçalho, aviso da proposta |
| `grolla-e2e` | cenários: ciclo com card, proposta após normalizado, mitigado volta com o alerta |

## Desvios de contrato combinados na implementação

**Núcleo:**
- `schedule_cycles` recebe `repository`, para registrar `FOLLOW_UP_STOPPED` uma vez quando o
  teto de tokens é atingido.
- `reap_stuck_cycles` recebe `policy`.
- `ALERT_REOPENED_REASON` fica em `domain.incidents`.
- Ciclo parcial não interrompe nem conta na sequência de normalizado.
- O impacto pula ciclos sem taxa de erro.

**Persistência:** o card da outbox carrega `cycle` e `impact`; cards antigos continuam legíveis.

**Integrações (convenção dos cards):**
- FOLLOW_UP_UPDATE com `cycle` é o card completo; sem `cycle`, é o card curto "Sem mudança", com
  o texto em `summary`.
- Em AGENT_PROPOSAL, o `summary` é o título e o `evidence` é o motivo.

**Web:** a seção, o aviso e o impacto chegam pelo polling da timeline (`hx-swap-oob`), sem rota
nova.

**Code review do pai:**
- O `finish` apaga só o pedido antecipado feito antes do início do ciclo: um pedido que chega
  durante a execução não se perde.
- A proposta de mitigar só vale em tratamento, como a ação humana: sem IC não há quem mitigue.
  Spec e cenário e2e ajustados.

## Risks / Trade-offs

- [Custo em incidente longo] → teto de ciclos e de tokens por incidente; só P0 e P1.
- [Ruído no canal] → card só quando muda, curto a cada 15 min.
- [Tendência errada do modelo] → ela só gera card e proposta; quem muda o estado é a pessoa.
- [Impacto enganoso] → sempre rotulado "estimado", com a fonte (valor por minuto no YAML).

## Migration Plan

Migração 0008 só cria tabelas. Sem `follow_up` no YAML, nada muda.

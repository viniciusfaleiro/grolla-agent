## Context

Depois do S3.1, a página do incidente mostra a análise, a validação e a timeline, só para leitura. Os estados são apenas Detectado e Informado. Os eventos já têm o campo `author`, que hoje é sempre "sistema". O worker já roda filas de análise e de validação com limites e garantia de término, e esse padrão é reaproveitado para as respostas do chat. É uma change multiagente.

## Goals / Non-Goals

**Goals:** o time conduz e encerra o incidente pela página. O agente participa por chat, investigando com as mesmas ferramentas. Todo ato humano fica na timeline com autor.

**Non-Goals:** ver a proposta.

## Contratos

```python
# grolla.domain.incidents (acréscimos)
class IncidentState(StrEnum):
    DETECTED = "detectado"
    INFORMED = "informado"
    HANDLING = "em_tratamento"
    MITIGATED = "mitigado"
    RESOLVED = "resolvido"
    DISCARDED = "descartado"


OPEN_STATES: frozenset[IncidentState]  # todos menos RESOLVED e DISCARDED


@dataclass(frozen=True)
class Incident:
    ...  # campos atuais, mais:
    commander: str | None = None
    handling_at: datetime | None = None  # TTA
    mitigated_at: datetime | None = None  # TTM
    resolved_at: datetime | None = None  # TTR (encerrado ou descartado)


class EventType(StrEnum):
    ...  # atuais, mais:
    COMMANDER_ASSIGNED = "commander_assigned"
    ACTION_RECORDED = "action_recorded"
    HYPOTHESIS_DISCARDED = "hypothesis_discarded"
    INCIDENT_MITIGATED = "incident_mitigated"
    INCIDENT_REOPENED = "incident_reopened"
    INCIDENT_RESOLVED = "incident_resolved"
    INCIDENT_DISCARDED = "incident_discarded"
    HUMAN_MESSAGE = "human_message"
    AGENT_MESSAGE = "agent_message"


class NotificationKind(StrEnum):
    ...  # atuais, mais:
    COMMANDER_ASSIGNED = "commander_assigned"
    SEVERITY_CHANGED_BY_HUMAN = "severity_changed_by_human"
    INCIDENT_MITIGATED = "incident_mitigated"
    INCIDENT_RESOLVED = "incident_resolved"
    INCIDENT_DISCARDED = "incident_discarded"


# grolla.domain.war_room (novo): ações e decisões puras
@dataclass(frozen=True)
class AssumeCommander: ...


@dataclass(frozen=True)
class ChangeSeverity:
    severity: Severity


@dataclass(frozen=True)
class RecordAction:
    text: str


@dataclass(frozen=True)
class DiscardHypothesis:
    title: str
    reason: str


@dataclass(frozen=True)
class MarkMitigated: ...


@dataclass(frozen=True)
class Reopen: ...


@dataclass(frozen=True)
class Resolve:
    reason: str


@dataclass(frozen=True)
class DiscardIncident:
    reason: str


HumanAction = (
    AssumeCommander
    | ChangeSeverity
    | RecordAction
    | DiscardHypothesis
    | MarkMitigated
    | Reopen
    | Resolve
    | DiscardIncident
)


class InvalidActionError(ValueError): ...  # mensagem em português para o humano


def decide_action(
    incident: Incident,
    action: HumanAction,
    author: str,
    now: datetime,
    page_url: Callable[[UUID], str],
) -> Decision: ...  # incidente atualizado, eventos com author e o card, se houver


# grolla.domain.chat (novo)
class ReplyStatus(StrEnum):  # mesma ideia da análise
    PENDING = "pendente"
    RUNNING = "em_andamento"
    ANSWERED = "respondida"
    FAILED = "falhou"  # "não consegui responder: motivo"


@dataclass(frozen=True)
class ChatMessage:
    id: int
    incident_id: UUID
    author: str  # nome da pessoa ou "agente"
    text: str
    at: datetime
    evaluations: tuple[Evaluation, ...]  # só nas do agente


@dataclass(frozen=True)
class ChatDraft:  # o que o modelo devolve
    text: str


# grolla.application.war_room (novo)
async def act(
    repository: IncidentRepository,
    incident_id: UUID,
    action: HumanAction,
    author: str,
    clock: Clock,
    page_url: Callable[[UUID], str],
) -> Incident: ...  # transação: get_for_update, decide_action, save, append, enqueue; InvalidActionError


async def ask(
    repository: IncidentRepository, incident_id: UUID, author: str, text: str, clock: Clock
) -> None: ...  # grava human_message e o pedido de resposta


# grolla.application.chat (novo)
class ChatQueue(Protocol):  # adapter: grolla.adapters.postgres
    async def claim(self, now: datetime) -> ChatJob | None: ...
    async def stuck(self, started_before: datetime) -> list[ChatJob]: ...
    async def answer(
        self,
        job: ChatJob,
        message: ChatMessage,
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None: ...  # só se em andamento
    async def chat_tokens(self, incident_id: UUID) -> int: ...


async def run_next_reply(...) -> bool: ...
async def reap_stuck_replies(...) -> int: ...


# LanguageModel ganha:
async def reply(
    self,
    brief: InvestigationBrief,
    context: str,
    question: str,
    toolbox: Toolbox,
    budget: TokenBudget,
) -> ChatDraft: ...


CHAT_PROMPT: str
STAGE_WARROOM_CHAT = "warroom_chat"

# IncidentUnitOfWork ganha incident_for_update(incident_id); IncidentRepository ganha
# chat_of(incident_id) -> list[ChatMessage].
```

**Schema (migração `0005`)**
- `incidents` ganha `commander`, `handling_at`, `mitigated_at` e `resolved_at`.
- Tabela `chat_messages`:
  - campos `id bigserial`, `incident_id`, `author`, `text`, `at`, `evaluations jsonb`, `reply_status` e `reply_to`;
  - a pergunta do humano tem `reply_status`, que funciona como fila;
  - a resposta do agente aponta `reply_to`.

**Configuração.** A seção `investigation` do YAML ganha:
- `chat_timeout_seconds`, padrão 120;
- `chat_max_tokens`, padrão 60.000 por resposta;
- `chat_max_tokens_per_incident`, padrão 400.000.

A etapa `llm.stages.warroom_chat` é opcional: sem ela, o chat usa o modelo da etapa `investigation`.

**HTTP (`grolla-api`)**

| Rota | O que faz |
|---|---|
| `POST /incidents/{id}/actions/{acao}` | Recebe o form com `motivo`, `texto` ou `severidade`. Lê o autor do cookie `grolla_author`. Responde com o fragmento HTML do cabeçalho e das ações, ou 422 com a mensagem. |
| `POST /incidents/{id}/chat` | Recebe o form com `texto`. Responde com o fragmento do chat. |
| `GET /incidents/{id}/chat` e `GET /incidents/{id}/timeline` | Fragmentos para o polling de 3 s. |
| `POST /author` | Grava o cookie com o nome. |

O HTMX é servido como estático.

## Decisions

1. **Ações como decisões puras no domínio (`decide_action`),** como `decide_firing`. Toda a máquina de estados é testável sem banco. *Mais simples considerada:* atualizar o estado direto na rota. Descartada porque espalharia as regras e perderia os eventos.
2. **Lock do incidente na ação (`incident_for_update`)** para duas pessoas não assumirem ao mesmo tempo. Vale a mesma lição do agrupamento concorrente.
3. **Chat por fila no Postgres e worker,** como a análise e a validação. *Mais simples considerada:* responder na própria requisição HTTP. Descartada porque uma investigação de até 2 minutos travaria a página, e as respostas se perderiam num restart.
4. **Polling HTMX de 3 s em vez de SSE.** *Mais simples considerada e escolhida:* o SSE exige um canal de eventos entre o worker e a api. Com polling, o próprio banco é a fonte.
5. **Autor em cookie, sem JS próprio.** O nome entra num form uma vez e vale para todas as ações. Não é autenticação, como define a fase 1.
6. **O agente só responde no chat.** Mudanças de estado, severidade e hipóteses continuam humanas. O prompt pode sugerir uma ação, e o humano usa o botão.
7. **Contexto do chat montado pela application (`chat_context`)**: o incidente, a análise, a validação, os últimos 30 eventos da timeline e as últimas 20 mensagens do chat, num texto compacto. O LLM fake lê a linha `Serviços:`, como na análise.
8. **Encerramento com motivo obrigatório**, porque vira material do post mortem (S6). **Rebaixar a severidade é livre**, porque é um humano decidindo e o autor fica registrado.
9. **Card de encerramento** reaproveitando `Notification.verification` para a causa raiz. As ações registradas entram no `evidence` como lista compacta.

## Divisão de trabalho

| Papel | Entrega |
|---|---|
| agente pai | Commit de contrato, `bootstrap` (limites do chat, etapa `warroom_chat`), HTMX estático, compose e e2e config, integração e laboratório |
| `grolla-dominio` | `decide_action` e a máquina de estados, `OPEN_STATES` no agrupamento, `act`, `ask`, `run_next_reply`, `reap_stuck_replies`, `chat_context`, `CHAT_PROMPT`, fakes e suítes de contrato |
| `grolla-persistencia` | Migração 0005, campos novos do incidente, `incident_for_update`, `chat_messages`, `ChatQueue`, `chat_of`, `active_incidents` só com estados abertos |
| `grolla-integracoes` | `reply` no adapter da Anthropic, modo chat no LLM fake, cards novos no Teams, jobs do chat no worker |
| `grolla-web` | Cabeçalho, ações, chat e polling com HTMX, cookie do autor e console |
| `grolla-e2e` | Cenários: assumir e encerrar com card; ação inválida recusada; chat com resposta e consulta; chat com LLM fora do ar; alerta depois do encerramento abre incidente novo |

## Risks / Trade-offs

- [Nome livre sem autenticação] → só rede interna. Fica registrado em toda ação. O SSO é da fase 2.
- [Custo do chat] → teto por resposta e por incidente, com o uso em `llm_usage` na etapa `warroom_chat`.
- [Pergunta durante a validação] → o contexto usa o que já existe; uma validação em andamento aparece como "em andamento".
- [Polling a cada 3 s com muitas abas abertas] → as consultas são por incidente e leves. Se pesar, troca-se por SSE.

## Migration Plan

Merge depois do S3.1 e da normalização. A migração 0005 é aditiva. Incidentes antigos ficam com `commander` nulo e continuam no estado em que estão.

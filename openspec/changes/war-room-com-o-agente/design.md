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

## Desvios de contrato combinados na implementação

1. **Evento `agent_message`.** Leva `{reply_to, ok, text}`, sem `message_id`, porque o id da resposta só existe depois do INSERT dentro do `answer`.
2. **Status da resposta.** O `ChatQueue.answer` lê o desfecho em `reply.reply_status`: `FAILED` vira `falhou`, e `None` ou `ANSWERED` vira `respondida`. Esse status é gravado na pergunta. A resposta fica com `reply_status` nulo e `reply_to` apontando para a pergunta.
3. **`ask` em incidente inexistente** levanta `IncidentNotFoundError`, como o `act`. Texto vazio levanta `InvalidActionError`.
4. **Alerta agrupado não mexe em quem conduz.** Ele só reclassifica o estado de incidentes Detectados ou Informados; Em tratamento e Mitigado ficam como o humano deixou. Um alerta resolvido em incidente encerrado só é registrado, sem card.
5. **O IC atual assumir de novo é recusado** ("Ana já é o IC"). Outra pessoa pode assumir a qualquer momento, e o war room esconde o botão só para o próprio IC (code review do agente pai).
6. **Formato dos cards.**
   - Encerramento: `summary` = "Encerrado por Ana · durou X"; `evidence` = "Motivo: …", seguido de "Ações do time:" com uma linha por ação; `verification` anexada.
   - Ações: "Ana assumiu como IC", "P1 → P3 por Ana" e "Mitigado por Ana".
7. **Mensagem do chat.** Vai ao modelo como `f"{context}\n\nPergunta: {question}"`, e o resultado vem pelo schema `{"reply"}`. O LLM fake reconhece a fase pela propriedade do schema: `verdicts`, `reply` ou análise.
8. **Polling com `?visto=<versão>`.** Sem novidade, a resposta é 204 e o HTMX não troca nada. Assim um `<details>` aberto não fecha a cada 3 s.
9. **Testes da web.** Usam um espião sobre o `act` e o `ask` reais, em vez do stub provisório (code review do agente pai).

## Design da página (revisão de UX)

Pedido do usuário depois do teste funcional: UX fácil, amigável, moderna, com referência ao Grolla, e chat com cara de chat.

- **Identidade.** A paleta sai do mascote:
  - azul da camiseta (`#1f5fbf`) para a marca e o agente;
  - grafite da barba (`#23262d`) para o texto;
  - um tom claro e frio (`#f3f5f8`) para o fundo.

  Severidade e estado têm cores próprias e contidas. A tipografia é Bricolage Grotesque nos títulos e Figtree no texto, com fallback do sistema caso a rede bloqueie as fontes.
- **Elemento marcante.** A "linha de batimento" da camiseta do Grolla vira o pulso do incidente, sob o título: em vermelho enquanto aberto, em âmbar mitigado e reta verde quando resolvido. É o único elemento ousado; o resto é sóbrio.
- **Layout.** Duas colunas no desktop: à esquerda, a condução (cabeçalho, ciclo, ações, causa raiz, análise, validação, alertas e timeline); à direita, o chat fixo na altura da tela, porque a conversa é o centro do war room. No celular, uma coluna, com o chat logo depois das ações.
- **Ações.** A ação principal do estado fica em destaque. Severidade vira um seletor P0 a P4. Encerrar e descartar abrem um popover nativo (`popover`) com o motivo. Descartar hipótese mora na própria hipótese.
- **Chat.** Bolhas: as de quem vê à direita, as dos outros à esquerda, e o Grolla com avatar. A resposta do agente é renderizada com um markdown mínimo e seguro (negrito, listas e links), feito no servidor depois do escape. Há indicador "investigando…" e sugestões de perguntas, que são forms com o texto pronto.
- **JS.** Além do HTMX, um arquivo pequeno (`static/war-room.js`) só para Enter enviar, a caixa crescer e a rolagem automática. Sem framework.
- **Orçamento do chat (achado no laboratório).** Cada volta do laço reenvia a conversa inteira, e o orçamento conta o acumulado. Com 60 mil tokens, a resposta morria em "orçamento esgotado" depois de umas 7 consultas.
  - Correção: no chat, quando o que sobra não paga duas voltas, a última chamada vai com `tool_choice: none` e um aviso para responder com o que já consultou.
  - A análise e a validação não mudam.
  - Alternativa descartada: só subir o limite, que adia o problema e encarece a resposta.
- **Respostas curtas (pedido do usuário depois do teste).** As respostas tinham de 150 a 250 palavras, com ressalvas e método, e atrapalhavam o war room. O prompt do chat agora fixa o formato: uma frase de resposta, até 3 evidências, uma sugestão, cerca de 60 palavras, e só as consultas necessárias. Alternativa descartada: limitar `max_tokens`, porque a resposta sairia cortada no meio em vez de curta.
- **Alternativa mais simples considerada:** só reestilizar o CSS atual. Foi descartada porque não resolve o "próximo passo" nem a cara de chat, que dependem da estrutura.

## Risks / Trade-offs

- [Nome livre sem autenticação] → só rede interna. Fica registrado em toda ação. O SSO é da fase 2.
- [Custo do chat] → teto por resposta e por incidente, com o uso em `llm_usage` na etapa `warroom_chat`.
- [Pergunta durante a validação] → o contexto usa o que já existe; uma validação em andamento aparece como "em andamento".
- [Polling a cada 3 s com muitas abas abertas] → as consultas são por incidente e leves. Se pesar, troca-se por SSE.

## Migration Plan

Merge depois do S3.1 e da normalização. A migração 0005 é aditiva. Incidentes antigos ficam com `commander` nulo e continuam no estado em que estão.

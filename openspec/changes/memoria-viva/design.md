## Context

O log de eventos guarda cada incidente desde o S2, mas nada volta para o agente: cada análise,
validação e resposta do chat começa do zero e sem conhecer o ambiente. O usuário pediu uma memória
viva que:
- comece com uma base de conhecimento do ambiente, montada pelo próprio agente na primeira subida,
  na ordem **alertas do domínio → serviços → repositórios → pipelines → infraestrutura AWS →
  integrações nos traces**;
- cresça com os incidentes, o chat e o que o time escreve;
- possa ser reaprendida do zero quando pedirem.

Ele aprende sozinho e o time corrige.

Restrições: só Anthropic como LLM (sem embeddings); crédito curto no laboratório; somente leitura
nos sistemas do time (a única escrita é na própria memória); memória e conteúdo lido são dado,
nunca instrução.

## Goals / Non-Goals

**Goals:**
- Dossiês por assunto (ambiente, serviço, integração), episódios e notas, com fonte, confiança e
  versões.
- Estudo inicial, estudo diário e "reaprender do zero", com teto de tokens.
- Aprender ao encerrar (uma chamada) e no chat (`remember`).
- Memória entregue à análise, à validação e ao chat; "já vi isso" na análise.
- Página `/memoria` para o time ler, corrigir, semear e pedir estudo.

**Non-Goals:** os da proposta.

## Contratos

```python
# grolla.domain.memory (novo): registros e regras puras
AGENT_AUTHOR = "Grolla"
ENVIRONMENT = "ambiente"  # nome do assunto único do tipo ambiente


class SubjectKind(StrEnum):
    ENVIRONMENT = "ambiente"
    SERVICE = "servico"
    INTEGRATION = "integracao"


class MemoryKind(StrEnum):
    DOSSIER = "dossie"
    EPISODE = "episodio"
    NOTE = "nota"


class Confidence(StrEnum):
    LEARNED = "aprendido"
    CONFIRMED = "confirmado"


class SourceKind(StrEnum):
    STUDY = "estudo"  # ref = links consultados, separados por linha
    INCIDENT = "incidente"  # ref = id do incidente
    CHAT = "chat"  # ref = id do incidente; author = quem escreveu a mensagem
    PERSON = "pessoa"  # ref = None; author = quem escreveu na página


@dataclass(frozen=True)
class Source:
    kind: SourceKind
    author: str
    ref: str | None = None


@dataclass(frozen=True)
class Memory:
    id: UUID
    kind: MemoryKind
    subject: str
    subject_kind: SubjectKind
    title: str
    body: str
    confidence: Confidence
    source: Source
    created_at: datetime
    updated_at: datetime
    active: bool = True
    consolidated: bool = False  # nota já incorporada ao dossiê
    noise: bool = False  # episódio de alarme falso
    incident_id: UUID | None = None  # episódio: um por (incidente, assunto)


@dataclass(frozen=True)
class MemoryVersion:
    memory_id: UUID
    at: datetime
    author: str
    reason: str
    body: str  # texto ANTERIOR à mudança
    active: bool  # estado ANTERIOR (restaurar volta a ele)


def written(memory, body, author, reason, now) -> tuple[Memory, MemoryVersion]:
    """Nova versão; autor humano deixa confirmado, o agente mantém a confiança atual."""


def confirmed(memory, author, now) -> tuple[Memory, MemoryVersion]: ...
def forgotten(memory, author, reason, now) -> tuple[Memory, MemoryVersion]: ...
def restored(memory, version, author, now) -> tuple[Memory, MemoryVersion]: ...
def relearn_targets(records: Sequence[Memory]) -> list[Memory]:
    """Dossiês e notas aprendidas ativos; episódios e notas confirmadas ficam."""


# grolla.domain.analysis (acréscimos)
@dataclass(frozen=True)
class SeenBefore:
    incident_id: UUID
    title: str
    detected_at: datetime
    reason: str


# AnalysisDraft e Analysis ganham seen_before: SeenBefore | None = None; complete(...) recebe os
# episódios entregues e descarta a referência fora deles.


# grolla.application.investigation (acréscimos)
@dataclass(frozen=True)
class AlertRule:
    name: str
    condition: str  # texto legível da condição
    severity: str
    labels: Mapping[str, str]
    service: str  # pelos service_labels do YAML; UNKNOWN_SERVICE se não houver
    url: str | None


# Telemetry ganha:
async def alert_rules(self) -> list[AlertRule]: ...  # todas; o caso de uso filtra pelo domínio
async def active_services(self, since: datetime) -> list[str]: ...
async def dependencies(self, service: str, since: datetime) -> ToolResult: ...
# CloudInventory ganha:
async def overview(self) -> ToolResult: ...
# ChangeHistory ganha:
async def read_file(
    self, service: str, path: str
) -> ToolResult: ...  # READ_LIMIT = 20_000 caracteres


# InvestigationBrief ganha memory: MemoryContext = MemoryContext()
# Toolbox ganha memory: MemoryStore | None e remember_as: str | None (só no chat).
# Ferramentas novas: recall(subject, query), read_file(service, path),
# service_dependencies(service), cloud_overview(), remember(subject, text) (só no chat).


# grolla.application.memory (novo)
@dataclass(frozen=True)
class MemoryContext:
    dossiers: tuple[Memory, ...] = ()  # ambiente, serviços do incidente e integrações citadas
    notes: tuple[Memory, ...] = ()  # ativas, não consolidadas, desses assuntos
    episodes: tuple[Memory, ...] = ()  # até 5 parecidos


def memory_text(context: MemoryContext) -> str:
    """Bloco <memoria> marcado como dado, com tipo, confiança, data e link de cada registro."""


class MemoryStore(Protocol):
    async def context_for(
        self, services: Sequence[str], alert_names: Sequence[str], text: str
    ) -> MemoryContext: ...
    async def search(self, subject: str | None, query: str, limit: int) -> list[Memory]: ...
    async def get(self, memory_id: UUID) -> Memory | None: ...
    async def dossier(self, subject: str) -> Memory | None: ...
    async def episode_of(self, incident_id: UUID, subject: str) -> Memory | None: ...
    async def all_active(self) -> list[Memory]: ...
    async def save(self, records: Sequence[tuple[Memory, MemoryVersion | None]]) -> None:
        """Grava registros e versões numa transação."""

    async def subjects(self) -> list[SubjectSummary]: ...
    async def of_subject(self, subject: str) -> list[Memory]: ...  # inclusive esquecidos
    async def versions(self, memory_id: UUID) -> list[MemoryVersion]: ...


@dataclass(frozen=True)
class SubjectSummary:
    subject: str
    kind: SubjectKind
    records: int
    updated_at: datetime
    studied_at: datetime | None


class LearningQueue(Protocol):  # mesmo padrão do AnalysisQueue
    async def claim(self, now: datetime) -> LearningJob | None: ...
    async def stuck(self, started_before: datetime) -> list[LearningJob]: ...
    async def finish(
        self,
        job: LearningJob,
        ok: bool,
        reason: str | None,
        usages: Sequence[LlmUsage],
        now: datetime,
    ) -> None:
        """ok=False com attempts < 2 volta para pendente; senão fica falho."""

    async def failed(self) -> list[LearningFailure]: ...


class StudyMode(StrEnum):
    INITIAL = "inicial"  # base vazia na subida
    DAILY = "diario"
    FULL = "completo"  # pedido na página, mantém a memória
    RELEARN = "reaprender"  # pedido na página, esquece antes


class StudyQueue(Protocol):
    async def request(self, mode: StudyMode, author: str, now: datetime) -> None:
        """Ignora se já há rodada pendente ou rodando (exceto RELEARN, que a substitui)."""

    async def claim(self, now: datetime) -> StudyRun | None: ...
    async def stuck(self, started_before: datetime) -> list[StudyRun]: ...
    async def service_order(self, services: Sequence[str], mode: StudyMode) -> list[str]:
        """Não estudados e os que ficaram sem orçamento primeiro, depois os mais antigos;
        FULL e RELEARN zeram a ordem."""

    async def service_done(
        self,
        run: StudyRun,
        service: str,
        ok: bool,
        reason: str | None,
        usages: Sequence[LlmUsage],
        now: datetime,
    ) -> None: ...
    async def finish(self, run: StudyRun, usages: Sequence[LlmUsage], now: datetime) -> None: ...
    async def last(self) -> StudyRun | None: ...  # para a página


@dataclass(frozen=True)
class ServiceStudyBrief:
    service: str
    rules: tuple[AlertRule, ...]
    memory: MemoryContext  # dossiê atual, notas e episódios do serviço
    now: datetime


@dataclass(frozen=True)
class SynthesisBrief:
    rules: tuple[AlertRule, ...]
    services: tuple[Memory, ...]  # dossiês dos serviços
    memory: MemoryContext  # dossiês atuais de ambiente e integrações, notas, episódios
    cloud: str  # resumo de cloud_overview
    now: datetime


@dataclass(frozen=True)
class DossierDraft:
    subject: str
    subject_kind: SubjectKind
    body: str
    sources: tuple[str, ...]  # links consultados


@dataclass(frozen=True)
class SynthesisDraft:
    environment: DossierDraft
    integrations: tuple[DossierDraft, ...]


@dataclass(frozen=True)
class LearningBrief:
    incident: Incident
    events: tuple[IncidentEvent, ...]
    memory: MemoryContext


@dataclass(frozen=True)
class LearningDraft:
    episode_title: str
    episode_body: str
    noise: bool
    dossiers: tuple[DossierDraft, ...]  # serviços e integrações atualizados


# LanguageModel ganha:
async def learn(self, brief: LearningBrief, budget: TokenBudget) -> LearningDraft: ...
async def study_service(
    self, brief: ServiceStudyBrief, toolbox: Toolbox, budget: TokenBudget
) -> DossierDraft: ...
async def synthesize(self, brief: SynthesisBrief, budget: TokenBudget) -> SynthesisDraft: ...


STAGE_LEARNING = "learning"
STAGE_STUDY = "study"

# IncidentUnitOfWork ganha request_learning(incident_id, at), chamado na transação de
# INCIDENT_RESOLVED/INCIDENT_DISCARDED.

# Casos de uso: run_next_learning, reap_stuck_learnings, run_next_study (rodada inteira),
# reap_stuck_studies, ensure_initial_study (na subida do worker), request_study,
# add_note, edit_memory, confirm_memory, forget_memory, restore_memory.
```

**Schema (migração 0006):**
- `memories`: `id`, `kind`, `subject`, `subject_kind`, `title`, `body`, `confidence`,
  `source_kind`, `source_author`, `source_ref`, `incident_id`, `active`, `consolidated`, `noise`,
  `created_at`, `updated_at`.
  - coluna gerada `search tsvector = to_tsvector('portuguese', title || ' ' || body)` com índice
    GIN;
  - índice único parcial do dossiê ativo por `subject`;
  - índice único de `(incident_id, subject)` para episódios.
- `memory_versions` (memory_id, at, author, reason, body, active), só inserção.
- `learnings` (incident_id PK, status, attempts, requested_at, started_at, finished_at, reason).
- `study_runs` (id, mode, author, status, requested_at, started_at, finished_at, tokens, reason)
  e `service_studies` (service PK, studied_at, status, reason).
- O uso do LLM vai para a tabela de uso existente com as etapas `learning` e `study`.

**Config (`memory`):**
- `learning_max_tokens`, padrão 60000;
- `study`, opcional: `cron`, `run_max_tokens`, `service_max_tokens` e `lookback_days`
  (padrão 7);
- `llm.stages.learning` e `llm.stages.study` herdam de `investigation`.

**Endpoints (grolla-api):**

| Rota | O que faz |
|---|---|
| `GET /memoria` | página principal da memória |
| `GET /memoria/{subject}` | página de um assunto |
| `POST /memoria/{subject}/notas` | adiciona uma nota |
| `POST /memoria/registros/{id}` | edita um registro |
| `POST /memoria/registros/{id}/confirmar` | confirma um registro |
| `POST /memoria/registros/{id}/esquecer` | esquece um registro |
| `POST /memoria/registros/{id}/restaurar` | restaura uma versão |
| `GET /memoria/registros/{id}/versoes` | fragmento com o histórico de versões |
| `POST /memoria/estudar` | estudo completo |
| `POST /memoria/reaprender` | reaprende do zero, com confirmação |

Escrita exige autor identificado (cookie `grolla_author`).

**Worker:**
- `run_learnings` (a cada 10 s);
- `run_studies` (a cada 1 min; pega a rodada pendente);
- `schedule_daily_study` (cron do YAML, pede `DAILY`);
- `ensure_initial_study` (na subida, pede `INITIAL` se não há dossiê);
- os reapers junto com os existentes.

### Ajustes do commit de contrato

O código do commit de contrato é a fonte de verdade; ele difere do bloco acima em:
- `MemoryContext`, `memory_text`, `DossierDraft`, `SynthesisDraft` e `LearningDraft` ficam em
  `grolla.domain.memory` (dados puros); `MemoryStore` e `SubjectSummary` em
  `grolla.application.memory_store`, para o `Toolbox` e os casos de uso usarem o mesmo port sem
  import circular; os briefs (`LearningBrief`, `ServiceStudyBrief`, `SynthesisBrief`) e
  `AlertRule` em `grolla.application.investigation`.
- O modelo devolve `SeenBeforeDraft(incident_id, reason)`; `complete(..., episodes)` monta o
  `SeenBefore(incident_id, title, at, reason)` a partir do episódio entregue.
- `Telemetry.dependencies(service, start, end)`, com janela como as outras consultas.
- `StudyQueue.service_done(run, ServiceStudy, usages)`, `finish(run, status, reason, now)` e
  `services()`; `request` devolve se pediu. `llm_usage` ganha `study_run_id`.
- `Toolbox(..., *, memory=None, remember_as: Source | None = None)`: `remember_as` é a fonte
  das notas do chat (tipo `chat`, autor da pergunta, id do incidente).
- `MemoryPolicy` e `StudyPolicy` (com o `alert_filter` do time) em `grolla.application.memory`;
  `TeamConfig.memory_policy()`.

## Decisions

**1. Assuntos com dossiê, não lista de fatos.** Uma lista de centenas de fatos atômicos vira
contexto ruim e caro. O que um SRE veterano mantém é um caderno por sistema e um mapa do
ambiente. Por isso cada assunto (ambiente, serviço, integração) tem um dossiê com seções fixas.
Notas são a entrada rápida (chat, página) até serem incorporadas; episódios são o histórico.
*Mais simples considerada:* só episódios — descartada porque não guarda o conhecimento do
ambiente, que é o centro do pedido.

**2. Estudo orquestrado pelo código, não um agente solto.** A ordem pedida vira o caso de uso
`run_next_study`:
1. regras de alerta, sem LLM, filtradas pelo `alert_filter`;
2. serviços dessas regras, mais os ativos com repositório;
3. uma investigação com ferramentas por serviço, com teto próprio: `read_file` para repositório e
   pipeline, `recent_deploys`, `cloud_resources`/`cloud_changes`/`cloud_alarms`,
   `service_dependencies`, `query_metric` para o normal;
4. uma síntese sem ferramentas.

Assim o custo é previsível, um serviço que falha não derruba os outros e a rodada retoma de onde
parou. *Alternativa:* um único laço do LLM explorando tudo — descartada por custo imprevisível,
sem retomada, e porque o contexto estoura em ambientes com vários serviços.

**3. Integrações vêm dos traces.** `service_dependencies` agrega os spans de saída por
`peer.service`, `db.system`/`db.name` e `server.address`. O estudo de cada serviço lista as
dependências; a síntese cria um dossiê por integração. *Alternativa:* integrações escritas à mão
— descartada; o time pode completar pela página.

**4. Regras de alerta, serviços ativos e dependências no port `Telemetry`; visão geral no
`CloudInventory`; `read_file` no `ChangeHistory`.** Mesmos fornecedores, mesmos tokens e mesmas
suítes de contrato. O adapter do SigNoz ganha as leituras `signoz_list_alert_rules`,
`signoz_get_alert` e `signoz_list_services` na lista fechada (`READ_TOOLS`). O da AWS usa o
Resource Groups Tagging API e os `describe_*` que já usa. *Alternativa:* ports novos
(`AlertSource`, `SourceCode`) — descartada por não terem segundo uso hoje.

**5. Reaprender = esquecer + estudo completo.** Esquece dossiês e notas aprendidas, com versão;
mantém episódios (fatos do que aconteceu) e notas confirmadas (o que o time ensinou), que a
síntese reincorpora. Nada é apagado, e tudo pode ser restaurado. *Alternativa:* `DELETE` em
tudo — descartada; perderia o que o time ensinou e não teria volta.

**6. Aprender e consolidar numa chamada só, sem ferramentas, ao encerrar.** O log de eventos já
tem tudo; a chamada recebe o log, os dossiês envolvidos e as notas pendentes, e devolve o
episódio e os dossiês reescritos. *Alternativa:* um job de "sono" periódico — descartado por
gastar tokens sem gatilho.

**7. Confiança em dois níveis, sem aprovação.** `aprendido` (agente) e `confirmado` (humano). O
prompt preserva o confirmado e registra contradição. *Alternativa:* nota numérica — descartada,
porque ninguém calibra números.

**8. Busca determinística + full-text do Postgres; o próprio LLM escolhe.**
- O contexto entregue à análise leva sempre o dossiê do ambiente, os dos serviços do incidente e
  os das integrações que eles citam.
- Os episódios são ordenados por mesmo serviço e mesma regra de alerta, depois por `ts_rank` com
  `websearch_to_tsquery('portuguese')`.
- Não há chamada extra de reordenação.

*Alternativa:* embeddings — descartada (só Anthropic).

**9. "Já vi isso" como campo opcional da análise, validado** contra os episódios entregues.
*Mais simples considerada:* texto livre no resumo — descartada, porque o card e a página precisam
do link.

**10. `remember` só no chat, gravando na hora.** É a única ferramenta que escreve, e escreve só
na memória do agente.

**11. Memória e conteúdo lido sempre como dado.** `memory_text` e o resultado de `read_file` vão
num bloco marcado como dado; o prompt diz que instruções ali dentro não valem.

**12. Tamanho dos dossiês.** Seções fixas e limite no prompt: cerca de 800 palavras para serviço
e ambiente e 300 para integração. O contexto da análise fica previsível: com 5 serviços, cerca de
6k tokens de memória.

## Divisão de trabalho

| Papel | Entrega |
|---|---|
| `grolla-dominio` | `domain/memory.py`, `SeenBefore`, `AlertRule`, `application/memory.py` (estudo, reaprender, aprendizado, página, prompts `STUDY_PROMPT`, `SYNTHESIS_PROMPT`, `LEARNING_PROMPT`), ferramentas novas no `Toolbox`, memória no brief da análise, validação e chat, `request_learning` no encerramento, fakes e suítes de contrato, jobs do worker |
| `grolla-persistencia` | migração 0006, `MemoryStore`, `LearningQueue`, `StudyQueue` e `request_learning` no Postgres |
| `grolla-integracoes` | Anthropic (`learn`, `study_service`, `synthesize`, `seen_before`); SigNoz (`alert_rules`, `active_services`, `dependencies`); AWS (`overview`); Azure DevOps (`read_file`); protocolos no `grolla-fakes` e LLM fake; "Já vi isso" no card do Teams |
| `grolla-web` | `/memoria` e `/memoria/{subject}` com todas as ações, versões, estado do estudo e falhas; "Já vi isso" na página do incidente; link "Memória" na barra |
| `grolla-e2e` | cenários de estudo inicial, encerramento vira episódio, "já vi isso", nota da página e reaprender |

O agente pai faz o contrato, a config, o bootstrap, os evals, o roadmap e a integração.

## Desvios de contrato combinados na implementação

**Núcleo (grolla-dominio):**
- `memory_for` fica em `grolla.application.memory_store`, porque `application.memory` importa
  `application.analysis` e a análise precisa da memória.
- `run_next_analysis`, `run_next_verification` e `run_next_reply` recebem `memory=` (keyword),
  e o helper `analysis.investigation_brief` monta o brief.
- `chat_context(..., memory=)`.
- A síntese grava o uso como um `ServiceStudy("ambiente", ...)`, então `services()` mostra também
  quando a síntese rodou.
- Constantes `TRUNCATED`, `BINARY_FILE` e `SHARED_RESOURCES` ("sem serviço"), e as seções dos
  dossiês, no `application.investigation`.
- Nota num assunto sem dossiê é gravada como serviço, a menos que a página diga o tipo.
- O estudo só repete a síntese quando algum dossiê de serviço mudou ou não há dossiê do
  ambiente.
- A rodada presa vence após `STUDY_STALE`, de 2 h.

**Persistência:**
- `memories.incident_id` não tem FK: o episódio é registro da memória e as suítes de contrato
  usam ids soltos.
- Índice único `study_runs_one_pending`: uma só rodada pendente, mesmo com pedidos simultâneos.
- `study_runs` não tem a coluna `tokens`: o valor é a soma do `llm_usage` da rodada (coluna
  `study_run_id`), e uma rodada em andamento já mostra o parcial.

**Integrações:**
- `alert_rules` omite as regras desligadas.
- As fontes do estudo de um serviço são os links das avaliações, nunca links escritos pelo
  modelo.
- `SignozTelemetry` recebe os `service_labels` do YAML.
- O link "Já vi isso" no Teams troca o id do `page_url` do card pelo do incidente passado.

**Code review do pai:** o contrato não previa onde guardar o "já vi isso". A migração 0006
ganhou `analyses.seen_before jsonb`, e a análise da outbox também o carrega (antes, a página e
o card perdiam a referência; o cenário e2e pegou). Teste de integração cobre os dois.

**Web:**
- Sucesso devolve 303 para a página, ou 204 com `HX-Redirect` no HTMX.
- Sem autor: 401. Erro de domínio: 422. Estudo já na fila: 409.
- Reaprender exige `confirmar=sim`.
- `/memoria?assunto=x` leva para `/memoria/x`.
- Sem `memory.study`, a página diz que o estudo está desligado e esconde os botões.
- `rich_text` passou a tratar `#` e `##` como título de seção.

## Risks / Trade-offs

- [Agente aprende algo errado e repete] → cada registro mostra confiança e fonte; o time
  esquece, corrige ou reaprende; o confirmado prevalece; episódio é sempre hipótese a confirmar.
- [Custo do estudo inicial em ambiente grande] → teto por rodada e por serviço; o que não coube
  fica para a próxima rodada; o estudo vem desligado sem `memory.study`. No laboratório
  (1 serviço) a estimativa é de 150k tokens por rodada.
- [Prompt injection pelo README, pelos logs ou pela memória] → bloco de dados marcado; as
  ferramentas são somente leitura, e `remember` só escreve na memória; a memória não muda regra
  nem aciona ninguém.
- [Regras de alerta sem label de serviço] → o serviço vem de `service_labels`; sem ele a regra
  entra no dossiê do ambiente como "sem serviço".
- [Busca textual perde casos com palavras diferentes] → o critério serviço + regra pega a
  maioria; os evals medem.
- [Dossiê desatualizado] → o estudo diário reestuda os mais antigos; a data aparece no dossiê.

## Migration Plan

A migração 0006 só cria tabelas. Na primeira subida com `memory.study`, o worker pede o estudo
inicial. Os incidentes já encerrados não viram episódio. Rollback: tirar `memory.study` desliga o
estudo; o aprendizado não afeta incidentes.

## Open Questions

- Labels reais das regras de alerta da Credenciadora (o filtro do domínio usa o mesmo
  `alert_filter`).

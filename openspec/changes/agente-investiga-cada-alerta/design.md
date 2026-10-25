## Context

Depois do S2, todo alerta da Credenciadora vira um incidente deduplicado e agrupado, com card no Teams, página e log de eventos imutável. Mas o card repete o que o SigNoz já manda. O S3 acrescenta a investigação com LLM: um card "investigando" na hora e, em até 5 minutos, o card de análise.

Decisões já tomadas com o usuário:
- **Escopo:** todo incidente é analisado, e P0/P1 vão mais fundo depois (S5). O botão "Abrir incidente" leva à página do incidente.
- **SigNoz via MCP.** O servidor oficial `signoz-mcp-server` tem transporte HTTP e autenticação pelo header `SIGNOZ-API-KEY`. Ele expõe mais de 60 ferramentas, inclusive de escrita, como `signoz_create_dashboard` e `signoz_delete_alert`. No futuro o agente vai criar dashboards.
- **Azure DevOps** com token somente leitura.
- **Chave da Anthropic** no ambiente da EC2.

É uma change multiagente.

## Goals / Non-Goals

**Goals:**
- O canal recebe "investigando" em segundos e a análise (o que avaliou, hipóteses, plano) em até 5 minutos, para todo incidente novo.
- A análise sempre chega: falha, timeout, orçamento ou queda do worker viram análise parcial, nunca silêncio.
- Fornecedores isolados atrás de `LanguageModel`, `Telemetry` e `ChangeHistory`. As ferramentas, o prompt e os evals são neutros.
- Testável sem nenhum serviço real: fakes no e2e e no console, e um LLM roteirizado.

**Non-Goals:** ver a proposta (investigação profunda, incidentes parecidos, leitura de código, AWS, escrita no SigNoz, Spike.sh, validação de modelos no startup).

## Contratos

Escritos como código pelo agente pai no commit de contrato. Os corpos ficam com `raise NotImplementedError` até o dono implementar.

### Domínio — `grolla.domain.analysis` (novo) e `grolla.domain.incidents` (alterado)

```python
# grolla.domain.analysis
INITIAL_CHECKS = ("métricas do serviço", "deploys recentes", "traces com erro", "logs de erro")


class AnalysisStatus(StrEnum):
    PENDING = "pendente"
    RUNNING = "em_andamento"
    COMPLETED = "concluida"
    PARTIAL = "parcial"


class PartialReason(StrEnum):
    LLM_FAILED = "falha_do_llm"
    TIMEOUT = "tempo_esgotado"
    BUDGET = "orcamento_esgotado"
    INVALID_RESULT = "resultado_invalido"
    INTERRUPTED = "interrompida"


@dataclass(frozen=True)
class Evidence:
    text: str
    url: str | None


@dataclass(frozen=True)
class Evaluation:  # uma chamada de ferramenta realmente executada
    tool: str  # query_metric | search_logs | find_traces | recent_deploys
    subject: str  # ex.: "taxa_de_erro de authorizer-api, últimos 60 min"
    finding: str  # resumo devolvido pela ferramenta ou "não foi possível consultar"
    url: str | None
    ok: bool


@dataclass(frozen=True)
class Hypothesis:
    title: str
    confidence: float  # 0..1
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class AnalysisDraft:  # o que o LLM devolve, já validado
    summary: str
    hypotheses: tuple[Hypothesis, ...]
    plan: tuple[str, ...]


@dataclass(frozen=True)
class Analysis:
    incident_id: UUID
    status: AnalysisStatus
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    summary: str
    evaluations: tuple[Evaluation, ...]
    hypotheses: tuple[Hypothesis, ...]  # ordenadas por confiança, maior primeiro
    plan: tuple[str, ...]
    partial_reason: PartialReason | None


def complete(
    pending: Analysis, draft: AnalysisDraft, evaluations: Sequence[Evaluation], now: datetime
) -> Analysis: ...
def partial(
    pending: Analysis, reason: PartialReason, evaluations: Sequence[Evaluation], now: datetime
) -> Analysis: ...


# grolla.domain.incidents (alterações)
class EventType(StrEnum):
    ...  # os do S2, mais:
    ANALYSIS_REQUESTED = "analysis_requested"
    ANALYSIS_COMPLETED = "analysis_completed"
    ANALYSIS_PARTIAL = "analysis_partial"


class NotificationKind(StrEnum):
    INCIDENT_OPENED = "incident_opened"  # card "investigando"
    ANALYSIS_READY = "analysis_ready"
    SEVERITY_RAISED = "severity_raised"
    INCIDENT_NORMALIZED = "incident_normalized"


@dataclass(frozen=True)
class Notification:
    ...  # campos do S2, mais:
    analysis: Analysis | None = None  # só em ANALYSIS_READY


@dataclass(frozen=True)
class Decision:
    ...  # campos do S2, mais:
    request_analysis: bool = False  # True só quando o alerta abre um incidente
```

### Ports e casos de uso — `grolla.application`

```python
# grolla.application.investigation (novo)
class ToolUnavailableError(Exception): ...


@dataclass(frozen=True)
class ToolResult:
    summary: str  # texto compacto para o LLM, em português
    url: str | None  # link para a origem (SigNoz, Azure DevOps)


Signal = Literal["taxa_de_erro", "latencia_p99", "vazao"]


class Telemetry(Protocol):  # adapter: grolla.adapters.signoz (cliente MCP)
    async def query_metric(
        self, service: str, signal: Signal, start: datetime, end: datetime
    ) -> ToolResult: ...
    async def search_logs(
        self, service: str, text: str | None, start: datetime, end: datetime
    ) -> ToolResult: ...
    async def find_traces(
        self, service: str, only_errors: bool, start: datetime, end: datetime
    ) -> ToolResult: ...


class ChangeHistory(Protocol):  # adapter: grolla.adapters.azure_devops
    async def recent_deploys(self, service: str, since: datetime) -> ToolResult: ...


@dataclass(frozen=True)
class ToolSpec:  # definição neutra que o adapter do LLM traduz
    name: str
    description: str
    input_schema: Mapping[str, Any]  # JSON Schema


class Toolbox:  # concreto: liga as ferramentas neutras aos ports e registra as avaliações
    def __init__(
        self, telemetry: Telemetry, changes: ChangeHistory, clock: Clock, lookback: timedelta
    ) -> None: ...
    def specs(self) -> list[ToolSpec]: ...
    async def call(self, name: str, arguments: Mapping[str, Any]) -> str: ...  # nunca levanta
    @property
    def evaluations(self) -> tuple[Evaluation, ...]: ...


@dataclass(frozen=True)
class LlmUsage:
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int


class BudgetExceededError(Exception): ...


class TokenBudget:  # concreto: o adapter chama spend() a cada resposta
    def __init__(self, limit: int) -> None: ...
    def spend(self, usage: LlmUsage) -> None: ...  # levanta BudgetExceededError ao estourar
    @property
    def usages(self) -> tuple[LlmUsage, ...]: ...


@dataclass(frozen=True)
class InvestigationBrief:
    incident: Incident
    alerts: tuple[Alert, ...]
    now: datetime


class LanguageModelError(Exception): ...  # API fora, recusa, erro de modelo


class InvalidAnalysisError(Exception): ...  # resultado fora do schema


class LanguageModel(Protocol):  # adapter: grolla.adapters.anthropic
    async def investigate(
        self, brief: InvestigationBrief, toolbox: Toolbox, budget: TokenBudget
    ) -> AnalysisDraft: ...


SYSTEM_PROMPT: str  # roteiro curto em português, neutro (grolla.application.investigation_prompt)


def brief_text(brief: InvestigationBrief) -> str: ...  # mensagem do usuário montada do incidente


# grolla.application.analysis (novo)
@dataclass(frozen=True)
class AnalysisJob:
    incident_id: UUID
    requested_at: datetime
    started_at: datetime


class AnalysisQueue(Protocol):  # adapter: grolla.adapters.postgres
    async def claim(self, now: datetime) -> AnalysisJob | None: ...  # pendente → em_andamento
    async def stuck(self, started_before: datetime) -> list[AnalysisJob]: ...
    async def finish(
        self,
        analysis: Analysis,
        events: Sequence[IncidentEvent],
        notification: Notification,
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None: ...  # uma transação: análise, eventos, outbox e llm_usage


@dataclass(frozen=True)
class AnalysisPolicy:
    timeout: timedelta
    max_tokens: int
    lookback: timedelta  # janela das ferramentas
    page_url: Callable[[UUID], str]
    telemetry_url: str | None  # botão "Ver no SigNoz" do card


async def run_next_analysis(
    queue: AnalysisQueue,
    repository: IncidentRepository,
    model: LanguageModel,
    telemetry: Telemetry,
    changes: ChangeHistory,
    clock: Clock,
    policy: AnalysisPolicy,
) -> bool: ...  # False se não havia pedido
async def reap_stuck_analyses(
    queue: AnalysisQueue, repository: IncidentRepository, clock: Clock, policy: AnalysisPolicy
) -> int: ...


# grolla.application.incidents (alterações)
class IncidentUnitOfWork(Protocol):
    ...  # S2, mais:

    async def request_analysis(self, incident_id: UUID, at: datetime) -> None: ...


class IncidentRepository(Protocol):
    ...  # S2, mais:

    async def analysis_of(self, incident_id: UUID) -> Analysis | None: ...
```

O `receive_alerts` passa a chamar `request_analysis` quando `decision.request_analysis`, na mesma transação, e grava o evento `analysis_requested`.

O `run_next_analysis` funciona assim:
1. Reivindica um pedido e monta o `InvestigationBrief`.
2. Roda `model.investigate` dentro de `asyncio.timeout(policy.timeout)`.
3. Converte o resultado:
   - um `AnalysisDraft` vira `complete`;
   - `TimeoutError`, `BudgetExceededError`, `LanguageModelError` e `InvalidAnalysisError` viram `partial`, com o motivo correspondente.
4. Chama `queue.finish` com o evento (`analysis_completed` ou `analysis_partial`), o card `ANALYSIS_READY` e os usos do `budget`.

Qualquer outra exceção também vira `partial(LLM_FAILED)`, porque a análise sempre chega.

### Configuração — `grolla.bootstrap`

```yaml
# acréscimos ao YAML do time
investigation:
  timeout_seconds: 300  # o e2e usa um valor curto para o cenário de tempo esgotado
  max_tokens: 300000
  lookback_minutes: 60
  max_parallel: 3
llm:
  stages:
    investigation: { model: claude-sonnet-5-5, effort: medium, max_tokens: 16000 }
signoz:
  mcp_url: https://signoz-mcp.interno/mcp
  base_url: https://signoz.interno  # links "Ver no SigNoz"
azure_devops:
  base_url: https://dev.azure.com/minha-org
  service_repo_map:
    authorizer-api: { project: Core, repo: authorizer-api }
```

- **Ambiente do worker:** `GROLLA_CONFIG_FILE` (o worker passa a ler o YAML), `GROLLA_ANTHROPIC_API_KEY`, `GROLLA_SIGNOZ_API_KEY` e `GROLLA_AZURE_DEVOPS_TOKEN`.
- **Só no e2e:** `GROLLA_ANTHROPIC_BASE_URL`, opcional, aponta o SDK para o fake.
- **Montagem:** o `prepare_worker()` monta os três adapters, a `AnalysisQueue` e a `AnalysisPolicy`.

### Schema — migração `0003`

- `analyses`:
  - colunas: `incident_id uuid pk fk`, `status`, `requested_at`, `started_at`, `finished_at`, `summary`, `evaluations jsonb`, `hypotheses jsonb`, `plan jsonb` e `partial_reason`;
  - índice parcial em `status = 'pendente'`;
  - a reivindicação usa `FOR UPDATE SKIP LOCKED`.
- `llm_usage`: `id bigserial`, `incident_id fk`, `stage`, `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens` e `at`.

### Worker

- O processo roda **dois laços do procrastinate**:
  - fila `default` com concorrência 1: `heartbeat` e `deliver_notifications`;
  - fila `analysis` com concorrência `investigation.max_parallel`, onde ficam o job periódico `run_analyses` (a cada 5 s, com `queueing_lock`) e o `reap_stuck_analyses`.
- Uma análise de 5 minutos nunca atrasa o heartbeat nem a entrega dos cards.

### HTTP — `grolla-api`

`GET /incidents/{id}` ganha a seção de análise. Nenhum endpoint novo.

### Fakes e e2e

| Fake | Rota | Comportamento |
|---|---|---|
| LLM | `POST /anthropic/v1/messages` | Formato da Messages API. O roteiro por cenário tem os modos `completa` (padrão), `fora_do_ar` (529) e `lenta` (espera além do timeout do e2e). O modo `completa` primeiro pede `query_metric`, `recent_deploys` e `search_logs`, depois devolve o JSON final usando os resultados recebidos. |
| MCP do SigNoz | `/signoz/mcp` | Servidor MCP streamable HTTP, feito com a lib `mcp`, montado no app. Expõe só as ferramentas de leitura usadas pelo adapter, com respostas roteirizadas e um padrão plausível. |
| Azure DevOps | `GET /azure-devops/{org}/{project}/_apis/build/builds` | Builds roteirizados, com um padrão com 1 deploy recente. |

- **Configuração do e2e:** `investigation.timeout_seconds` curto em `e2e/config.yaml`, para o cenário de tempo esgotado rodar em segundos.
- **Console:** ganha o seletor do roteiro do LLM.

## Decisions

1. **Análise assíncrona por fila no Postgres (`analyses`) e job no worker.**
   - *Mais simples considerada:* investigar dentro da requisição do webhook. Descartada: o SigNoz espera resposta rápida, e uma análise de minutos travaria o webhook e se perderia num restart.
   - A tabela é ao mesmo tempo fila e projeção, como a outbox.
2. **"O que avaliei" vem do `Toolbox`, não do LLM.** Cada chamada real de ferramenta vira uma `Evaluation`, e o LLM só interpreta. *Mais simples considerada:* pedir a lista ao LLM no JSON final. Descartada porque o LLM pode dizer que olhou o que não olhou, e na análise parcial não haveria resposta do LLM para usar.
3. **Laço de ferramentas manual (`messages.create` + `tools` + `output_config.format`) em vez do tool runner.**
   - *Mais simples considerada:* o `tool_runner`. Descartada porque está em beta e porque o orçamento precisa ser checado a cada turno.
   - O laço tem umas 40 linhas no adapter.
   - O resultado final vem por structured outputs (`output_config.format` com JSON Schema), não por `tool_choice` forçado, que dá 400 no Sonnet/Opus 5.5.
   - O adapter também usa thinking adaptativo, `effort` do YAML e `cache_control` de 1 h no system e nas tools.
4. **Ferramentas neutras com sinais fixos** (`taxa_de_erro`, `latencia_p99`, `vazao`), em vez de nomes de métrica do SigNoz. *Mais simples considerada:* repassar nomes de métrica livres. Descartada porque prenderia o prompt e os evals ao fornecedor. O adapter traduz o sinal para a consulta do SigNoz.
5. **SigNoz pelo MCP com lista fechada de ferramentas de leitura.** O adapter chama algo como `signoz_query_metrics` / `signoz_execute_builder_query`, `signoz_search_logs`, `signoz_search_traces` e `signoz_get_service_top_operations`; o agente de integrações confirma os nomes e os argumentos contra o servidor real. *Mais simples considerada:* a API HTTP do SigNoz. Descartada porque o usuário quer o MCP para a criação de dashboards no futuro.
   - A API key é de papel **Viewer**.
   - A lista fechada impede escrita mesmo que o servidor permita.
6. **Azure DevOps pela API REST de builds** (`_apis/build/builds`, filtrados pelo repositório do mapa e por `minTime`), com PAT somente leitura. *Mais simples considerada e escolhida:* é uma consulta só; o MCP do Azure DevOps entra quando houver leitura de código (S5).
7. **Dois laços de worker no mesmo processo.** *Mais simples considerada:* aumentar a concorrência do worker único. Descartada porque as análises ocupariam todos os slots e atrasariam heartbeat e cards. Um segundo container de worker também foi descartado, por ser mais deploy para o mesmo ganho.
8. **Card "investigando" substitui o "incidente novo"** (mesmo `NotificationKind.INCIDENT_OPENED`, novo conteúdo), em vez de postar dois cards. Menos ruído no canal.
9. **Análise parcial é o mesmo card, marcado.** *Mais simples considerada e escolhida:* um formato de card só, com o motivo no topo.
10. **Prompt neutro na `application`**, para que trocar de fornecedor não mude o prompt nem os evals.
11. **Evals fora do `make check`** (`make evals`, com chave real), porque custam dinheiro e não são determinísticos. Os casos usam o mesmo `Toolbox` com ports fake roteirizados.

## Divisão de trabalho

| Papel | Entrega |
|---|---|
| agente pai | Commit de contrato, `bootstrap` (settings, YAML, `prepare_worker`), dependências, compose do e2e, `deploy/` e integração |
| `grolla-dominio` | `complete`/`partial`, `decide_firing` com `request_analysis`, `receive_alerts`, `Toolbox`, `TokenBudget`, prompt e `brief_text`, `run_next_analysis`, `reap_stuck_analyses`, fakes e suítes de contrato de `LanguageModel`, `Telemetry`, `ChangeHistory` e `AnalysisQueue` |
| `grolla-persistencia` | Migração 0003, `request_analysis`, `analysis_of`, `PostgresAnalysisQueue` com `SKIP LOCKED` e `llm_usage` |
| `grolla-integracoes` (LLM) | `grolla.adapters.anthropic` (laço manual, structured outputs, orçamento e erros), fake da Messages API no `grolla-fakes`, card de análise no `teams`, laços e jobs do worker, `evals/` e `make evals` |
| `grolla-integracoes` (observabilidade) | `grolla.adapters.signoz` (cliente MCP de `Telemetry`), `grolla.adapters.azure_devops` (`ChangeHistory`), fakes do MCP do SigNoz e do Azure DevOps |
| `grolla-web` | Seção de análise na página do incidente e seletor do roteiro do LLM no console do `grolla-fakes` |
| `grolla-e2e` | Cenários: análise completa, LLM fora do ar (parcial), tempo esgotado (parcial), alerta agrupado não reabre análise, ordem dos cards |

## Risks / Trade-offs

- [Nomes e argumentos das ferramentas do MCP do SigNoz diferentes do esperado] → o agente de observabilidade lista as ferramentas do servidor real (`tools/list`) antes de fechar e grava as respostas reais como fixtures. O resto do sistema só vê `Telemetry`.
- [LLM inventa evidência ou link] → as evidências da hipótese citam avaliações, e os links das avaliações vêm do adapter, não do LLM. O card mostra os links das avaliações.
- [Custo] → uma análise por incidente novo, limite de tokens por análise, cache de 1 h no system e nas tools, e `llm_usage` para medir. Alertas agrupados não reabrem análise.
- [Tempo de resposta do MCP ou do Azure DevOps] → timeout por ferramenta (20 s) dentro do timeout da análise. Uma ferramenta lenta vira avaliação "não foi possível consultar".
- [Escrita acidental no SigNoz] → lista fechada no adapter, com um teste que prova a recusa, mais API key Viewer.
- [Prompt injection via logs, que entram no contexto] → as ferramentas são somente leitura e o resultado é só texto num card. O pior caso é uma análise ruim, não uma ação.
- [Worker cai no meio de uma análise] → `reap_stuck_analyses` encerra como parcial, com o motivo "interrompida", o que está há mais de `timeout + 2 min` em execução.

## Migration Plan

1. Merge na `master` com `make check` e `make e2e` verdes; bump da versão.
2. **O usuário faz:**
   - criar no SigNoz uma API key Viewer e subir (ou apontar) o `signoz-mcp-server` em modo HTTP alcançável pela EC2;
   - gerar o PAT somente leitura do Azure DevOps;
   - preencher o `.env` e o mapa serviço → repositório em `credenciadora.yaml`.
3. Deploy manual seguindo `deploy/README.md`, atualizado. O webhook do SigNoz é apontado para a instância: primeiro num canal de teste, depois no canal do time.
4. Aceite junto com o S2: alertas reais viram "investigando" e análise útil em até 5 minutos.
5. Rollback: voltar a versão. Sem LLM, o S2 continua funcionando; a análise sai parcial.

## Open Questions

- Onde o `signoz-mcp-server` vai rodar: SigNoz Cloud (endpoint hospedado) ou container próprio ao lado do agente na EC2? O design suporta os dois (só muda `signoz.mcp_url`). Se for container próprio, ele entra no `deploy/compose.yml`.
- Mapa serviço → repositório da Credenciadora: quais serviços e repositórios.

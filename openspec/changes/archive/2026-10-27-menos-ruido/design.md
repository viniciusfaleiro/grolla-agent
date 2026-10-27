## Context

O webhook agrupa por serviço e janela, de forma determinística e rápida, e a severidade vem da
primeira regra que casa ou do padrão. O agente já tem memória (dossiês com dependências e
integrações) que permite ligar dois incidentes de serviços diferentes ao mesmo problema.

## Goals / Non-Goals

**Goals:** os da proposta. **Non-Goals:** os da proposta.

## Contratos

```python
# grolla.domain.triage (novo)
@dataclass(frozen=True)
class TriageDraft:  # o que o modelo devolve
    same_as: UUID | None  # outro incidente aberto que é o mesmo problema
    same_reason: str
    severity: Severity | None  # só quando a severidade veio do padrão
    severity_reason: str


@dataclass(frozen=True)
class TriageDecision:
    incident: Incident | None  # com a severidade elevada, se for o caso
    events: tuple[IncidentEvent, ...]
    notifications: tuple[Notification, ...]


def needs_model(incident, others, opened_by_default: bool) -> bool: ...
def decide_triage(incident, others, draft, opened_by_default, now, page_url) -> TriageDecision:
    """MERGE_SUGGESTED (evento + card) quando same_as é um dos `others`; SEVERITY_CHANGED com
    "sugerida pelo agente: <motivo>" e card SEVERITY_RAISED quando a sugestão é mais alta."""


# grolla.domain.incidents (acréscimos)
# EventType: TRIAGE_COMPLETED, MERGE_SUGGESTED, INCIDENT_MERGED, MERGED_INTO
# NotificationKind: MERGE_SUGGESTED, INCIDENT_MERGED
# SEVERITY_DEFAULT_REASON já existe ("severidade padrão") no data do ALERT_RECEIVED.
def decide_merge(source, target, author, now, page_url) -> tuple[Decision, Decision]: ...


# grolla.application.triage (novo)
class TriageQueue(Protocol):
    async def claim(self, now) -> TriageJob | None: ...
    async def stuck(self, started_before) -> list[TriageJob]: ...
    async def finish(self, job, incident, events, notifications, usages, reason, at) -> None: ...
    async def open_since(
        self, since
    ) -> list[Incident]: ...  # abertos com último alerta desde `since`


@dataclass(frozen=True)
class TriagePolicy:
    window: timedelta = timedelta(minutes=30)
    max_tokens: int = 20_000
    timeout: timedelta = timedelta(minutes=1)


async def run_next_triage(*, queue, repository, model, memory, clock, policy, page_url) -> bool: ...
async def reap_stuck_triages(queue, clock, policy) -> int: ...
async def merge_incidents(*, repository, source_id, target_id, author, clock, page_url) -> Incident:
    """Trava os dois (ordem por id), decide e grava; levanta InvalidActionError."""


# IncidentUnitOfWork ganha request_triage(incident_id, at); receive_alerts pede quando abre.

# grolla.application.investigation
STAGE_TRIAGE = "triage"


@dataclass(frozen=True)
class TriageBrief:
    incident: Incident
    alerts: tuple[Alert, ...]
    others: tuple[tuple[Incident, str], ...]  # incidente e o resumo (alertas e análise)
    memory: MemoryContext
    opened_by_default: bool
    now: datetime


# LanguageModel.triage(brief, budget) -> TriageDraft; TRIAGE_PROMPT e triage_text.
```

**Schema (migração 0011):** `triages`, com `incident_id` como chave primária, `status`,
`requested_at`, `started_at`, `finished_at` e `reason`.

**Config:** `noise: {window_minutes}` e a etapa `llm.stages.triage`.

## Decisions

**1. Triagem no worker, não no webhook.** O webhook continua determinístico e rápido. Uma
sugestão que chega segundos depois não atrapalha.
*Alternativa:* chamar o modelo no webhook. Descartada pela latência e porque o caminho do alerta
não pode depender do LLM.

**2. Juntar é humano; severidade só sobe sozinha.** Juntar mexe no war room de duas pessoas, então
é decisão delas. Subir a severidade quando nenhuma regra casou segue a regra que o time já tem:
"só sobe" e "na dúvida, o mais alto". Baixar nunca.

**3. O juntado vira descartado com motivo.** Reaproveita o estado e a página que já existem. Os
alertas disparando passam para o principal pelo `firing`, e alerta novo daquele fingerprint cai
no principal.
*Alternativa:* um estado novo "juntado". Descartada por espalhar estado novo por todas as
páginas, cards e métricas.

**4. Pedir sempre e decidir no worker se chama o modelo.** `needs_model` é uma função pura, e o
webhook não precisa consultar os outros incidentes.

## Divisão de trabalho

| Papel | Entrega |
|---|---|
| `grolla-dominio` | `domain.triage`, `decide_merge`, `application.triage`, `merge_incidents`, `request_triage` no alerta que abre o incidente, prompt, fakes e suítes de contrato, jobs do worker |
| `grolla-persistencia` | migração 0011, `PostgresTriageQueue`, `request_triage` |
| `grolla-integracoes` | `triage` no Anthropic, cards `MERGE_SUGGESTED` e `INCIDENT_MERGED`, LLM fake |
| `grolla-web` | aviso "Parece o mesmo problema que…" com "Juntar" e o seletor "Juntar com…" no war room |
| `grolla-e2e` | cenários: sugestão de junção e juntar; severidade sugerida sobe; sugestão mais baixa não baixa |

## Desvios de contrato combinados na implementação

**Núcleo:**
- `IntakePolicy.triage`: o pedido de triagem só sai com `noise` na config e quando o alerta abre o
  incidente.
- O juntado perde o `firing`, para a resolução de um alerta não cair no descartado.
- `run_next_triage` relê o incidente depois da chamada ao modelo, para não perder um alerta
  agrupado no meio.
- A sugestão de junção fica no incidente novo, que é juntado no outro.
- Não existe P4: a severidade sugerida vai de P0 a P3.

**Integrações:** o LLM fake aceita `same_as` pelo nome do serviço, porque o banco do e2e não é
limpo entre cenários.

**Web:** juntar responde 303 para o principal; o botão só aparece para quem informou o nome.

**Code review do pai:** a triagem foi ligada no intake pela config, com teste. O cenário e2e não
confere o botão (a página é lida sem cookie). A spec foi ajustada: não há P4, e a sugestão fica
no incidente novo.

## Risks / Trade-offs

- [Sugestão errada] → só sugere; a pessoa ignora ou junta.
- [Severidade subindo demais] → só quando nenhuma regra casou, com o motivo na timeline; o time
  pode baixar pelo war room.

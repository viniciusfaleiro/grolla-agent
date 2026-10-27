## Context

Todo incidente tem log de eventos imutável, análise, validação, ações do time, chat, ciclos de
acompanhamento e TTA/TTM/TTR, e o aprendizado ao encerrar já faz uma chamada sem ferramentas sobre
o log. Falta o post mortem: um documento editável, versionado, com ciclo de vida e PDF.

## Goals / Non-Goals

**Goals:** os da proposta.

**Non-Goals:** os da proposta.

## Contratos

```python
# grolla.domain.post_mortem (novo)
class SectionKey(StrEnum):  # ordem do template
    SUMMARY = "resumo"
    IMPACT = "impacto"
    METRICS = "metricas"  # determinística
    TIMELINE = "timeline"  # determinística
    DETECTION = "deteccao"
    FACTORS = "fatores"
    HYPOTHESES = "hipoteses"
    LESSONS = "licoes"  # o que funcionou, o que não funcionou, sorte
    ACTIONS = "plano"  # texto livre de apoio; os itens ficam em ActionItem
    AGENT = "avaliacao_agente"


DETERMINISTIC = frozenset({SectionKey.METRICS, SectionKey.TIMELINE})
SECTION_TITLES: Mapping[SectionKey, str]


class PostMortemStatus(StrEnum):
    DRAFT = "rascunho"
    REVIEW = "em_revisao"
    APPROVED = "aprovado"
    CLOSED = "encerrado"


class ActionKind(StrEnum):
    PREVENT = "prevenir"
    DETECT = "detectar"
    MITIGATE = "mitigar"


class ActionStatus(StrEnum):
    OPEN = "aberto"
    DONE = "concluido"
    ACCEPTED = "aceito"


@dataclass(frozen=True)
class Section:
    key: SectionKey
    body: str  # markdown
    author: str  # "Grolla" ou a pessoa
    updated_at: datetime


@dataclass(frozen=True)
class ActionItem:
    id: UUID
    description: str
    kind: ActionKind
    owner: str | None = None
    due: date | None = None
    status: ActionStatus = ActionStatus.OPEN


@dataclass(frozen=True)
class PostMortem:
    incident_id: UUID
    status: PostMortemStatus
    sections: tuple[Section, ...]  # na ordem do template
    actions: tuple[ActionItem, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SectionVersion:
    incident_id: UUID
    key: SectionKey
    at: datetime
    author: str
    reason: str
    body: str  # texto ANTERIOR


@dataclass(frozen=True)
class PostMortemDraft:  # o que o modelo devolve
    sections: Mapping[SectionKey, str]  # sem as determinísticas
    actions: tuple[tuple[str, ActionKind], ...]  # descrição e tipo


def metrics_text(incident, alerts) -> str: ...  # TTD, TTA, TTM, TTR
def timeline_text(events) -> str: ...  # "- 03:01 [alerta] ...", origem: alerta|agente|chat|pessoa
def drafted(
    incident, alerts, events, draft: PostMortemDraft | None, failure: str | None, now
) -> PostMortem: ...
def edited(
    pm, key, body, author, now
) -> tuple[PostMortem, SectionVersion]: ...  # rascunho → em revisão
def regenerated(pm, key, body, now) -> tuple[PostMortem, SectionVersion]: ...
def moved(
    pm, to: PostMortemStatus, author, now
) -> PostMortem: ...  # levanta InvalidPostMortemError
def overdue(pm, today: date) -> tuple[ActionItem, ...]: ...


class InvalidPostMortemError(ValueError): ...


# grolla.application.post_mortem (novo)
class PostMortemStore(Protocol):
    async def get(self, incident_id: UUID) -> PostMortem | None: ...
    async def save(self, pm: PostMortem, versions: Sequence[SectionVersion]) -> None: ...
    async def versions(self, incident_id: UUID, key: SectionKey) -> list[SectionVersion]: ...
    async def list(self, filters: PostMortemFilters) -> list[PostMortemSummary]: ...
    # fila: pedidos de rascunho e de regeneração (status pendente/em_andamento/feito/falho)
    async def claim(self, now: datetime) -> PostMortemJob | None: ...
    async def stuck(self, started_before: datetime) -> list[PostMortemJob]: ...
    async def request_regeneration(self, incident_id, key, instruction, author, at) -> None: ...
    async def finish(self, job, pm, versions, usages, ok, reason, at) -> None: ...


@dataclass(frozen=True)
class PostMortemJob:
    incident_id: UUID
    key: SectionKey | None  # None: rascunho inteiro
    instruction: str | None
    author: str | None
    started_at: datetime


class DocumentRenderer(Protocol):
    def render(self, pm: PostMortem, incident: Incident) -> bytes:
        """PDF do post mortem; levanta RenderError."""


# IncidentUnitOfWork ganha request_post_mortem(incident_id, at) (no INCIDENT_RESOLVED).
# LanguageModel ganha:
async def draft_post_mortem(self, brief: PostMortemBrief, budget) -> PostMortemDraft: ...
async def regenerate_section(
    self, brief: PostMortemBrief, key: SectionKey, instruction: str | None, budget
) -> str: ...


STAGE_POST_MORTEM = "post_mortem"
# Casos de uso: run_next_post_mortem, reap_stuck_post_mortems, edit_section,
# request_regeneration, move_status, add_action, update_action, list_post_mortems, render_pdf.
```

**Schema (migração 0009):**
- `post_mortems`: `incident_id` como chave primária, `status`, `sections jsonb`,
  `actions jsonb`, `created_at`, `updated_at`.
- `post_mortem_versions`: só inserção.
- `post_mortem_jobs`:
  - colunas: `id`, `incident_id`, `section` (null para o rascunho inteiro), `instruction`,
    `author`, `status`, `requested_at`, `started_at`, `finished_at`, `reason`;
  - índices nos pendentes.

## Decisions

**1. Métricas e timeline sem LLM.** São fatos do log; o núcleo formata com a origem de cada item.
O modelo só escreve o que é interpretação.
*Alternativa:* deixar tudo com o modelo. Descartada porque números e horários são os primeiros a
sair errados.

**2. Seções em `jsonb` numa linha, versões numa tabela à parte.** O post mortem é um documento lido
inteiro. Versão por seção é o que a página precisa.
*Alternativa:* uma tabela por seção. Descartada por ser mais esquema sem ganho.

**3. Fila própria para rascunho e regeneração.** É o mesmo padrão das análises: o pedido fica
na transação do encerramento ou da página, e o worker executa. Falha não perde o pedido.

**4. PDF com fpdf2 (puro Python).** Sem dependências de sistema como Cairo e Pango, o que mantém
a imagem pequena.
- Fonte core Helvetica, com o texto convertido para latin-1, que cobre os acentos do português.
- Caracteres fora do latin-1 são trocados.
- *Alternativa:* WeasyPrint (HTML para PDF, mais bonito). Descartada pelas dependências nativas
  na imagem slim.
- O port `DocumentRenderer` isola a escolha.

**5. Ciclo de vida no domínio.** As regras de aprovar (dono e prazo) e encerrar (itens concluídos)
são funções puras com erro de domínio, e a página só exibe a mensagem.

## Divisão de trabalho

| Papel | Entrega |
|---|---|
| `grolla-dominio` | `domain.post_mortem`, `application.post_mortem`, `request_post_mortem` no encerramento, prompts, fakes (`InMemoryPostMortemStore`, `FakeDocumentRenderer`, `ScriptedLanguageModel` com as duas etapas) e suítes de contrato, jobs do worker |
| `grolla-persistencia` | migração 0009, `PostgresPostMortemStore`, `request_post_mortem` |
| `grolla-integracoes` | Anthropic (`draft_post_mortem`, `regenerate_section`), adapter `grolla.adapters.fpdf` (`FpdfDocumentRenderer`), LLM fake |
| `grolla-web` | `/postmortems` com filtros, `/postmortems/{id}` com edição, versões, regenerar, plano de ação, status e PDF; link na página do incidente |
| `grolla-e2e` | cenários: resolver gera o rascunho; editar move para em revisão; aprovar sem dono é recusado; aprovar com dono e baixar o PDF; regenerar uma seção |

## Risks / Trade-offs

- [Texto culpando pessoas] → o prompt pede blameless e fatores contribuintes, e o time edita.
- [PDF sem caracteres especiais] → latin-1 cobre o português; o resto vira o equivalente mais
  próximo.
- [Custo] → uma chamada por incidente resolvido, com teto de tokens.

## Migration Plan

A migração 0009 só cria tabelas. Incidentes resolvidos antes não ganham post mortem
automaticamente.

## Context

O war room registra ações humanas, chat e ciclos, mas não o que se fala na call. As ferramentas
de reunião já geram a transcrição em texto (`.vtt`/`.srt`), então falta receber esse texto,
extrair o que importa e pôr na timeline com a origem.

## Goals / Non-Goals

**Goals:** os da proposta. **Non-Goals:** os da proposta.

## Contratos

```python
# grolla.domain.transcript (novo)
class TranscriptItemKind(StrEnum):
    DECISION = "decisao"
    ACTION = "acao"
    TIME = "horario"

@dataclass(frozen=True)
class TranscriptItem:
    kind: TranscriptItemKind
    text: str
    owner: str | None  # ação: quem ficou responsável
    said_at: str | None  # horário dito na call, como "03:12"
    excerpt: str

class HypothesisVerdict(StrEnum):  # o que a call disse
    CONFIRMED = "confirmada"
    DISCARDED = "descartada"

@dataclass(frozen=True)
class HypothesisCheck:
    hypothesis: str
    verdict: HypothesisVerdict
    excerpt: str

@dataclass(frozen=True)
class TranscriptDraft:
    items: tuple[TranscriptItem, ...]
    participants: tuple[str, ...]
    hypotheses: tuple[HypothesisCheck, ...]

@dataclass(frozen=True)
class Transcript:
    id: int
    incident_id: UUID
    author: str
    text: str
    received_at: datetime

def clean_transcript(raw: str, filename: str | None) -> str:  # tira cabeçalho/tempos de .vtt/.srt
def transcript_events(incident_id, transcript, draft, now) -> tuple[IncidentEvent, ...]: ...
# EventType: TRANSCRIPT_RECEIVED, TRANSCRIPT_ITEM ({"kind","text","owner","said_at","excerpt",
# "origin": "transcrição"}), TRANSCRIPT_PARTICIPANTS, TRANSCRIPT_HYPOTHESIS, TRANSCRIPT_FAILED

# grolla.application.transcript (novo)
class TranscriptStore(Protocol):
    async def add(self, transcript: Transcript, event: IncidentEvent) -> Transcript: ...
    async def claim(self, now) -> TranscriptJob | None: ...
    async def stuck(self, started_before) -> list[TranscriptJob]: ...
    async def finish(self, job, events, usages, reason, at) -> None: ...
    async def retry(self, transcript_id, at) -> None: ...
@dataclass(frozen=True)
class TranscriptPolicy:
    max_chars: int = 200_000
    max_tokens: int = 80_000
    timeout: timedelta = timedelta(minutes=3)
async def upload_transcript(*, repository, store, incident_id, author, raw, filename, clock, policy): ...
async def run_next_transcript(*, store, repository, model, clock, policy) -> bool: ...
async def reap_stuck_transcripts(store, clock, policy) -> int: ...
# LanguageModel.extract_transcript(context: str, transcript: str, budget) -> TranscriptDraft
# STAGE_TRANSCRIPT = "transcript"; TRANSCRIPT_PROMPT e transcript_text.
# domain.post_mortem.timeline_text: origem "transcrição" para os eventos TRANSCRIPT_*.
```

**Schema (migração 0013):** `transcripts` (`id`, `incident_id`, `author`, `text`, `received_at`,
`status`, `started_at`, `finished_at`, `reason`).

## Decisions

**1. Upload de texto, sem integração com a ferramenta de reunião.** Funciona com Teams, Meet ou
Zoom sem conta nova, e as ferramentas já exportam `.vtt`.

**2. Itens como eventos do incidente.** A timeline, o post mortem e o chat já leem o log. A origem
"transcrição" fica no `data` e no autor do evento.

**3. Reavaliação das hipóteses só como evidência.** "O núcleo decide": a call descartar uma hipótese
é registro, e quem descarta no war room é a pessoa.

## Divisão de trabalho

| Papel | Entrega |
|---|---|
| `grolla-dominio` | `domain.transcript`, `application.transcript`, a origem na timeline do post mortem, prompt, fakes e suítes de contrato, jobs |
| `grolla-persistencia` | migração 0013 e `PostgresTranscriptStore` |
| `grolla-integracoes` | `extract_transcript` no Anthropic e o LLM fake com a etapa `transcript` |
| `grolla-web` | upload no war room (colar ou arquivo), itens na timeline e reprocessar quando falhar |
| `grolla-e2e` | cenário: enviar um `.vtt`, ver a decisão, a ação e a hipótese descartada na timeline e a origem no post mortem |

## Risks / Trade-offs

- [Transcrição longa] → limite de caracteres e de tokens; o resto é cortado com aviso.
- [Prompt injection pela fala] → a transcrição vai como dado, e a extração não muda estado.

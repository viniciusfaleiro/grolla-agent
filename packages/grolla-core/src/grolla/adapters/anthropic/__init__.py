"""Anthropic (Claude): o modelo de linguagem da investigação, com laço de ferramentas manual.

A cada turno o modelo pede ferramentas do `Toolbox` (tool_use) ou devolve o resultado final,
que o structured outputs (`output_config.format`) obriga a seguir o schema do rascunho. O uso
de cada resposta passa pelo `TokenBudget` antes de qualquer outra coisa.

As três chamadas usam o mesmo laço: a análise inicial (`investigate`, etapa `investigation`), a
validação das hipóteses até a causa raiz (`verify`, etapa `verification`, com modelo e
`effort` próprios) e a resposta no chat do war room (`reply`, etapa `warroom_chat`, idem, com o
resultado no schema `{"reply": string, "scope": ...}`). Na validação, um plano com passo de
investigação ganha uma única mensagem de correção; se vier de novo, o resultado é inválido.

O acompanhamento da crise (`follow_up`, etapa `follow_up`) roda o mesmo laço, fechando no
orçamento como o chat, e devolve a tendência, a taxa de erro, o resumo, se algo mudou e até três
evidências.

O post mortem usa duas chamadas sem ferramentas, com saída estruturada e a etapa `post_mortem`:
o rascunho (`draft_post_mortem`, um campo por seção escrita pelo modelo e os itens do plano) e
a regeneração de uma seção (`regenerate_section`, `{"body": string}`).

A memória usa mais três chamadas: o estudo de um serviço (`study_service`, etapa `study`) roda
o mesmo laço, fechando no orçamento como o chat, e devolve o dossiê; o aprendizado ao encerrar
(`learn`, etapa `learning`) e a síntese do ambiente (`synthesize`, etapa `study`) são uma
chamada só, sem ferramentas, com saída estruturada.

A triagem do incidente novo (`triage`, etapa `triage`) também é uma chamada só, sem ferramentas:
o outro incidente aberto que é o mesmo problema (ou null) e a severidade sugerida (ou null), cada
um com o motivo. Id que não é UUID vira "sem junção", sem invalidar a triagem.

O console de operação (`consult`, etapa `console`, herdando o modelo do chat) roda o mesmo laço,
fechando no orçamento como o chat, e devolve o texto e os gráficos pedidos
(`{text, scope, charts}`, cada gráfico com serviço, sinal e janela em minutos); quem filtra os
gráficos é o domínio.

O chat e o console devolvem também o escopo do pedido (`dominio`, `fora_do_escopo` ou
`pedido_de_execucao`); quem troca o texto pela recusa fixa é o núcleo. Escopo fora do enum é
resultado inválido; ausente (resposta anterior ao campo) vale `dominio`.

A transcrição de call (`extract_transcript`, etapa `transcript`, herdando o modelo do post mortem)
é uma chamada só, sem ferramentas: as decisões, ações e horários (`items`, cada um com o dono e o
horário dito, ou null, e o trecho da fala), os participantes e o que a call disse das hipóteses
(`confirmada` ou `descartada`, com o trecho). Item, participante ou hipótese sem texto é ignorado.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import anthropic
import httpx2
from anthropic import Omit, omit
from anthropic.lib.bedrock import AsyncAnthropicBedrock
from anthropic.types import (
    Message,
    MessageParam,
    TextBlockParam,
    ToolChoiceParam,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlock,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from grolla.application.investigation import (
    CHAT_PROMPT,
    CONSOLE_PROMPT,
    FOLLOW_UP_PROMPT,
    LEARNING_PROMPT,
    POST_MORTEM_PROMPT,
    STAGE_CONSOLE,
    STAGE_FOLLOW_UP,
    STAGE_INVESTIGATION,
    STAGE_LEARNING,
    STAGE_POST_MORTEM,
    STAGE_STUDY,
    STAGE_TRANSCRIPT,
    STAGE_TRIAGE,
    STAGE_VERIFICATION,
    STAGE_WARROOM_CHAT,
    STUDY_PROMPT,
    SYNTHESIS_PROMPT,
    SYSTEM_PROMPT,
    TRANSCRIPT_PROMPT,
    TRIAGE_PROMPT,
    VERIFICATION_PROMPT,
    InvalidAnalysisError,
    InvestigationBrief,
    LanguageModelError,
    LearningBrief,
    LlmUsage,
    PostMortemBrief,
    ServiceStudyBrief,
    SynthesisBrief,
    TokenBudget,
    Toolbox,
    TriageBrief,
    brief_text,
    console_text,
    follow_up_text,
    learning_text,
    post_mortem_text,
    regeneration_text,
    study_text,
    synthesis_text,
    transcript_text,
    triage_text,
    verification_text,
)
from grolla.domain.analysis import (
    Action,
    Analysis,
    AnalysisDraft,
    Evidence,
    Hypothesis,
    HypothesisVerdict,
    NotConcreteError,
    SeenBeforeDraft,
    Verdict,
    VerificationDraft,
    check_plan,
    usable_commands,
)
from grolla.domain.chat import ChatDraft
from grolla.domain.console import CHART_SIGNALS, ChartRequest, ConsoleDraft
from grolla.domain.follow_up import Cycle, CycleDraft, Trend
from grolla.domain.incidents import Severity
from grolla.domain.memory import (
    ENVIRONMENT,
    DossierDraft,
    LearningDraft,
    SubjectKind,
    SynthesisDraft,
)
from grolla.domain.post_mortem import DETERMINISTIC, ActionKind, PostMortemDraft, SectionKey
from grolla.domain.safety import Scope
from grolla.domain.transcript import (
    HypothesisCheck,
    TranscriptDraft,
    TranscriptItem,
    TranscriptItemKind,
)
from grolla.domain.transcript import HypothesisVerdict as CallVerdict
from grolla.domain.triage import TriageDraft

# Poucas retentativas e timeout curto por requisição: a análise inteira tem o próprio timeout,
# e uma API fora do ar precisa virar análise parcial "LLM indisponível" em segundos.
MAX_RETRIES = 2
REQUEST_TIMEOUT_SECONDS = 60.0

# Cada volta reenvia a conversa inteira e custa um pouco mais que a anterior: com menos de duas
# voltas no orçamento, o chat fecha a resposta com o que já consultou.
WRAP_UP_TURNS = 2
WRAP_UP = (
    "O orçamento desta resposta está acabando: responda agora, no formato pedido, com o que já "
    "consultou e diga em uma linha o que não deu para verificar."
)

# Cache de 1 h no prefixo estável (ferramentas e system): as análises de um mesmo dia o reusam.
_CACHE_1H: Any = {"type": "ephemeral", "ttl": "1h"}

_EVIDENCE_SCHEMA: dict[str, object] = {
    "type": "array",
    "minItems": 1,  # toda hipótese e todo veredito citam ao menos uma evidência
    "items": {
        "type": "object",
        "properties": {"text": {"type": "string"}, "url": {"type": ["string", "null"]}},
        "required": ["text", "url"],
        "additionalProperties": False,
    },
}

# Schema do resultado final. Structured outputs não aceita limites numéricos (só minItems 0
# ou 1): a confiança entre 0 e 1 é conferida na validação.
DRAFT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "confidence": {"type": "number"},
                    "evidence": _EVIDENCE_SCHEMA,
                },
                "required": ["title", "confidence", "evidence"],
                "additionalProperties": False,
            },
        },
        "plan": {"type": "array", "items": {"type": "string"}},
        # "Já vi isso": o id de um episódio da memória entregue, ou null.
        "seen_before": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "incident_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["incident_id", "reason"],
                    "additionalProperties": False,
                },
                {"type": "null"},
            ]
        },
    },
    "required": ["summary", "hypotheses", "plan", "seen_before"],
    "additionalProperties": False,
}

# Schema da validação: um veredito por hipótese, a causa raiz (null sem confirmação), o plano de
# ações concretas e o que faltou. O plano concreto é conferido depois, por `check_plan`.
VERIFICATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hypothesis": {"type": "string"},
                    "verdict": {"type": "string", "enum": [v.value for v in Verdict]},
                    "reasoning": {"type": "string"},
                    "evidence": _EVIDENCE_SCHEMA,
                },
                "required": ["hypothesis", "verdict", "reasoning", "evidence"],
                "additionalProperties": False,
            },
        },
        "root_cause": {"type": ["string", "null"]},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "target": {"type": "string"},
                    "expected": {"type": "string"},
                    "link": {"type": ["string", "null"]},
                    "commands": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "command": {"type": "string"},
                                "run_in": {"type": "string"},
                            },
                            "required": ["command", "run_in"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["action", "target", "expected", "link", "commands"],
                "additionalProperties": False,
            },
        },
        "missing": {"type": "string"},
    },
    "required": ["verdicts", "root_cause", "actions", "missing"],
    "additionalProperties": False,
}

# Schema da resposta do chat: só o texto, que vai para o chat como está.
# Escopo do pedido no chat e no console: quem troca o texto pela recusa fixa é o núcleo.
_SCOPE = {"type": "string", "enum": [scope.value for scope in Scope]}

CHAT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"reply": {"type": "string"}, "scope": _SCOPE},
    "required": ["reply", "scope"],
    "additionalProperties": False,
}


def _object(**properties: object) -> dict[str, object]:
    """Objeto do structured outputs: todos os campos obrigatórios, nenhum a mais."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_TEXT = {"type": "string"}

# Estudo de um serviço: o dossiê, em texto. Os links vêm das avaliações, não do modelo.
STUDY_SCHEMA = _object(dossier=_TEXT)

# Aprendizado ao encerrar: o episódio e os dossiês reescritos dos serviços e integrações.
LEARNING_SCHEMA = _object(
    episode_title=_TEXT,
    episode_body=_TEXT,
    noise={"type": "boolean"},
    dossiers={
        "type": "array",
        "items": _object(
            subject=_TEXT,
            subject_kind={
                "type": "string",
                "enum": [SubjectKind.SERVICE.value, SubjectKind.INTEGRATION.value],
            },
            body=_TEXT,
        ),
    },
)

# Síntese: o dossiê do ambiente e um por integração.
SYNTHESIS_SCHEMA = _object(
    environment=_TEXT,
    integrations={"type": "array", "items": _object(subject=_TEXT, body=_TEXT)},
)

# Console de operação: a resposta e os gráficos pedidos. O modelo só diz o que mostrar; os pontos
# vêm da telemetria. A janela e o número de gráficos são limitados pelo domínio.
CONSOLE_SCHEMA = _object(
    text=_TEXT,
    scope=_SCOPE,
    charts={
        "type": "array",
        "items": _object(
            service=_TEXT,
            signal={"type": "string", "enum": list(CHART_SIGNALS)},
            minutes={"type": "integer"},
        ),
    },
)

# Ciclo de acompanhamento. Sem limites numéricos no structured outputs: a taxa de erro entre 0 e
# 100 é conferida na validação, e só as três primeiras evidências ficam.
FOLLOW_UP_SCHEMA = _object(
    trend={"type": "string", "enum": [t.value for t in Trend]},
    error_rate={"type": ["number", "null"]},
    summary=_TEXT,
    changed={"type": "boolean"},
    evidence={
        "type": "array",
        "items": _object(text=_TEXT, url={"type": ["string", "null"]}),
    },
)
MAX_CYCLE_EVIDENCE = 3

# Post mortem: um campo de texto (markdown) por seção escrita pelo modelo, na ordem do template,
# e os itens do plano de ação, sem dono nem prazo. A regeneração devolve só o texto da seção.
MODEL_SECTIONS = tuple(key for key in SectionKey if key not in DETERMINISTIC)
POST_MORTEM_SCHEMA = _object(
    **{key.value: _TEXT for key in MODEL_SECTIONS},
    actions={
        "type": "array",
        "items": _object(
            description=_TEXT, kind={"type": "string", "enum": [k.value for k in ActionKind]}
        ),
    },
)
SECTION_SCHEMA = _object(body=_TEXT)

# Triagem: o id do outro incidente aberto que é o mesmo problema e a severidade sugerida, cada um
# nulo quando não se aplica e com o motivo em texto.
TRIAGE_SCHEMA = _object(
    same_as={"type": ["string", "null"]},
    same_reason=_TEXT,
    severity={"anyOf": [{"type": "string", "enum": [s.value for s in Severity]}, {"type": "null"}]},
    severity_reason=_TEXT,
)

# Transcrição de call: os itens (decisão, ação ou horário, com dono e horário dito quando houver),
# os participantes e o veredito da call para cada hipótese citada, sempre com o trecho da fala.
_NULLABLE_TEXT = {"type": ["string", "null"]}
TRANSCRIPT_SCHEMA = _object(
    items={
        "type": "array",
        "items": _object(
            kind={"type": "string", "enum": [k.value for k in TranscriptItemKind]},
            text=_TEXT,
            owner=_NULLABLE_TEXT,
            said_at=_NULLABLE_TEXT,
            excerpt=_TEXT,
        ),
    },
    participants={"type": "array", "items": _TEXT},
    hypotheses={
        "type": "array",
        "items": _object(
            hypothesis=_TEXT,
            verdict={"type": "string", "enum": [v.value for v in CallVerdict]},
            excerpt=_TEXT,
        ),
    },
)

NOT_CONCRETE = (
    "O plano tem passo de investigação: troque por ações concretas, ou deixe sem plano se a "
    "causa não foi confirmada."
)


@dataclass(frozen=True)
class ModelSettings:
    """Parâmetros da etapa, lidos do YAML do time (`llm.stages.<etapa>`)."""

    model: str
    max_tokens: int
    effort: str | None = None


@dataclass(frozen=True)
class _Phase:
    """O que muda entre a análise inicial e a validação."""

    system: str
    schema: dict[str, object]
    settings: ModelSettings
    stage: str
    wrap_up: bool = False  # chat: com o orçamento acabando, responde com o que já consultou

    def output_config(self) -> Any:
        config: dict[str, object] = {"format": {"type": "json_schema", "schema": self.schema}}
        if self.settings.effort is not None:
            config["effort"] = self.settings.effort
        return config


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Evidence(_Strict):
    text: str
    url: str | None


class _Hypothesis(_Strict):
    title: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[_Evidence] = Field(min_length=1)


class _SeenBefore(_Strict):
    incident_id: str
    reason: str

    def draft(self) -> SeenBeforeDraft | None:
        """Id que não é UUID ou motivo vazio: a referência é descartada, sem invalidar."""
        try:
            incident_id = UUID(self.incident_id)
        except ValueError:
            return None
        return SeenBeforeDraft(incident_id, self.reason.strip()) if self.reason.strip() else None


class _Draft(_Strict):
    summary: str
    hypotheses: list[_Hypothesis]
    plan: list[str]
    seen_before: _SeenBefore | None = None


class _Study(_Strict):
    dossier: str


class _LearnedDossier(_Strict):
    subject: str
    subject_kind: Literal[SubjectKind.SERVICE, SubjectKind.INTEGRATION]
    body: str


class _Learning(_Strict):
    episode_title: str
    episode_body: str
    noise: bool
    dossiers: list[_LearnedDossier]


class _Integration(_Strict):
    subject: str
    body: str


class _Synthesis(_Strict):
    environment: str
    integrations: list[_Integration]


class _Verdict(_Strict):
    hypothesis: str
    verdict: Verdict
    reasoning: str
    evidence: list[_Evidence] = Field(min_length=1)


class _Command(_Strict):
    command: str
    run_in: str


class _Action(_Strict):
    action: str
    target: str
    expected: str
    link: str | None
    commands: list[_Command]


class _Verification(_Strict):
    verdicts: list[_Verdict]
    root_cause: str | None
    actions: list[_Action]
    missing: str


class _Reply(_Strict):
    reply: str
    scope: Scope = Scope.DOMAIN  # ausente só em resposta anterior ao campo; fora do enum é inválida


class _Chart(_Strict):
    service: str
    signal: str
    minutes: int


class _Console(_Strict):
    text: str
    charts: list[_Chart]
    scope: Scope = Scope.DOMAIN  # ausente só em resposta anterior ao campo; fora do enum é inválida


class _Cycle(_Strict):
    trend: Trend
    error_rate: float | None = Field(ge=0, le=100)
    summary: str
    changed: bool
    evidence: list[_Evidence]


class _ProposedAction(_Strict):
    description: str
    kind: ActionKind


class _PostMortem(_Strict):
    """Os campos são os valores de `MODEL_SECTIONS`, mais o plano de ação."""

    resumo: str
    impacto: str
    deteccao: str
    fatores: str
    hipoteses: str
    licoes: str
    plano: str
    avaliacao_agente: str
    actions: list[_ProposedAction]


class _Section(_Strict):
    body: str


class _Triage(_Strict):
    same_as: str | None
    same_reason: str
    severity: Severity | None
    severity_reason: str

    def draft(self) -> TriageDraft:
        """Id que não é UUID: sem junção, sem invalidar a triagem."""
        try:
            same_as = UUID(self.same_as) if self.same_as else None
        except ValueError:
            same_as = None
        return TriageDraft(
            same_as=same_as,
            same_reason=self.same_reason.strip(),
            severity=self.severity,
            severity_reason=self.severity_reason.strip(),
        )


class _TranscriptItem(_Strict):
    kind: TranscriptItemKind
    text: str
    owner: str | None
    said_at: str | None
    excerpt: str


class _HypothesisCheck(_Strict):
    hypothesis: str
    verdict: CallVerdict
    excerpt: str


class _Transcript(_Strict):
    items: list[_TranscriptItem]
    participants: list[str]
    hypotheses: list[_HypothesisCheck]

    def draft(self) -> TranscriptDraft:
        """Sem espaços nas pontas; o que vem sem texto fica de fora, e dono ou horário vazio é
        null."""
        return TranscriptDraft(
            items=tuple(
                TranscriptItem(
                    kind=i.kind,
                    text=i.text.strip(),
                    owner=(i.owner or "").strip() or None,
                    said_at=(i.said_at or "").strip() or None,
                    excerpt=i.excerpt.strip(),
                )
                for i in self.items
                if i.text.strip()
            ),
            participants=tuple(dict.fromkeys(p.strip() for p in self.participants if p.strip())),
            hypotheses=tuple(
                HypothesisCheck(h.hypothesis.strip(), h.verdict, h.excerpt.strip())
                for h in self.hypotheses
                if h.hypothesis.strip()
            ),
        )


@dataclass(frozen=True)
class AnthropicApi:
    api_key: str
    base_url: str | None = None  # só o e2e aponta para o fake


@dataclass(frozen=True)
class Bedrock:
    """O mesmo modelo pelo Amazon Bedrock; credenciais da cadeia padrão da AWS."""

    region: str


type Connection = AnthropicApi | Bedrock
type _Client = anthropic.AsyncAnthropic | AsyncAnthropicBedrock


class ClaudeLanguageModel:
    def __init__(  # noqa: PLR0913 — modelos por etapa e conexão vêm da configuração
        self,
        connection: Connection,
        settings: ModelSettings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verification: ModelSettings | None = None,
        chat: ModelSettings | None = None,
        learning: ModelSettings | None = None,
        study: ModelSettings | None = None,
        follow_up: ModelSettings | None = None,
        post_mortem: ModelSettings | None = None,
        triage: ModelSettings | None = None,
        console: ModelSettings | None = None,
        transcript: ModelSettings | None = None,
    ) -> None:
        self._connection = connection
        self._settings = settings
        self._verification = verification or settings  # etapa `verification` herda a da análise
        self._chat = chat or settings  # etapa `warroom_chat` também herda
        self._learning = learning or settings  # etapas da memória também herdam
        self._study = study or settings
        self._follow_up = follow_up or settings
        self._post_mortem = post_mortem or settings
        self._triage = triage or settings
        self._console = console or chat or settings  # o console herda do chat
        self._transcript = transcript or post_mortem or settings  # herda do post mortem
        self._transport = transport  # testes injetam um transporte fake

    async def investigate(
        self, brief: InvestigationBrief, toolbox: Toolbox, budget: TokenBudget
    ) -> AnalysisDraft:
        phase = _Phase(SYSTEM_PROMPT, DRAFT_SCHEMA, self._settings, STAGE_INVESTIGATION)
        messages: list[MessageParam] = [{"role": "user", "content": brief_text(brief)}]
        async with self._client() as client:
            try:
                return _draft(await _loop(client, phase, messages, toolbox, budget))
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error

    async def reply(
        self,
        brief: InvestigationBrief,
        context: str,
        question: str,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> ChatDraft:
        del brief  # o contexto montado pela application já traz o incidente
        phase = _Phase(CHAT_PROMPT, CHAT_SCHEMA, self._chat, STAGE_WARROOM_CHAT, wrap_up=True)
        messages: list[MessageParam] = [{"role": "user", "content": chat_text(context, question)}]
        async with self._client() as client:
            try:
                return _chat_draft(await _loop(client, phase, messages, toolbox, budget))
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error

    async def verify(
        self,
        brief: InvestigationBrief,
        analysis: Analysis,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> VerificationDraft:
        phase = _Phase(
            VERIFICATION_PROMPT, VERIFICATION_SCHEMA, self._verification, STAGE_VERIFICATION
        )
        text = verification_text(brief, analysis)
        messages: list[MessageParam] = [{"role": "user", "content": text}]
        async with self._client() as client:
            try:
                return await _concrete(client, phase, messages, toolbox, budget)
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error

    async def follow_up(
        self,
        brief: InvestigationBrief,
        context: str,
        previous: Cycle | None,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> CycleDraft:
        del brief  # o contexto montado pela application já traz o incidente
        phase = _Phase(
            FOLLOW_UP_PROMPT, FOLLOW_UP_SCHEMA, self._follow_up, STAGE_FOLLOW_UP, wrap_up=True
        )
        text = follow_up_text(context, previous)
        messages: list[MessageParam] = [{"role": "user", "content": text}]
        async with self._client() as client:
            try:
                response = await _loop(client, phase, messages, toolbox, budget)
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error
        cycle = _parsed(_Cycle, response, "do acompanhamento")
        if not cycle.summary.strip():
            raise InvalidAnalysisError("resumo do ciclo vazio")
        return CycleDraft(
            trend=cycle.trend,
            error_rate=cycle.error_rate,
            summary=cycle.summary.strip(),
            changed=cycle.changed,
            evidence=tuple(
                Evidence(text=e.text, url=e.url) for e in cycle.evidence[:MAX_CYCLE_EVIDENCE]
            ),
        )

    async def draft_post_mortem(
        self, brief: PostMortemBrief, budget: TokenBudget
    ) -> PostMortemDraft:
        phase = _Phase(POST_MORTEM_PROMPT, POST_MORTEM_SCHEMA, self._post_mortem, STAGE_POST_MORTEM)
        async with self._client() as client:
            try:
                response = await _once(client, phase, post_mortem_text(brief), budget)
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error
        written = _parsed(_PostMortem, response, "do post mortem")
        fields = written.model_dump()
        sections = {key: str(fields[key.value]).strip() for key in MODEL_SECTIONS}
        if empty := [key.value for key, body in sections.items() if not body]:
            raise InvalidAnalysisError(f"seções vazias no post mortem: {', '.join(empty)}")
        return PostMortemDraft(
            sections=sections,
            actions=tuple(
                (a.description.strip(), a.kind) for a in written.actions if a.description.strip()
            ),
        )

    async def regenerate_section(
        self,
        brief: PostMortemBrief,
        key: SectionKey,
        instruction: str | None,
        budget: TokenBudget,
    ) -> str:
        phase = _Phase(POST_MORTEM_PROMPT, SECTION_SCHEMA, self._post_mortem, STAGE_POST_MORTEM)
        text = regeneration_text(brief, key, instruction)
        async with self._client() as client:
            try:
                response = await _once(client, phase, text, budget)
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error
        body = _parsed(_Section, response, "da seção").body.strip()
        if not body:
            raise InvalidAnalysisError(f"seção {key.value} regenerada vazia")
        return body

    async def triage(self, brief: TriageBrief, budget: TokenBudget) -> TriageDraft:
        phase = _Phase(TRIAGE_PROMPT, TRIAGE_SCHEMA, self._triage, STAGE_TRIAGE)
        async with self._client() as client:
            try:
                response = await _once(client, phase, triage_text(brief), budget)
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error
        return _parsed(_Triage, response, "da triagem").draft()

    async def consult(
        self, context: str, question: str, toolbox: Toolbox, budget: TokenBudget
    ) -> ConsoleDraft:
        phase = _Phase(CONSOLE_PROMPT, CONSOLE_SCHEMA, self._console, STAGE_CONSOLE, wrap_up=True)
        text = console_text(context, question)
        messages: list[MessageParam] = [{"role": "user", "content": text}]
        async with self._client() as client:
            try:
                response = await _loop(client, phase, messages, toolbox, budget)
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error
        answer = _parsed(_Console, response, "da resposta do console")
        if not answer.text.strip() and answer.scope is Scope.DOMAIN:  # a recusa troca o texto
            raise InvalidAnalysisError("resposta do console vazia")
        return ConsoleDraft(
            text=answer.text.strip(),
            charts=tuple(ChartRequest(c.service, c.signal, c.minutes) for c in answer.charts),
            scope=answer.scope,
        )

    async def extract_transcript(
        self, context: str, transcript: str, budget: TokenBudget
    ) -> TranscriptDraft:
        phase = _Phase(TRANSCRIPT_PROMPT, TRANSCRIPT_SCHEMA, self._transcript, STAGE_TRANSCRIPT)
        async with self._client() as client:
            try:
                response = await _once(client, phase, transcript_text(context, transcript), budget)
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error
        return _parsed(_Transcript, response, "da transcrição").draft()

    async def learn(self, brief: LearningBrief, budget: TokenBudget) -> LearningDraft:
        phase = _Phase(LEARNING_PROMPT, LEARNING_SCHEMA, self._learning, STAGE_LEARNING)
        async with self._client() as client:
            try:
                response = await _once(client, phase, learning_text(brief), budget)
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error
        learned = _parsed(_Learning, response, "do aprendizado")
        if not learned.episode_title.strip() or not learned.episode_body.strip():
            raise InvalidAnalysisError("episódio sem título ou sem texto")
        return LearningDraft(
            episode_title=learned.episode_title.strip(),
            episode_body=learned.episode_body.strip(),
            noise=learned.noise,
            dossiers=tuple(
                DossierDraft(d.subject.strip(), d.subject_kind, d.body.strip())
                for d in learned.dossiers
                if d.subject.strip() and d.body.strip()
            ),
        )

    async def study_service(
        self, brief: ServiceStudyBrief, toolbox: Toolbox, budget: TokenBudget
    ) -> DossierDraft:
        phase = _Phase(STUDY_PROMPT, STUDY_SCHEMA, self._study, STAGE_STUDY, wrap_up=True)
        messages: list[MessageParam] = [{"role": "user", "content": study_text(brief)}]
        async with self._client() as client:
            try:
                response = await _loop(client, phase, messages, toolbox, budget)
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error
        body = _parsed(_Study, response, "do estudo").dossier.strip()
        if not body:
            raise InvalidAnalysisError("dossiê do serviço vazio")
        # Fontes: só os links que as ferramentas devolveram, nunca os escritos pelo modelo.
        links = dict.fromkeys(e.url for e in toolbox.evaluations if e.ok and e.url)
        return DossierDraft(brief.service, SubjectKind.SERVICE, body, tuple(links))

    async def synthesize(self, brief: SynthesisBrief, budget: TokenBudget) -> SynthesisDraft:
        phase = _Phase(SYNTHESIS_PROMPT, SYNTHESIS_SCHEMA, self._study, STAGE_STUDY)
        async with self._client() as client:
            try:
                response = await _once(client, phase, synthesis_text(brief), budget)
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error
        synthesis = _parsed(_Synthesis, response, "da síntese")
        if not synthesis.environment.strip():
            raise InvalidAnalysisError("dossiê do ambiente vazio")
        return SynthesisDraft(
            environment=DossierDraft(
                ENVIRONMENT, SubjectKind.ENVIRONMENT, synthesis.environment.strip()
            ),
            integrations=tuple(
                DossierDraft(i.subject.strip(), SubjectKind.INTEGRATION, i.body.strip())
                for i in synthesis.integrations
                if i.subject.strip() and i.body.strip()
            ),
        )

    def _client(self) -> _Client:
        http_client = (
            None
            if self._transport is None
            else anthropic.DefaultAsyncHttpxClient(transport=self._transport)
        )
        if isinstance(self._connection, Bedrock):
            return AsyncAnthropicBedrock(
                aws_region=self._connection.region,
                http_client=http_client,
                max_retries=MAX_RETRIES,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        return anthropic.AsyncAnthropic(
            api_key=self._connection.api_key,
            base_url=self._connection.base_url,
            http_client=http_client,
            max_retries=MAX_RETRIES,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )


async def _loop(
    client: _Client,
    phase: _Phase,
    messages: list[MessageParam],
    toolbox: Toolbox,
    budget: TokenBudget,
) -> Message:
    """Roda as ferramentas pedidas até a resposta final e a devolve; estende `messages`.

    Com `wrap_up`, quando o que sobra do orçamento não paga mais uma volta, a última chamada vai
    sem ferramentas: o modelo responde com o que já consultou em vez de estourar o orçamento.
    """
    tools = _tools(toolbox)
    wrapping_up = False
    while True:
        choice: ToolChoiceParam | Omit = {"type": "none"} if wrapping_up else omit
        response = await client.messages.create(
            model=phase.settings.model,
            max_tokens=phase.settings.max_tokens,
            system=[{"type": "text", "text": phase.system, "cache_control": _CACHE_1H}],
            tools=tools,
            tool_choice=choice,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config=phase.output_config(),
        )
        usage = _usage(response, phase.stage)
        budget.spend(usage)
        if response.stop_reason != "tool_use" or wrapping_up:
            return response
        calls = [block for block in response.content if isinstance(block, ToolUseBlock)]
        results: list[ToolResultBlockParam | TextBlockParam] = list(
            await asyncio.gather(*(_run(toolbox, call) for call in calls))
        )
        if phase.wrap_up and budget.remaining < WRAP_UP_TURNS * _tokens(usage):
            results.append({"type": "text", "text": WRAP_UP})
            wrapping_up = True
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": results})


async def _once(client: _Client, phase: _Phase, text: str, budget: TokenBudget) -> Message:
    """Uma chamada sem ferramentas, só com a saída estruturada da etapa."""
    response = await client.messages.create(
        model=phase.settings.model,
        max_tokens=phase.settings.max_tokens,
        system=[{"type": "text", "text": phase.system, "cache_control": _CACHE_1H}],
        messages=[{"role": "user", "content": text}],
        thinking={"type": "adaptive"},
        output_config=phase.output_config(),
    )
    budget.spend(_usage(response, phase.stage))
    return response


def _parsed[T: BaseModel](model: type[T], response: Message, what: str) -> T:
    try:
        return model.model_validate_json(_final_text(response))
    except ValidationError as error:
        raise InvalidAnalysisError(f"resultado fora do formato {what}") from error


async def _concrete(
    client: _Client,
    phase: _Phase,
    messages: list[MessageParam],
    toolbox: Toolbox,
    budget: TokenBudget,
) -> VerificationDraft:
    """Validação com plano concreto: uma correção só, na mesma conversa e no mesmo orçamento."""
    response = await _loop(client, phase, messages, toolbox, budget)
    draft = _verification(response)
    try:
        check_plan(draft)
    except NotConcreteError:
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": NOT_CONCRETE})
        draft = _verification(await _loop(client, phase, messages, toolbox, budget))
        try:
            check_plan(draft)
        except NotConcreteError as error:
            raise InvalidAnalysisError(f"plano não concreto: {error}") from error
    return draft


def _tools(toolbox: Toolbox) -> list[ToolParam]:
    tools: list[ToolParam] = [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": dict(spec.input_schema),
        }
        for spec in toolbox.specs()
    ]
    if tools:
        tools[-1]["cache_control"] = _CACHE_1H
    return tools


def _tokens(usage: LlmUsage) -> int:
    return (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_creation_tokens
    )


async def _run(toolbox: Toolbox, call: ToolUseBlock) -> ToolResultBlockParam:
    text = await toolbox.call(call.name, call.input)
    return {"type": "tool_result", "tool_use_id": call.id, "content": text}


def _usage(response: Message, stage: str) -> LlmUsage:
    usage = response.usage
    return LlmUsage(
        stage=stage,
        model=response.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
        cache_creation_tokens=usage.cache_creation_input_tokens or 0,
    )


def _final_text(response: Message) -> str:
    if response.stop_reason == "refusal":
        raise LanguageModelError("o modelo recusou a análise")
    if response.stop_reason != "end_turn":
        raise InvalidAnalysisError(f"resposta incompleta ({response.stop_reason})")
    return "".join(block.text for block in response.content if block.type == "text")


def _draft(response: Message) -> AnalysisDraft:
    try:
        draft = _Draft.model_validate_json(_final_text(response))
    except ValidationError as error:
        raise InvalidAnalysisError("resultado fora do formato da análise") from error
    return AnalysisDraft(
        summary=draft.summary,
        hypotheses=tuple(
            Hypothesis(
                title=item.title,
                confidence=item.confidence,
                evidence=tuple(Evidence(text=e.text, url=e.url) for e in item.evidence),
            )
            for item in draft.hypotheses
        ),
        plan=tuple(draft.plan),
        seen_before=draft.seen_before.draft() if draft.seen_before else None,
    )


def _verification(response: Message) -> VerificationDraft:
    try:
        draft = _Verification.model_validate_json(_final_text(response))
    except ValidationError as error:
        raise InvalidAnalysisError("resultado fora do formato da validação") from error
    return VerificationDraft(
        verdicts=tuple(
            HypothesisVerdict(
                hypothesis=item.hypothesis,
                verdict=item.verdict,
                reasoning=item.reasoning,
                evidence=tuple(Evidence(text=e.text, url=e.url) for e in item.evidence),
            )
            for item in draft.verdicts
        ),
        root_cause=draft.root_cause or None,  # texto vazio também é "não confirmada"
        actions=tuple(
            Action(
                a.action,
                a.target,
                a.expected,
                a.link,
                commands=usable_commands((c.command, c.run_in) for c in a.commands),
            )
            for a in draft.actions
        ),
        missing=draft.missing,
    )


def chat_text(context: str, question: str) -> str:
    """Mensagem do chat: o contexto do incidente e, por último, a pergunta."""
    return f"{context}\n\nPergunta: {question}"


def _chat_draft(response: Message) -> ChatDraft:
    try:
        draft = _Reply.model_validate_json(_final_text(response))
    except ValidationError as error:
        raise InvalidAnalysisError("resultado fora do formato da resposta do chat") from error
    if not draft.reply.strip() and draft.scope is Scope.DOMAIN:  # a recusa troca o texto
        raise InvalidAnalysisError("resposta do chat vazia")
    return ChatDraft(text=draft.reply.strip(), scope=draft.scope)


def _describe(error: anthropic.APIError) -> str:
    if isinstance(error, anthropic.APIStatusError):
        return f"HTTP {error.status_code}"
    return type(error).__name__

"""Fakes dos ports da investigação: telemetria, histórico de mudanças, nuvem e modelo de
linguagem.
"""

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from grolla.application.investigation import (
    BINARY_FILE,
    NO_CLOUD_RESOURCES,
    NO_DEPENDENCIES,
    NO_REPOSITORY,
    READ_LIMIT,
    STAGE_CONSOLE,
    STAGE_FOLLOW_UP,
    STAGE_INVESTIGATION,
    STAGE_LEARNING,
    STAGE_POST_MORTEM,
    STAGE_STUDY,
    STAGE_TRIAGE,
    STAGE_VERIFICATION,
    STAGE_WARROOM_CHAT,
    TRUNCATED,
    AlertRule,
    InvestigationBrief,
    LearningBrief,
    LlmUsage,
    PostMortemBrief,
    RepositoryDocument,
    ServiceStudyBrief,
    Signal,
    SynthesisBrief,
    TokenBudget,
    Toolbox,
    ToolResult,
    ToolUnavailableError,
    TriageBrief,
)
from grolla.domain.analysis import Analysis, AnalysisDraft, VerificationDraft
from grolla.domain.chat import ChatDraft
from grolla.domain.console import ChartRequest, ConsoleDraft
from grolla.domain.follow_up import Cycle, CycleDraft, Trend
from grolla.domain.incidents import IncidentState
from grolla.domain.memory import (
    ENVIRONMENT,
    DossierDraft,
    LearningDraft,
    SubjectKind,
    SynthesisDraft,
)
from grolla.domain.post_mortem import (
    DETERMINISTIC,
    SECTION_TITLES,
    ActionKind,
    PostMortemDraft,
    SectionKey,
)
from grolla.domain.triage import TriageDraft
from grolla.testing.builders import make_draft, make_verification_draft

FAKE_SOURCE_URL = "https://observabilidade.interno/consulta"
SERIES_POINTS = 12
SERIES_VALUES: Mapping[Signal, float] = {"taxa_de_erro": 0.2, "latencia_p99": 180.0, "vazao": 42.0}


class FakeTelemetry:
    """Telemetria roteirizada por ferramenta.

    `results` troca a resposta de uma ferramenta (`query_metric`, `search_logs`, `find_traces`);
    `rules` e `services` são as regras de alerta e os serviços ativos; `dependencies` mapeia
    serviço → resumo das dependências (fora do mapa, NO_DEPENDENCIES). As consultas em
    `failing` (inclusive `alert_rules`, `active_services`, `dependencies` e `series`) levantam
    `error` (ToolUnavailableError por padrão). Cada chamada fica em `calls`.

    `points` mapeia (serviço, sinal) → pontos da série; `series` devolve os que caem na janela.
    Fora do mapa, devolve SERIES_POINTS pontos igualmente espaçados na janela, com o valor de
    SERIES_VALUES do sinal.
    """

    def __init__(  # noqa: PLR0913 — cada consulta da telemetria é roteirizável no teste
        self,
        results: Mapping[str, ToolResult] | None = None,
        failing: Iterable[str] = (),
        error: type[Exception] = ToolUnavailableError,
        *,
        rules: Sequence[AlertRule] = (),
        services: Sequence[str] = (),
        dependencies: Mapping[str, str] | None = None,
        points: Mapping[tuple[str, str], Sequence[tuple[datetime, float]]] | None = None,
    ) -> None:
        self.points = {key: list(values) for key, values in (points or {}).items()}
        self.results = dict(results or {})
        self.failing = set(failing)
        self.error = error
        self.rules = list(rules)
        self.services = list(services)
        self.dependency_map = dict(dependencies or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def query_metric(
        self, service: str, signal: Signal, start: datetime, end: datetime
    ) -> ToolResult:
        args = {"service": service, "signal": signal, "start": start, "end": end}
        return self._answer("query_metric", args, f"{signal} de {service} estável na janela")

    async def search_logs(
        self, service: str, text: str | None, start: datetime, end: datetime
    ) -> ToolResult:
        args = {"service": service, "text": text, "start": start, "end": end}
        return self._answer("search_logs", args, f"nenhum log de erro de {service} na janela")

    async def find_traces(
        self, service: str, only_errors: bool, start: datetime, end: datetime
    ) -> ToolResult:
        args = {"service": service, "only_errors": only_errors, "start": start, "end": end}
        return self._answer("find_traces", args, f"nenhum trace com erro de {service} na janela")

    async def alert_rules(self) -> list[AlertRule]:
        self._called("alert_rules", {})
        return list(self.rules)

    async def active_services(self, since: datetime) -> list[str]:
        self._called("active_services", {"since": since})
        return list(self.services)

    async def series(
        self, service: str, signal: Signal, start: datetime, end: datetime
    ) -> list[tuple[datetime, float]]:
        self._called("series", {"service": service, "signal": signal, "start": start, "end": end})
        scripted = self.points.get((service, signal))
        if scripted is not None:
            return sorted(p for p in scripted if start <= p[0] <= end)
        step = (end - start) / (SERIES_POINTS - 1)
        return [(start + step * n, SERIES_VALUES[signal]) for n in range(SERIES_POINTS)]

    async def dependencies(self, service: str, start: datetime, end: datetime) -> ToolResult:
        self._called("dependencies", {"service": service, "start": start, "end": end})
        summary = self.dependency_map.get(service)
        if summary is None:
            return ToolResult(NO_DEPENDENCIES, None)
        return ToolResult(summary, FAKE_SOURCE_URL)

    def _called(self, tool: str, args: dict[str, Any]) -> None:
        self.calls.append((tool, args))
        if tool in self.failing:
            raise self.error(f"{tool} fora do ar no fake")

    def _answer(self, tool: str, args: dict[str, Any], default: str) -> ToolResult:
        self._called(tool, args)
        return self.results.get(tool, ToolResult(default, FAKE_SOURCE_URL))


class FakeChangeHistory:
    """Histórico de mudanças roteirizado: `deploys` mapeia serviço → resumo dos deploys e
    `files` mapeia serviço → {caminho: conteúdo} do repositório.

    Serviço fora dos dois mapas responde NO_REPOSITORY; com `unavailable`, toda chamada levanta
    ToolUnavailableError. Cada chamada de `recent_deploys` fica em `calls` como (serviço, since),
    cada `read_file` em `reads` como (serviço, caminho) e cada `documents` em `document_calls`.
    `documents` devolve os `.md` de `files`, menores primeiro (em bytes), até os limites.
    """

    def __init__(
        self,
        deploys: Mapping[str, str] | None = None,
        unavailable: bool = False,
        *,
        files: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self.deploys = dict(deploys or {})
        self.files = {service: dict(paths) for service, paths in (files or {}).items()}
        self.unavailable = unavailable
        self.calls: list[tuple[str, datetime]] = []
        self.reads: list[tuple[str, str]] = []
        self.document_calls: list[str] = []

    async def recent_deploys(self, service: str, since: datetime) -> ToolResult:
        self.calls.append((service, since))
        if self.unavailable:
            raise ToolUnavailableError("histórico de mudanças fora do ar no fake")
        if service not in self.deploys:
            return ToolResult(NO_REPOSITORY, None)
        return ToolResult(self.deploys[service], f"https://pipelines.interno/{service}")

    async def documents(
        self, service: str, max_files: int, max_bytes: int
    ) -> list[RepositoryDocument]:
        self.document_calls.append(service)
        if self.unavailable:
            raise ToolUnavailableError("histórico de mudanças fora do ar no fake")
        markdown = sorted(
            (len(content.encode()), path, content)
            for path, content in self.files.get(service, {}).items()
            if path.casefold().endswith(".md")
        )
        found: list[RepositoryDocument] = []
        total = 0
        for size, path, content in markdown[:max_files]:
            if total + size > max_bytes:
                break
            total += size
            found.append(
                RepositoryDocument(path, f"https://repositorio.interno/{service}/{path}", content)
            )
        return found

    async def read_file(self, service: str, path: str) -> ToolResult:
        self.reads.append((service, path))
        if self.unavailable:
            raise ToolUnavailableError("histórico de mudanças fora do ar no fake")
        if service not in self.deploys and service not in self.files:
            return ToolResult(NO_REPOSITORY, None)
        files = self.files.get(service, {})
        url = f"https://repositorio.interno/{service}/{path}"
        folder = path.rstrip("/")
        if folder not in files:
            inside = sorted(p for p in files if not folder or p.startswith(f"{folder}/"))
            if inside or not folder:
                return ToolResult("\n".join(inside) or "pasta vazia", url)
            return ToolResult(f"arquivo não encontrado: {path}", None)
        content = files[folder]
        if "\x00" in content:
            return ToolResult(f"{BINARY_FILE}: {path}", url)
        if len(content) > READ_LIMIT:
            return ToolResult(f"{content[:READ_LIMIT]}\n[{TRUNCATED} em {READ_LIMIT}]", url)
        return ToolResult(content, url)


CLOUD_TOOLS = ("resources", "changes", "alarms", "overview")


class FakeCloudInventory:
    """Nuvem roteirizada: `resources`, `changes` e `alarms` mapeiam serviço → resumo.

    Serviço sem recursos no mapa responde NO_CLOUD_RESOURCES; sem mudanças ou alarmes no mapa,
    responde que não há. As consultas em `failing` (nomes de `CLOUD_TOOLS`) levantam
    ToolUnavailableError. Cada chamada fica em `calls` como (consulta, serviço); a visão geral
    (`overview`) responde o texto do mesmo nome, com o serviço vazio em `calls`.
    """

    def __init__(
        self,
        resources: Mapping[str, str] | None = None,
        changes: Mapping[str, str] | None = None,
        alarms: Mapping[str, str] | None = None,
        failing: Iterable[str] = (),
        *,
        overview: str = "sem serviço: rede vpc-lab (available)",
    ) -> None:
        self.overview_text = overview
        self.answers = {
            "resources": dict(resources or {}),
            "changes": dict(changes or {}),
            "alarms": dict(alarms or {}),
        }
        self.failing = set(failing)
        self.calls: list[tuple[str, str]] = []
        self.since: list[datetime] = []

    async def resources(self, service: str) -> ToolResult:
        return self._answer("resources", service, NO_CLOUD_RESOURCES)

    async def changes(self, service: str, since: datetime) -> ToolResult:
        self.since.append(since)
        return self._answer("changes", service, f"nenhuma mudança nos recursos de {service}")

    async def alarms(self, service: str) -> ToolResult:
        return self._answer("alarms", service, f"não há alarmes ativos para {service}")

    async def overview(self) -> ToolResult:
        self.calls.append(("overview", ""))
        if "overview" in self.failing:
            raise ToolUnavailableError("overview da nuvem fora do ar no fake")
        return ToolResult(self.overview_text, "https://console.nuvem.interno/")

    def _answer(self, tool: str, service: str, default: str) -> ToolResult:
        self.calls.append((tool, service))
        if tool in self.failing:
            raise ToolUnavailableError(f"{tool} da nuvem fora do ar no fake")
        summary = self.answers[tool].get(service)
        if summary is None:
            return ToolResult(default, None)
        return ToolResult(summary, f"https://console.nuvem.interno/{service}")


FAKE_MODEL = "modelo-fake"
CONSOLE_CHART_MINUTES = 60


def fake_usage(tokens: int = 1_000, stage: str = STAGE_INVESTIGATION) -> LlmUsage:
    """Uso de uma resposta do modelo: `tokens` de entrada e um décimo disso de saída."""
    return LlmUsage(stage, FAKE_MODEL, tokens, tokens // 10, 0, 0)


ToolCalls = Sequence[tuple[str, Mapping[str, Any]]]


class ScriptedLanguageModel:
    """Modelo de linguagem roteirizado.

    Em `investigate`: espera `delay` segundos, faz as chamadas de ferramenta de `calls` pelo
    toolbox (gastando `usage` no orçamento a cada turno, inclusive o final) e então levanta
    `error`, se houver, ou devolve `draft`. Os briefs recebidos ficam em `briefs` e as
    respostas das ferramentas em `tool_answers`.

    Em `verify`, o mesmo roteiro com `verification_calls`, `verification_error`,
    `verification_delay` e `verification` (o rascunho devolvido), gastando o uso com a etapa
    `verification`. As análises recebidas ficam em `verified_analyses`.

    Em `reply`, o mesmo roteiro com `chat_calls`, `chat_error`, `chat_delay` e `chat` (a
    resposta devolvida), gastando o uso com a etapa `warroom_chat`. Cada (contexto, pergunta)
    recebido fica em `questions`.

    Em `follow_up`, o mesmo roteiro com `follow_up_calls`, `follow_up_error`, `follow_up_delay`
    e `follow_up_draft` (o ciclo devolvido; por padrão, `normalizado` com 0% de erro), gastando
    o uso com a etapa `follow_up`. Cada (contexto, ciclo anterior) recebido fica em `follow_ups`.

    Em `learn`, sem ferramentas: gasta um uso da etapa `learning` e levanta `learning_error` ou
    devolve `learning` (por padrão, um episódio e o dossiê de cada serviço do incidente). Em
    `study_service`, faz `study_calls`, gasta com a etapa `study` e levanta o erro de
    `study_errors` do serviço ou devolve o dossiê do serviço com os links consultados. Em
    `synthesize`, gasta com a etapa `study` e levanta `synthesis_error` ou devolve `synthesis`
    (por padrão, só o dossiê do ambiente). Os briefs ficam em `learning_briefs`, `study_briefs`
    e `synthesis_briefs`.

    Em `triage`, sem ferramentas: gasta um uso da etapa `triage` e levanta `triage_error` ou
    devolve `triage_draft` (por padrão, nada a juntar e nenhuma severidade sugerida). Os briefs
    ficam em `triage_briefs`.

    Em `consult`, o mesmo roteiro de `reply` com `console_calls`, `console_error`,
    `console_delay` e `console` (a resposta devolvida), gastando o uso com a etapa `console`.
    Cada (contexto, pergunta) recebido fica em `consultations`. Por padrão, a resposta é um
    texto curto sobre o primeiro serviço citado nos argumentos de `console_calls`, com o gráfico
    de `taxa_de_erro` dele na última hora; sem serviço citado, um texto curto sem gráfico.
    """

    def __init__(  # noqa: PLR0913 — o roteiro de cada fase é ajustável no teste
        self,
        draft: AnalysisDraft | None = None,
        *,
        calls: ToolCalls = (),
        error: Exception | None = None,
        delay: float = 0.0,
        usage: LlmUsage | None = None,
        verification: VerificationDraft | None = None,
        verification_calls: ToolCalls = (),
        verification_error: Exception | None = None,
        verification_delay: float = 0.0,
        chat: ChatDraft | None = None,
        chat_calls: ToolCalls = (),
        chat_error: Exception | None = None,
        chat_delay: float = 0.0,
        learning: LearningDraft | None = None,
        learning_error: Exception | None = None,
        study_calls: ToolCalls = (),
        study_errors: Mapping[str, Exception] | None = None,
        synthesis: SynthesisDraft | None = None,
        synthesis_error: Exception | None = None,
        follow_up_draft: CycleDraft | None = None,
        follow_up_calls: ToolCalls = (),
        follow_up_error: Exception | None = None,
        follow_up_delay: float = 0.0,
        post_mortem: PostMortemDraft | None = None,
        post_mortem_error: Exception | None = None,
        post_mortem_delay: float = 0.0,
        regeneration: str | None = None,
        regeneration_error: Exception | None = None,
        triage_draft: TriageDraft | None = None,
        triage_error: Exception | None = None,
        console: ConsoleDraft | None = None,
        console_calls: ToolCalls = (),
        console_error: Exception | None = None,
        console_delay: float = 0.0,
    ) -> None:
        self.console = console
        self.console_calls = list(console_calls)
        self.console_error = console_error
        self.console_delay = console_delay
        self.consultations: list[tuple[str, str]] = []
        self.triage_draft = triage_draft or TriageDraft(None, "", None, "")
        self.triage_error = triage_error
        self.triage_briefs: list[TriageBrief] = []
        self.post_mortem = post_mortem
        self.post_mortem_error = post_mortem_error
        self.post_mortem_delay = post_mortem_delay
        self.regeneration = regeneration
        self.regeneration_error = regeneration_error
        self.post_mortem_briefs: list[PostMortemBrief] = []
        self.regenerations: list[tuple[PostMortemBrief, SectionKey, str | None]] = []
        self.follow_up_draft = follow_up_draft or CycleDraft(
            Trend.NORMAL, 0.0, "Serviço normalizado: erro em 0%.", changed=False
        )
        self.follow_up_calls = list(follow_up_calls)
        self.follow_up_error = follow_up_error
        self.follow_up_delay = follow_up_delay
        self.follow_ups: list[tuple[str, Cycle | None]] = []
        self.learning = learning
        self.learning_error = learning_error
        self.study_calls = list(study_calls)
        self.study_errors = dict(study_errors or {})
        self.synthesis = synthesis
        self.synthesis_error = synthesis_error
        self.learning_briefs: list[LearningBrief] = []
        self.study_briefs: list[ServiceStudyBrief] = []
        self.synthesis_briefs: list[SynthesisBrief] = []
        self.draft = draft or make_draft()
        self.calls = list(calls)
        self.error = error
        self.delay = delay
        self.usage = usage or fake_usage()
        self.verification = verification or make_verification_draft()
        self.verification_calls = list(verification_calls)
        self.verification_error = verification_error
        self.verification_delay = verification_delay
        self.chat = chat or ChatDraft("O erro caiu para 0,2% depois do rollback.")
        self.chat_calls = list(chat_calls)
        self.chat_error = chat_error
        self.chat_delay = chat_delay
        self.questions: list[tuple[str, str]] = []
        self.briefs: list[InvestigationBrief] = []
        self.verified_analyses: list[Analysis] = []
        self.tool_answers: list[str] = []

    async def investigate(
        self, brief: InvestigationBrief, toolbox: Toolbox, budget: TokenBudget
    ) -> AnalysisDraft:
        self.briefs.append(brief)
        await self._play(self.delay, self.calls, self.usage, toolbox, budget)
        if self.error is not None:
            raise self.error
        return self.draft

    async def reply(
        self,
        brief: InvestigationBrief,
        context: str,
        question: str,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> ChatDraft:
        self.briefs.append(brief)
        self.questions.append((context, question))
        usage = replace(self.usage, stage=STAGE_WARROOM_CHAT)
        await self._play(self.chat_delay, self.chat_calls, usage, toolbox, budget)
        if self.chat_error is not None:
            raise self.chat_error
        return self.chat

    async def verify(
        self,
        brief: InvestigationBrief,
        analysis: Analysis,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> VerificationDraft:
        self.briefs.append(brief)
        self.verified_analyses.append(analysis)
        usage = replace(self.usage, stage=STAGE_VERIFICATION)
        await self._play(self.verification_delay, self.verification_calls, usage, toolbox, budget)
        if self.verification_error is not None:
            raise self.verification_error
        return self.verification

    async def follow_up(
        self,
        brief: InvestigationBrief,
        context: str,
        previous: Cycle | None,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> CycleDraft:
        self.briefs.append(brief)
        self.follow_ups.append((context, previous))
        usage = replace(self.usage, stage=STAGE_FOLLOW_UP)
        await self._play(self.follow_up_delay, self.follow_up_calls, usage, toolbox, budget)
        if self.follow_up_error is not None:
            raise self.follow_up_error
        return self.follow_up_draft

    async def draft_post_mortem(
        self, brief: PostMortemBrief, budget: TokenBudget
    ) -> PostMortemDraft:
        """Padrão: uma frase por seção escrita pelo modelo e dois itens no plano de ação."""
        self.post_mortem_briefs.append(brief)
        if self.post_mortem_delay:
            await asyncio.sleep(self.post_mortem_delay)
        budget.spend(replace(self.usage, stage=STAGE_POST_MORTEM))
        if self.post_mortem_error is not None:
            raise self.post_mortem_error
        title = brief.incident.title
        return self.post_mortem or PostMortemDraft(
            sections={
                key: f"{SECTION_TITLES[key]} de {title}."
                for key in SectionKey
                if key not in DETERMINISTIC
            },
            actions=(
                ("Criar alarme de conexões no banco", ActionKind.DETECT),
                ("Limitar o pool de conexões por instância", ActionKind.PREVENT),
            ),
        )

    async def regenerate_section(
        self,
        brief: PostMortemBrief,
        key: SectionKey,
        instruction: str | None,
        budget: TokenBudget,
    ) -> str:
        """Padrão: o título da seção reescrita, com a instrução, se houver."""
        self.regenerations.append((brief, key, instruction))
        budget.spend(replace(self.usage, stage=STAGE_POST_MORTEM))
        if self.regeneration_error is not None:
            raise self.regeneration_error
        asked = f" ({instruction})" if instruction else ""
        return self.regeneration or f"{SECTION_TITLES[key]} reescrita{asked}."

    async def triage(self, brief: TriageBrief, budget: TokenBudget) -> TriageDraft:
        self.triage_briefs.append(brief)
        budget.spend(replace(self.usage, stage=STAGE_TRIAGE))
        if self.triage_error is not None:
            raise self.triage_error
        return self.triage_draft

    async def consult(
        self, context: str, question: str, toolbox: Toolbox, budget: TokenBudget
    ) -> ConsoleDraft:
        self.consultations.append((context, question))
        usage = replace(self.usage, stage=STAGE_CONSOLE)
        await self._play(self.console_delay, self.console_calls, usage, toolbox, budget)
        if self.console_error is not None:
            raise self.console_error
        if self.console is not None:
            return self.console
        cited = [str(args["service"]) for _, args in self.console_calls if "service" in args]
        if not cited:
            return ConsoleDraft("Nada fora do normal nas consultas.")
        chart = ChartRequest(cited[0], "taxa_de_erro", CONSOLE_CHART_MINUTES)
        return ConsoleDraft(f"{cited[0]} estável: taxa de erro em 0,2% na última hora.", (chart,))

    async def learn(self, brief: LearningBrief, budget: TokenBudget) -> LearningDraft:
        self.learning_briefs.append(brief)
        budget.spend(replace(self.usage, stage=STAGE_LEARNING))
        if self.learning_error is not None:
            raise self.learning_error
        incident = brief.incident
        return self.learning or LearningDraft(
            episode_title=incident.title,
            episode_body=f"Sintomas e causa de {incident.title}.",
            noise=incident.state is IncidentState.DISCARDED,
            dossiers=tuple(
                DossierDraft(
                    s, SubjectKind.SERVICE, f"Dossiê de {s}: aprendido com {incident.title}."
                )
                for s in incident.services
            ),
        )

    async def study_service(
        self, brief: ServiceStudyBrief, toolbox: Toolbox, budget: TokenBudget
    ) -> DossierDraft:
        self.study_briefs.append(brief)
        usage = replace(self.usage, stage=STAGE_STUDY)
        await self._play(0.0, self.study_calls, usage, toolbox, budget)
        error = self.study_errors.get(brief.service)
        if error is not None:
            raise error
        rules = ", ".join(rule.name for rule in brief.rules) or "nenhuma"
        return DossierDraft(
            brief.service,
            SubjectKind.SERVICE,
            f"Dossiê de {brief.service}. Alertas: {rules}.",
            tuple(dict.fromkeys(e.url for e in toolbox.evaluations if e.url)),
        )

    async def synthesize(self, brief: SynthesisBrief, budget: TokenBudget) -> SynthesisDraft:
        self.synthesis_briefs.append(brief)
        budget.spend(replace(self.usage, stage=STAGE_STUDY))
        if self.synthesis_error is not None:
            raise self.synthesis_error
        services = ", ".join(d.subject for d in brief.services) or "nenhum"
        environment = DossierDraft(
            ENVIRONMENT, SubjectKind.ENVIRONMENT, f"Ambiente com os serviços: {services}."
        )
        return self.synthesis or SynthesisDraft(environment, ())

    async def _play(
        self, delay: float, calls: ToolCalls, usage: LlmUsage, toolbox: Toolbox, budget: TokenBudget
    ) -> None:
        if delay:
            await asyncio.sleep(delay)
        for name, arguments in calls:
            budget.spend(usage)
            self.tool_answers.append(await toolbox.call(name, arguments))
        budget.spend(usage)

"""Suíte de contrato de LanguageModel.

O adapter sobrescreve três fixtures, cada uma o adapter com um roteiro do transporte fake:
- `model`: chama ao menos uma ferramenta pelo toolbox e devolve um resultado válido;
- `failing_model`: a API responde erro (fora do ar, recusa) e o adapter levanta
  LanguageModelError;
- `invalid_model`: a API devolve um resultado fora do schema e o adapter levanta
  InvalidAnalysisError.

E as mesmas três para a validação (`verify`): `verifying_model`, `failing_verifier` e
`invalid_verifier`. A `analysis` validada vem pronta.

A resposta no chat do war room (`reply`) tem a suíte própria, `ChatReplyContract`, com as
fixtures `replying_model` (chama ao menos uma ferramenta e responde em texto) e
`failing_replier` (a API responde erro).

O ciclo de acompanhamento (`follow_up`) tem a suíte `FollowUpModelContract`, com as fixtures
`following_model` (chama ao menos uma ferramenta e devolve o ciclo) e `failing_follower` (a API
responde erro).

O post mortem (`draft_post_mortem` e `regenerate_section`) tem a suíte `PostMortemModelContract`,
com as fixtures `post_mortem_model` e `failing_post_mortem_model`.

A triagem (`triage`) tem a suíte `TriageModelContract`, com as fixtures `triage_model` (devolve
uma triagem válida) e `failing_triage_model` (a API responde erro).

A resposta do console (`consult`) tem a suíte `ConsoleModelContract`, com as fixtures
`consulting_model` (chama ao menos uma ferramenta e responde em texto, com ou sem gráficos) e
`failing_consultant` (a API responde erro).

A extração da transcrição de call (`extract_transcript`) tem a suíte `TranscriptModelContract`,
com as fixtures `transcript_model` (devolve uma extração válida) e `failing_transcript_model`
(a API responde erro).

`toolbox`, `budget` e `brief` vêm prontos, sobre os fakes de telemetria e de mudanças.
"""

from dataclasses import replace
from datetime import timedelta

import pytest
from grolla.application.investigation import (
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
    AlertRule,
    BudgetExceededError,
    InvalidAnalysisError,
    InvestigationBrief,
    LanguageModel,
    LanguageModelError,
    LearningBrief,
    PostMortemBrief,
    ServiceStudyBrief,
    SynthesisBrief,
    TokenBudget,
    Toolbox,
    TriageBrief,
)
from grolla.domain.analysis import Analysis, AnalysisDraft, VerificationDraft, check_plan
from grolla.domain.chat import ChatDraft
from grolla.domain.console import CHART_SIGNALS, ConsoleDraft
from grolla.domain.follow_up import Cycle, CycleDraft, CycleStatus, Trend
from grolla.domain.incidents import EventType, IncidentEvent, IncidentState, Severity
from grolla.domain.memory import DossierDraft, LearningDraft, MemoryContext, SynthesisDraft
from grolla.domain.post_mortem import (
    DETERMINISTIC,
    ActionKind,
    PostMortemDraft,
    PostMortemStatus,
    SectionKey,
)
from grolla.domain.transcript import (
    HypothesisVerdict,
    TranscriptDraft,
    TranscriptItemKind,
)
from grolla.domain.triage import TriageDraft
from grolla.testing.builders import T0, make_alert, make_analysis, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.investigation import FakeChangeHistory, FakeTelemetry
from grolla.testing.post_mortem import make_post_mortem

SERVICE = "authorizer-api"


class LanguageModelFixtures:
    """Toolbox, orçamento e brief comuns às suítes do modelo de linguagem."""

    @pytest.fixture
    def toolbox(self) -> Toolbox:
        return Toolbox(
            FakeTelemetry(),
            FakeChangeHistory({SERVICE: "1 deploy: 2.4.1 às 09:58 por ana"}),
            FakeClock(T0),
            lookback=timedelta(minutes=60),
        )

    @pytest.fixture
    def budget(self) -> TokenBudget:
        return TokenBudget(limit=1_000_000)

    @pytest.fixture
    def brief(self) -> InvestigationBrief:
        incident = make_incident(
            services=(SERVICE,), severity=Severity.P2, state=IncidentState.INFORMED
        )
        return InvestigationBrief(incident, (make_alert(service=SERVICE),), now=T0)


class LanguageModelContract(LanguageModelFixtures):
    @pytest.fixture
    def model(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def failing_model(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def invalid_model(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def verifying_model(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def failing_verifier(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def invalid_verifier(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def analysis(self, brief: InvestigationBrief) -> Analysis:
        return make_analysis(brief.incident.id)

    async def test_investigation_returns_a_valid_draft(
        self,
        model: LanguageModel,
        brief: InvestigationBrief,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        draft = await model.investigate(brief, toolbox, budget)

        assert isinstance(draft, AnalysisDraft)
        assert draft.summary.strip()
        assert isinstance(draft.plan, tuple)
        for hypothesis in draft.hypotheses:
            assert hypothesis.title.strip()
            assert 0 <= hypothesis.confidence <= 1
            assert hypothesis.evidence

    async def test_tools_are_called_through_the_toolbox(
        self,
        model: LanguageModel,
        brief: InvestigationBrief,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        await model.investigate(brief, toolbox, budget)

        assert toolbox.evaluations
        assert {e.tool for e in toolbox.evaluations} <= {s.name for s in toolbox.specs()}

    async def test_every_response_is_spent_on_the_budget(
        self,
        model: LanguageModel,
        brief: InvestigationBrief,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        await model.investigate(brief, toolbox, budget)

        assert len(budget.usages) >= 2  # ao menos a resposta com ferramenta e a final
        for usage in budget.usages:
            assert usage.stage == STAGE_INVESTIGATION
            assert usage.model
            assert usage.input_tokens + usage.output_tokens > 0

    async def test_exhausted_budget_stops_the_investigation(
        self, model: LanguageModel, brief: InvestigationBrief, toolbox: Toolbox
    ) -> None:
        with pytest.raises(BudgetExceededError):
            await model.investigate(brief, toolbox, TokenBudget(limit=1))

    async def test_api_failure_raises_language_model_error(
        self,
        failing_model: LanguageModel,
        brief: InvestigationBrief,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        with pytest.raises(LanguageModelError):
            await failing_model.investigate(brief, toolbox, budget)

    async def test_result_out_of_schema_raises_invalid_analysis(
        self,
        invalid_model: LanguageModel,
        brief: InvestigationBrief,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        with pytest.raises(InvalidAnalysisError):
            await invalid_model.investigate(brief, toolbox, budget)

    # ---- Validação

    async def test_verification_returns_a_valid_concrete_draft(
        self,
        verifying_model: LanguageModel,
        brief: InvestigationBrief,
        analysis: Analysis,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        draft = await verifying_model.verify(brief, analysis, toolbox, budget)

        assert isinstance(draft, VerificationDraft)
        assert draft.verdicts
        for verdict in draft.verdicts:
            assert verdict.hypothesis.strip()
            assert verdict.reasoning.strip()
            assert verdict.evidence
        check_plan(draft)  # plano concreto, e só com causa confirmada

    async def test_verification_calls_tools_and_spends_with_its_stage(
        self,
        verifying_model: LanguageModel,
        brief: InvestigationBrief,
        analysis: Analysis,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        await verifying_model.verify(brief, analysis, toolbox, budget)

        assert toolbox.evaluations
        assert len(budget.usages) >= 2
        assert {usage.stage for usage in budget.usages} == {STAGE_VERIFICATION}

    async def test_exhausted_budget_stops_the_verification(
        self,
        verifying_model: LanguageModel,
        brief: InvestigationBrief,
        analysis: Analysis,
        toolbox: Toolbox,
    ) -> None:
        with pytest.raises(BudgetExceededError):
            await verifying_model.verify(brief, analysis, toolbox, TokenBudget(limit=1))

    async def test_verification_api_failure_raises_language_model_error(
        self,
        failing_verifier: LanguageModel,
        brief: InvestigationBrief,
        analysis: Analysis,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        with pytest.raises(LanguageModelError):
            await failing_verifier.verify(brief, analysis, toolbox, budget)

    async def test_verification_out_of_schema_raises_invalid_analysis(
        self,
        invalid_verifier: LanguageModel,
        brief: InvestigationBrief,
        analysis: Analysis,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        with pytest.raises(InvalidAnalysisError):
            await invalid_verifier.verify(brief, analysis, toolbox, budget)


CONTEXT = f"Incidente: Taxa de erro alta\nServiços: {SERVICE}"
QUESTION = "o rollback resolveu?"


class ChatReplyContract(LanguageModelFixtures):
    @pytest.fixture
    def replying_model(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def failing_replier(self) -> LanguageModel:
        raise NotImplementedError

    async def test_reply_is_a_text(
        self,
        replying_model: LanguageModel,
        brief: InvestigationBrief,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        draft = await replying_model.reply(brief, CONTEXT, QUESTION, toolbox, budget)

        assert isinstance(draft, ChatDraft)
        assert draft.text.strip()

    async def test_reply_calls_tools_and_spends_with_its_stage(
        self,
        replying_model: LanguageModel,
        brief: InvestigationBrief,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        await replying_model.reply(brief, CONTEXT, QUESTION, toolbox, budget)

        assert toolbox.evaluations
        assert len(budget.usages) >= 2
        assert {usage.stage for usage in budget.usages} == {STAGE_WARROOM_CHAT}

    async def test_exhausted_budget_stops_the_reply(
        self, replying_model: LanguageModel, brief: InvestigationBrief, toolbox: Toolbox
    ) -> None:
        with pytest.raises(BudgetExceededError):
            await replying_model.reply(brief, CONTEXT, QUESTION, toolbox, TokenBudget(limit=1))

    async def test_reply_api_failure_raises_language_model_error(
        self,
        failing_replier: LanguageModel,
        brief: InvestigationBrief,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        with pytest.raises(LanguageModelError):
            await failing_replier.reply(brief, CONTEXT, QUESTION, toolbox, budget)


MAX_EVIDENCE = 3


class FollowUpModelContract(LanguageModelFixtures):
    @pytest.fixture
    def following_model(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def failing_follower(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def previous(self, brief: InvestigationBrief) -> Cycle:
        return Cycle(
            brief.incident.id,
            1,
            CycleStatus.COMPLETED,
            T0,
            started_at=T0,
            finished_at=T0,
            trend=Trend.STABLE,
            error_rate=12.0,
            summary="Erro estável em 12%.",
        )

    async def test_follow_up_returns_a_valid_cycle(
        self,
        following_model: LanguageModel,
        brief: InvestigationBrief,
        previous: Cycle,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        draft = await following_model.follow_up(brief, CONTEXT, previous, toolbox, budget)

        assert isinstance(draft, CycleDraft)
        assert isinstance(draft.trend, Trend)
        assert draft.error_rate is None or 0 <= draft.error_rate <= 100
        assert draft.summary.strip()
        assert len(draft.evidence) <= MAX_EVIDENCE

    async def test_follow_up_calls_tools_and_spends_with_its_stage(
        self,
        following_model: LanguageModel,
        brief: InvestigationBrief,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        await following_model.follow_up(brief, CONTEXT, None, toolbox, budget)

        assert toolbox.evaluations
        assert len(budget.usages) >= 2
        assert {usage.stage for usage in budget.usages} == {STAGE_FOLLOW_UP}

    async def test_exhausted_budget_stops_the_follow_up(
        self, following_model: LanguageModel, brief: InvestigationBrief, toolbox: Toolbox
    ) -> None:
        with pytest.raises(BudgetExceededError):
            await following_model.follow_up(brief, CONTEXT, None, toolbox, TokenBudget(limit=1))

    async def test_follow_up_api_failure_raises_language_model_error(
        self,
        failing_follower: LanguageModel,
        brief: InvestigationBrief,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        with pytest.raises(LanguageModelError):
            await failing_follower.follow_up(brief, CONTEXT, None, toolbox, budget)


class MemoryModelContract(LanguageModelFixtures):
    """As etapas da memória viva: `learn`, `study_service` e `synthesize`.

    O adapter sobrescreve `memory_model` (as três respondem um resultado válido e o estudo do
    serviço chama ao menos uma ferramenta) e `failing_memory_model` (a API responde erro).
    """

    @pytest.fixture
    def memory_model(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def failing_memory_model(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def learning_brief(self) -> LearningBrief:
        incident = make_incident(services=(SERVICE,), state=IncidentState.RESOLVED)
        event = IncidentEvent(
            incident.id, T0, EventType.INCIDENT_RESOLVED, {"reason": "rollback"}, "Ana"
        )
        return LearningBrief(incident, (event,), MemoryContext(), now=T0)

    @pytest.fixture
    def study_brief(self) -> ServiceStudyBrief:
        return ServiceStudyBrief(SERVICE, (RULE,), MemoryContext(), now=T0)

    @pytest.fixture
    def synthesis_brief(self) -> SynthesisBrief:
        return SynthesisBrief((RULE,), (), MemoryContext(), "sem serviço: vpc-lab", now=T0)

    async def test_learn_returns_an_episode_and_spends_with_its_stage(
        self, memory_model: LanguageModel, learning_brief: LearningBrief, budget: TokenBudget
    ) -> None:
        draft = await memory_model.learn(learning_brief, budget)

        assert isinstance(draft, LearningDraft)
        assert draft.episode_title.strip()
        assert draft.episode_body.strip()
        assert all(d.subject.strip() and d.body.strip() for d in draft.dossiers)
        assert budget.usages
        assert {usage.stage for usage in budget.usages} == {STAGE_LEARNING}

    async def test_study_service_calls_tools_and_returns_the_service_dossier(
        self,
        memory_model: LanguageModel,
        study_brief: ServiceStudyBrief,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        draft = await memory_model.study_service(study_brief, toolbox, budget)

        assert isinstance(draft, DossierDraft)
        assert draft.body.strip()
        assert toolbox.evaluations
        assert len(budget.usages) >= 2
        assert {usage.stage for usage in budget.usages} == {STAGE_STUDY}

    async def test_synthesize_returns_the_environment_dossier(
        self, memory_model: LanguageModel, synthesis_brief: SynthesisBrief, budget: TokenBudget
    ) -> None:
        draft = await memory_model.synthesize(synthesis_brief, budget)

        assert isinstance(draft, SynthesisDraft)
        assert draft.environment.body.strip()
        assert all(i.subject.strip() and i.body.strip() for i in draft.integrations)
        assert {usage.stage for usage in budget.usages} == {STAGE_STUDY}

    async def test_exhausted_budget_stops_every_memory_stage(
        self,
        memory_model: LanguageModel,
        learning_brief: LearningBrief,
        study_brief: ServiceStudyBrief,
        synthesis_brief: SynthesisBrief,
        toolbox: Toolbox,
    ) -> None:
        with pytest.raises(BudgetExceededError):
            await memory_model.learn(learning_brief, TokenBudget(limit=1))
        with pytest.raises(BudgetExceededError):
            await memory_model.study_service(study_brief, toolbox, TokenBudget(limit=1))
        with pytest.raises(BudgetExceededError):
            await memory_model.synthesize(synthesis_brief, TokenBudget(limit=1))

    async def test_learning_api_failure_raises_language_model_error(
        self,
        failing_memory_model: LanguageModel,
        learning_brief: LearningBrief,
        synthesis_brief: SynthesisBrief,
        budget: TokenBudget,
    ) -> None:
        with pytest.raises(LanguageModelError):
            await failing_memory_model.learn(learning_brief, budget)
        with pytest.raises(LanguageModelError):
            await failing_memory_model.synthesize(synthesis_brief, budget)

    async def test_study_api_failure_raises_language_model_error(
        self,
        failing_memory_model: LanguageModel,
        study_brief: ServiceStudyBrief,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> None:
        with pytest.raises(LanguageModelError):
            await failing_memory_model.study_service(study_brief, toolbox, budget)


RULE = AlertRule(
    name="Taxa de erro alta",
    condition="taxa de erro > 5% por 5 min",
    severity="critical",
    labels={"service_name": SERVICE},
    service=SERVICE,
    url="https://observabilidade.interno/regras/1",
)


class PostMortemModelContract(LanguageModelFixtures):
    """As duas etapas do post mortem, sem ferramentas: `draft_post_mortem` e
    `regenerate_section`.

    O adapter sobrescreve `post_mortem_model` (as duas respondem um resultado válido) e
    `failing_post_mortem_model` (a API responde erro).
    """

    @pytest.fixture
    def post_mortem_model(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def failing_post_mortem_model(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def post_mortem_brief(self) -> PostMortemBrief:
        incident = make_incident(
            services=(SERVICE,), severity=Severity.P1, state=IncidentState.RESOLVED
        )
        return PostMortemBrief(incident, CONTEXT, current=None, impact=None, now=T0)

    @pytest.fixture
    def regeneration_brief(self, post_mortem_brief: PostMortemBrief) -> PostMortemBrief:
        current = make_post_mortem(post_mortem_brief.incident.id, status=PostMortemStatus.REVIEW)
        return replace(post_mortem_brief, current=current, impact="R$ 3.000,00 estimado")

    async def test_draft_writes_every_section_of_the_model_and_the_action_plan(
        self,
        post_mortem_model: LanguageModel,
        post_mortem_brief: PostMortemBrief,
        budget: TokenBudget,
    ) -> None:
        draft = await post_mortem_model.draft_post_mortem(post_mortem_brief, budget)

        assert isinstance(draft, PostMortemDraft)
        assert set(draft.sections) == set(SectionKey) - DETERMINISTIC
        assert all(body.strip() for body in draft.sections.values())
        assert draft.actions
        assert all(text.strip() and isinstance(kind, ActionKind) for text, kind in draft.actions)
        assert budget.usages
        assert {usage.stage for usage in budget.usages} == {STAGE_POST_MORTEM}

    async def test_regeneration_rewrites_one_section(
        self,
        post_mortem_model: LanguageModel,
        regeneration_brief: PostMortemBrief,
        budget: TokenBudget,
    ) -> None:
        body = await post_mortem_model.regenerate_section(
            regeneration_brief, SectionKey.FACTORS, "incluir a falta de alarme no banco", budget
        )
        plain = await post_mortem_model.regenerate_section(
            regeneration_brief, SectionKey.SUMMARY, None, budget
        )

        assert body.strip()
        assert plain.strip()
        assert {usage.stage for usage in budget.usages} == {STAGE_POST_MORTEM}

    async def test_exhausted_budget_stops_both_stages(
        self,
        post_mortem_model: LanguageModel,
        post_mortem_brief: PostMortemBrief,
        regeneration_brief: PostMortemBrief,
    ) -> None:
        with pytest.raises(BudgetExceededError):
            await post_mortem_model.draft_post_mortem(post_mortem_brief, TokenBudget(limit=1))
        with pytest.raises(BudgetExceededError):
            await post_mortem_model.regenerate_section(
                regeneration_brief, SectionKey.SUMMARY, None, TokenBudget(limit=1)
            )

    async def test_api_failure_raises_language_model_error(
        self,
        failing_post_mortem_model: LanguageModel,
        post_mortem_brief: PostMortemBrief,
        regeneration_brief: PostMortemBrief,
        budget: TokenBudget,
    ) -> None:
        with pytest.raises(LanguageModelError):
            await failing_post_mortem_model.draft_post_mortem(post_mortem_brief, budget)
        with pytest.raises(LanguageModelError):
            await failing_post_mortem_model.regenerate_section(
                regeneration_brief, SectionKey.SUMMARY, None, budget
            )


class TriageModelContract(LanguageModelFixtures):
    """A triagem do incidente novo, sem ferramentas: o mesmo problema que outro aberto e a
    severidade sugerida.

    O adapter sobrescreve `triage_model` (devolve uma triagem válida) e `failing_triage_model`
    (a API responde erro).
    """

    @pytest.fixture
    def triage_model(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def failing_triage_model(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def triage_brief(self) -> TriageBrief:
        incident = make_incident(services=(SERVICE,), severity=Severity.P3)
        other = make_incident(services=("pedidos-api",), severity=Severity.P1)
        summary = "Taxa de erro alta em pedidos-api; alertas: Erro de conexão com o banco"
        return TriageBrief(
            incident,
            (make_alert(service=SERVICE),),
            ((other, summary),),
            MemoryContext(),
            opened_by_default=True,
            now=T0,
        )

    async def test_triage_returns_a_valid_draft(
        self, triage_model: LanguageModel, triage_brief: TriageBrief, budget: TokenBudget
    ) -> None:
        draft = await triage_model.triage(triage_brief, budget)

        assert isinstance(draft, TriageDraft)
        assert draft.same_as is None or draft.same_as in {o.id for o, _ in triage_brief.others}
        assert draft.severity is None or isinstance(draft.severity, Severity)
        assert budget.usages
        assert {usage.stage for usage in budget.usages} == {STAGE_TRIAGE}

    async def test_exhausted_budget_stops_the_triage(
        self, triage_model: LanguageModel, triage_brief: TriageBrief
    ) -> None:
        with pytest.raises(BudgetExceededError):
            await triage_model.triage(triage_brief, TokenBudget(limit=1))

    async def test_api_failure_raises_language_model_error(
        self, failing_triage_model: LanguageModel, triage_brief: TriageBrief, budget: TokenBudget
    ) -> None:
        with pytest.raises(LanguageModelError):
            await failing_triage_model.triage(triage_brief, budget)


CONSOLE_CONTEXT = "Agora: 2026-10-27T10:00:00+00:00\nConversa: nenhuma mensagem anterior"
CONSOLE_QUESTION = f"o {SERVICE} está com erro agora?"


class ConsoleModelContract(LanguageModelFixtures):
    @pytest.fixture
    def consulting_model(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def failing_consultant(self) -> LanguageModel:
        raise NotImplementedError

    async def test_consult_returns_a_text_and_charts_to_draw(
        self, consulting_model: LanguageModel, toolbox: Toolbox, budget: TokenBudget
    ) -> None:
        draft = await consulting_model.consult(CONSOLE_CONTEXT, CONSOLE_QUESTION, toolbox, budget)

        assert isinstance(draft, ConsoleDraft)
        assert draft.text.strip()
        assert isinstance(draft.charts, tuple)
        for chart in draft.charts:
            assert chart.service.strip()
            assert chart.signal in CHART_SIGNALS
            assert isinstance(chart.minutes, int)

    async def test_consult_calls_tools_and_spends_with_its_stage(
        self, consulting_model: LanguageModel, toolbox: Toolbox, budget: TokenBudget
    ) -> None:
        await consulting_model.consult(CONSOLE_CONTEXT, CONSOLE_QUESTION, toolbox, budget)

        assert toolbox.evaluations
        assert len(budget.usages) >= 2
        assert {usage.stage for usage in budget.usages} == {STAGE_CONSOLE}

    async def test_exhausted_budget_stops_the_consult(
        self, consulting_model: LanguageModel, toolbox: Toolbox
    ) -> None:
        with pytest.raises(BudgetExceededError):
            await consulting_model.consult(
                CONSOLE_CONTEXT, CONSOLE_QUESTION, toolbox, TokenBudget(limit=1)
            )

    async def test_consult_api_failure_raises_language_model_error(
        self, failing_consultant: LanguageModel, toolbox: Toolbox, budget: TokenBudget
    ) -> None:
        with pytest.raises(LanguageModelError):
            await failing_consultant.consult(CONSOLE_CONTEXT, CONSOLE_QUESTION, toolbox, budget)


TRANSCRIPT_CONTEXT = (
    f"Incidente: Taxa de erro alta\nServiços: {SERVICE}\nEstado: informado\n"
    "Hipóteses da análise:\n- deploy 20261027.1"
)
TRANSCRIPT = (
    "Pedro: não foi o deploy, o banco caiu às 03:07\n"
    "Ana: vamos fazer o rollback agora, 03:12\n"
    "Ana: o Pedro fica com olhar o pool de conexões"
)


class TranscriptModelContract(LanguageModelFixtures):
    """A extração da transcrição de call, sem ferramentas: decisões, ações, horários,
    participantes e o que a call disse das hipóteses.

    O adapter sobrescreve `transcript_model` (devolve uma extração válida) e
    `failing_transcript_model` (a API responde erro).
    """

    @pytest.fixture
    def transcript_model(self) -> LanguageModel:
        raise NotImplementedError

    @pytest.fixture
    def failing_transcript_model(self) -> LanguageModel:
        raise NotImplementedError

    async def test_extraction_returns_a_valid_draft(
        self, transcript_model: LanguageModel, budget: TokenBudget
    ) -> None:
        draft = await transcript_model.extract_transcript(TRANSCRIPT_CONTEXT, TRANSCRIPT, budget)

        assert isinstance(draft, TranscriptDraft)
        assert draft.items
        for item in draft.items:
            assert isinstance(item.kind, TranscriptItemKind)
            assert item.text.strip()
            assert item.excerpt.strip()
            assert item.owner is None or item.owner.strip()
            assert item.said_at is None or item.said_at.strip()
        assert all(name.strip() for name in draft.participants)
        for check in draft.hypotheses:
            assert check.hypothesis.strip()
            assert isinstance(check.verdict, HypothesisVerdict)
            assert check.excerpt.strip()
        assert budget.usages
        assert {usage.stage for usage in budget.usages} == {STAGE_TRANSCRIPT}

    async def test_exhausted_budget_stops_the_extraction(
        self, transcript_model: LanguageModel
    ) -> None:
        with pytest.raises(BudgetExceededError):
            await transcript_model.extract_transcript(
                TRANSCRIPT_CONTEXT, TRANSCRIPT, TokenBudget(limit=1)
            )

    async def test_api_failure_raises_language_model_error(
        self, failing_transcript_model: LanguageModel, budget: TokenBudget
    ) -> None:
        with pytest.raises(LanguageModelError):
            await failing_transcript_model.extract_transcript(
                TRANSCRIPT_CONTEXT, TRANSCRIPT, budget
            )

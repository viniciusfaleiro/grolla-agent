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

`toolbox`, `budget` e `brief` vêm prontos, sobre os fakes de telemetria e de mudanças.
"""

from datetime import timedelta

import pytest
from grolla.application.investigation import (
    STAGE_INVESTIGATION,
    STAGE_LEARNING,
    STAGE_STUDY,
    STAGE_VERIFICATION,
    STAGE_WARROOM_CHAT,
    AlertRule,
    BudgetExceededError,
    InvalidAnalysisError,
    InvestigationBrief,
    LanguageModel,
    LanguageModelError,
    LearningBrief,
    ServiceStudyBrief,
    SynthesisBrief,
    TokenBudget,
    Toolbox,
)
from grolla.domain.analysis import Analysis, AnalysisDraft, VerificationDraft, check_plan
from grolla.domain.chat import ChatDraft
from grolla.domain.incidents import EventType, IncidentEvent, IncidentState, Severity
from grolla.domain.memory import DossierDraft, LearningDraft, MemoryContext, SynthesisDraft
from grolla.testing.builders import T0, make_alert, make_analysis, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.investigation import FakeChangeHistory, FakeTelemetry

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

"""Suíte de contrato de LanguageModel.

O adapter sobrescreve três fixtures, cada uma o adapter com um roteiro do transporte fake:
- `model`: chama ao menos uma ferramenta pelo toolbox e devolve um resultado válido;
- `failing_model`: a API responde erro (fora do ar, recusa) e o adapter levanta
  LanguageModelError;
- `invalid_model`: a API devolve um resultado fora do schema e o adapter levanta
  InvalidAnalysisError.

E as mesmas três para a validação (`verify`): `verifying_model`, `failing_verifier` e
`invalid_verifier`. A `analysis` validada vem pronta.

`toolbox`, `budget` e `brief` vêm prontos, sobre os fakes de telemetria e de mudanças.
"""

from datetime import timedelta

import pytest
from grolla.application.investigation import (
    STAGE_INVESTIGATION,
    STAGE_VERIFICATION,
    BudgetExceededError,
    InvalidAnalysisError,
    InvestigationBrief,
    LanguageModel,
    LanguageModelError,
    TokenBudget,
    Toolbox,
)
from grolla.domain.analysis import Analysis, AnalysisDraft, VerificationDraft, check_plan
from grolla.domain.incidents import IncidentState, Severity
from grolla.testing.builders import T0, make_alert, make_analysis, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.investigation import FakeChangeHistory, FakeTelemetry

SERVICE = "authorizer-api"


class LanguageModelContract:
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

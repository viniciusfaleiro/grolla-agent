"""Evals da investigação: três incidentes roteirizados contra o modelo de linguagem real.

Cada caso liga o mesmo `Toolbox` da produção a telemetria e histórico de mudanças fake com um
roteiro, roda `ClaudeLanguageModel.investigate` e confere a hipótese principal. Custa dinheiro e
não é determinístico: fica fora do `make check` (`make evals`).

    GROLLA_ANTHROPIC_API_KEY=... uv run python evals/run.py [--config e2e/config.yaml]
"""

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from grolla.adapters.anthropic import ClaudeLanguageModel, ModelSettings
from grolla.application.investigation import (
    STAGE_INVESTIGATION,
    InvestigationBrief,
    LanguageModel,
    TokenBudget,
    Toolbox,
    ToolResult,
)
from grolla.bootstrap import load_team_config
from grolla.domain.analysis import AnalysisDraft
from grolla.domain.incidents import Severity
from grolla.testing.builders import T0, make_alert, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.investigation import FakeChangeHistory, FakeTelemetry

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "e2e" / "config.yaml"
LOOKBACK = timedelta(minutes=60)
MAX_TOKENS = 300_000
# Sem causa clara, a hipótese principal não pode afirmar mais que isso.
UNCLEAR_MAX_CONFIDENCE = 0.5


@dataclass(frozen=True)
class Case:
    name: str
    service: str
    rule: str
    description: str
    telemetry: dict[str, str] = field(default_factory=dict[str, str])
    deploys: str | None = None
    expected: tuple[str, ...] = ()  # termos que a hipótese principal precisa citar


CASES = (
    Case(
        name="deploy ruim",
        service="authorizer-api",
        rule="Taxa de erro alta",
        description="A taxa de erro HTTP 5xx do authorizer-api está em 8% (limite 2%).",
        telemetry={
            "query_metric": "taxa_de_erro subiu de 0,2% para 8% às 10:00, estável antes disso",
            "search_logs": "1.240 NullPointerException em CaptureHandler.parse desde 10:00",
            "find_traces": "erros concentrados em POST /authorize, todos na versão 2.4.1",
        },
        deploys="1 deploy: authorizer-api 2.4.1 às 09:58 por ana (pipeline verde)",
        expected=("deploy", "2.4.1"),
    ),
    Case(
        name="dependência lenta",
        service="checkout-api",
        rule="Latência alta",
        description="O p99 do checkout-api está em 4,2 s (limite 800 ms).",
        telemetry={
            "query_metric": "latencia_p99 subiu de 180 ms para 4,2 s às 10:00",
            "search_logs": "312 'timeout ao chamar antifraude-api' desde 10:00",
            "find_traces": "92% do tempo dos traces lentos em chamadas HTTP para antifraude-api",
        },
        deploys="nenhum deploy nos últimos 3 dias",
        expected=("antifraude",),
    ),
    Case(
        name="sem causa clara",
        service="settlement-worker",
        rule="Vazão baixa",
        description="A vazão do settlement-worker caiu 30% em relação à semana passada.",
        telemetry={
            "query_metric": "vazao 30% abaixo da semana passada, sem degrau: queda gradual",
        },
        deploys="nenhum deploy nos últimos 7 dias",
    ),
)


def toolbox(case: Case) -> Toolbox:
    results = {tool: ToolResult(text, None) for tool, text in case.telemetry.items()}
    deploys = {case.service: case.deploys} if case.deploys else {}
    return Toolbox(FakeTelemetry(results), FakeChangeHistory(deploys), FakeClock(T0), LOOKBACK)


def brief(case: Case) -> InvestigationBrief:
    incident = make_incident(services=(case.service,), severity=Severity.P2)
    alert = make_alert(service=case.service, rule_name=case.rule, description=case.description)
    return InvestigationBrief(incident, (alert,), now=T0)


def grade(case: Case, draft: AnalysisDraft) -> str | None:
    """None se a análise passou; senão, o motivo."""
    if not draft.hypotheses:
        return "nenhuma hipótese"
    top = max(draft.hypotheses, key=lambda h: h.confidence)
    if not case.expected:
        if top.confidence > UNCLEAR_MAX_CONFIDENCE:
            return f"afirmou '{top.title}' com {top.confidence:.0%} sem causa clara"
        return None
    cited = " ".join([top.title, *(e.text for e in top.evidence)]).lower()
    if not any(term.lower() in cited for term in case.expected):
        return f"hipótese principal '{top.title}' não cita {' / '.join(case.expected)}"
    return None


async def run(model: LanguageModel) -> list[tuple[Case, str | None]]:
    outcomes: list[tuple[Case, str | None]] = []
    for case in CASES:
        draft = await model.investigate(brief(case), toolbox(case), TokenBudget(MAX_TOKENS))
        outcomes.append((case, grade(case, draft)))
    return outcomes


def model_from(config: Path) -> ClaudeLanguageModel:
    team = load_team_config(config)
    stage = team.llm.stages[STAGE_INVESTIGATION] if team.llm else None
    key = os.environ.get("GROLLA_ANTHROPIC_API_KEY")
    if stage is None or not key:
        raise SystemExit(f"evals: precisa de GROLLA_ANTHROPIC_API_KEY e llm.stages em {config}")
    settings = ModelSettings(model=stage.model, max_tokens=stage.max_tokens, effort=stage.effort)
    return ClaudeLanguageModel(key, settings)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evals da investigação com o modelo real.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    outcomes = asyncio.run(run(model_from(parser.parse_args().config)))
    for case, failure in outcomes:
        print(f"{'ok  ' if failure is None else 'FALHOU'}  {case.name}  {failure or ''}")  # noqa: T201
    sys.exit(1 if any(failure for _, failure in outcomes) else 0)


if __name__ == "__main__":
    main()

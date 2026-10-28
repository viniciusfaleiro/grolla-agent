"""Evals da investigação: incidentes roteirizados contra o modelo de linguagem real.

Cada caso liga o mesmo `Toolbox` da produção a telemetria e histórico de mudanças fake com um
roteiro, roda `ClaudeLanguageModel.investigate` e confere a hipótese principal. O par "repetido"
roda o mesmo incidente sem e com a memória de um caso passado, para medir o que a memória
acrescenta. Custa dinheiro e não é determinístico: fica fora do `make check` (`make evals`).

    GROLLA_ANTHROPIC_API_KEY=... uv run python evals/run.py [--config e2e/config.yaml]
"""

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from grolla.adapters.anthropic import AnthropicApi, Bedrock, ClaudeLanguageModel, ModelSettings
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
from grolla.domain.memory import (
    AGENT_AUTHOR,
    Confidence,
    Memory,
    MemoryContext,
    MemoryKind,
    Source,
    SourceKind,
    SubjectKind,
)
from grolla.testing.builders import T0, make_alert, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.investigation import FakeChangeHistory, FakeTelemetry
from grolla.testing.memory import InMemoryMemoryStore

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
    memory: MemoryContext = field(default_factory=MemoryContext)
    seen_before: UUID | None = None  # o episódio que a análise precisa reconhecer


PAST_INCIDENT = UUID("5a1e0000-0000-4000-8000-000000000012")
PAST_EPISODE = Memory(
    id=UUID("5a1e0000-0000-4000-8000-0000000000e1"),
    kind=MemoryKind.EPISODE,
    subject="pedidos-api",
    subject_kind=SubjectKind.SERVICE,
    title="pedidos-api com 5xx por pool de conexões esgotado de madrugada",
    body=(
        "Sintomas: taxa de erro de 0,3% para 7% às 03:01, logs 'Connection is not available, "
        "request timed out' no pool do pedidos-db, sem deploy.\n"
        "Causa: o job de conciliação (conciliacao-job) começou às 03:00 e abriu 60 conexões no "
        "pedidos-db, esgotando o limite do banco.\n"
        "O que resolveu: pausar o conciliacao-job; a taxa de erro voltou a 0,3% em 2 min.\n"
        "O que despistou: o p99 alto do banco parecia lentidão do RDS."
    ),
    confidence=Confidence.LEARNED,
    source=Source(SourceKind.INCIDENT, AGENT_AUTHOR, str(PAST_INCIDENT)),
    created_at=T0 - timedelta(days=9),
    updated_at=T0 - timedelta(days=9),
    incident_id=PAST_INCIDENT,
)
# Sem memória, a telemetria mostra o pool esgotado, mas não quem o esgota.
REPEATED = Case(
    name="repetido, sem memória",
    service="pedidos-api",
    rule="Taxa de erro alta",
    description="A taxa de erro HTTP 5xx do pedidos-api está em 6% (limite 2%).",
    telemetry={
        "query_metric": "taxa_de_erro subiu de 0,3% para 6% às 03:02, estável antes disso",
        "search_logs": "880 'Connection is not available, request timed out' (pool do "
        "pedidos-db) desde 03:02",
        "find_traces": "erros em POST /pedidos esperando conexão do pool do pedidos-db",
    },
    deploys="nenhum deploy nos últimos 7 dias",
    expected=("pool", "conex"),
)

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
    # O mesmo incidente sem e com memória: só o caso passado diz que é o job de conciliação.
    REPEATED,
    replace(
        REPEATED,
        name="repetido, com memória",
        expected=("conciliação", "conciliacao"),
        memory=MemoryContext(episodes=(PAST_EPISODE,)),
        seen_before=PAST_INCIDENT,
    ),
)


def toolbox(case: Case) -> Toolbox:
    results = {tool: ToolResult(text, None) for tool, text in case.telemetry.items()}
    deploys = {case.service: case.deploys} if case.deploys else {}
    memory = InMemoryMemoryStore()
    memory.records = {record.id: record for record in case.memory.episodes}
    return Toolbox(
        FakeTelemetry(results),
        FakeChangeHistory(deploys),
        FakeClock(T0),
        LOOKBACK,
        memory=memory,
    )


def brief(case: Case) -> InvestigationBrief:
    incident = make_incident(services=(case.service,), severity=Severity.P2)
    alert = make_alert(service=case.service, rule_name=case.rule, description=case.description)
    return InvestigationBrief(incident, (alert,), now=T0, memory=case.memory)


def grade(case: Case, draft: AnalysisDraft) -> str | None:
    """None se a análise passou; senão, o motivo."""
    seen = draft.seen_before.incident_id if draft.seen_before else None
    if seen != case.seen_before:
        return f"já vi isso: esperava {case.seen_before}, veio {seen}"
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
    if stage is None:
        raise SystemExit(f"evals: precisa de llm.stages em {config}")
    settings = ModelSettings(model=stage.model, max_tokens=stage.max_tokens, effort=stage.effort)
    if team.llm and team.llm.provider == "bedrock" and team.llm.region:
        return ClaudeLanguageModel(Bedrock(team.llm.region), settings)
    key = os.environ.get("GROLLA_ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("evals: precisa de GROLLA_ANTHROPIC_API_KEY com llm.provider anthropic")
    return ClaudeLanguageModel(AnthropicApi(key), settings)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evals da investigação com o modelo real.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    outcomes = asyncio.run(run(model_from(parser.parse_args().config)))
    for case, failure in outcomes:
        print(f"{'ok  ' if failure is None else 'FALHOU'}  {case.name}  {failure or ''}")  # noqa: T201
    sys.exit(1 if any(failure for _, failure in outcomes) else 0)


if __name__ == "__main__":
    main()

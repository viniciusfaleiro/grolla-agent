"""Respostas do agente no chat: contexto, ferramentas, limites e a garantia de que chegam."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from grolla.application.analysis import AnalysisPolicy
from grolla.application.chat import (
    CHAT_LIMIT_REACHED,
    chat_context,
    reap_stuck_replies,
    run_next_reply,
)
from grolla.application.investigation import (
    STAGE_INVESTIGATION,
    STAGE_WARROOM_CHAT,
    InvalidAnalysisError,
    LanguageModelError,
)
from grolla.application.war_room import ask
from grolla.domain.analysis import AnalysisStatus, Command
from grolla.domain.chat import AGENT_AUTHOR, ChatDraft, ChatMessage, ReplyStatus
from grolla.domain.incidents import EventType, IncidentEvent, IncidentState, Severity
from grolla.domain.safety import REFUSALS, Scope
from grolla.testing.builders import (
    T0,
    make_alert,
    make_analysis,
    make_incident,
    make_verification,
)
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryChatQueue, InMemoryIncidentRepository
from grolla.testing.investigation import (
    FakeChangeHistory,
    FakeClusterInventory,
    FakeTelemetry,
    ScriptedLanguageModel,
    fake_usage,
)

SERVICE = "pedidos-api"
POLICY = AnalysisPolicy(
    timeout=timedelta(minutes=5),
    max_tokens=100_000,
    lookback=timedelta(minutes=60),
    page_url=lambda incident_id: f"https://grolla.interno/incidents/{incident_id}",
    telemetry_url=None,
    chat_timeout=timedelta(minutes=2),
    chat_max_tokens=60_000,
    chat_max_tokens_per_incident=400_000,
)
TOOLS = [("search_logs", {"service": "pedidos-db", "text": "connection"})]
COMMANDS = (
    Command(
        "kubectl scale deployment antifraude-api --replicas=2 -n loja",
        "cluster lab-k3s, namespace loja",
    ),
    Command(
        "kubectl rollout status deployment/antifraude-api -n loja",
        "cluster lab-k3s, namespace loja",
    ),
)


class World:
    def __init__(self) -> None:
        self.repository = InMemoryIncidentRepository()
        self.queue = InMemoryChatQueue(self.repository)
        self.clock = FakeClock(T0)
        self.telemetry = FakeTelemetry()
        self.cluster = FakeClusterInventory()

    async def ask(self, text: str = "o rollback resolveu?") -> UUID:
        incident = replace(
            make_incident(services=(SERVICE,), state=IncidentState.HANDLING),
            commander="Ana",
        )
        alert = make_alert(service=SERVICE)
        async with self.repository.transaction() as uow:
            await uow.save(incident)
            await uow.link_alert(alert, incident.id)
        await ask(self.repository, incident.id, "Ana", text, self.clock)
        return incident.id

    async def run(self, model: ScriptedLanguageModel, policy: AnalysisPolicy = POLICY) -> bool:
        return await run_next_reply(
            queue=self.queue,
            repository=self.repository,
            model=model,
            telemetry=self.telemetry,
            changes=FakeChangeHistory(),
            clock=self.clock,
            policy=policy,
            cluster=self.cluster,
        )

    async def reply(self, incident_id: UUID) -> tuple[ChatMessage, ChatMessage]:
        question, answer = await self.repository.chat_of(incident_id)
        return question, answer


@pytest.fixture
def world() -> World:
    return World()


async def test_nothing_to_do_without_questions(world: World) -> None:
    assert not await world.run(ScriptedLanguageModel())


async def test_answer_arrives_with_the_queries_made(world: World) -> None:
    incident_id = await world.ask("olha os logs do pedidos-db")
    model = ScriptedLanguageModel(chat=ChatDraft("O banco voltou às 21:55."), chat_calls=TOOLS)

    assert await world.run(model)

    question, answer = await world.reply(incident_id)
    assert question.reply_status is ReplyStatus.ANSWERED
    assert answer.author == AGENT_AUTHOR
    assert answer.text == "O banco voltou às 21:55."
    assert answer.reply_to == question.id
    assert [e.tool for e in answer.evaluations] == ["search_logs"]
    assert world.telemetry.calls[0][1]["service"] == "pedidos-db"
    [(context, asked)] = model.questions
    assert asked == "olha os logs do pedidos-db"
    assert f"Serviços: {SERVICE}" in context.splitlines()
    assert "Ana: olha os logs do pedidos-db" in context
    agent = (await world.repository.events_of(incident_id))[-1]
    assert agent == IncidentEvent(
        incident_id,
        T0,
        EventType.AGENT_MESSAGE,
        {"reply_to": question.id, "ok": True, "text": "O banco voltou às 21:55."},
        AGENT_AUTHOR,
    )
    usages = [usage for _, usage, _ in world.repository.usages]
    assert {u.stage for u in usages} == {STAGE_WARROOM_CHAT}
    assert len(usages) == 2
    assert not await world.run(model)


@pytest.mark.parametrize(
    ("error", "text"),
    [
        (LanguageModelError("API fora do ar"), "não consegui responder: LLM indisponível"),
        (InvalidAnalysisError("vazio"), "não consegui responder: resultado inválido"),
    ],
)
async def test_failure_becomes_a_reply_keeping_the_queries(
    world: World, error: Exception, text: str
) -> None:
    incident_id = await world.ask()

    assert await world.run(ScriptedLanguageModel(chat_calls=TOOLS, chat_error=error))

    question, answer = await world.reply(incident_id)
    assert question.reply_status is ReplyStatus.FAILED
    assert answer.text == text
    assert len(answer.evaluations) == 1
    agent = (await world.repository.events_of(incident_id))[-1]
    assert agent.data["ok"] is False


async def test_slow_model_ends_by_timeout(world: World) -> None:
    incident_id = await world.ask()
    policy = replace(POLICY, chat_timeout=timedelta(milliseconds=10))

    await world.run(ScriptedLanguageModel(chat_delay=5), policy)

    _, answer = await world.reply(incident_id)
    assert answer.text == "não consegui responder: tempo esgotado"


async def test_exhausted_budget_ends_the_reply(world: World) -> None:
    incident_id = await world.ask()
    policy = replace(POLICY, chat_max_tokens=2_000)

    await world.run(ScriptedLanguageModel(chat_calls=TOOLS * 3), policy)

    _, answer = await world.reply(incident_id)
    assert answer.text == "não consegui responder: orçamento de tokens esgotado"
    assert len(world.repository.usages) == 2  # o uso gasto até estourar fica registrado


async def test_chat_ceiling_answers_without_calling_the_model(world: World) -> None:
    incident_id = await world.ask()
    spent = [
        (incident_id, fake_usage(399_000, STAGE_WARROOM_CHAT), T0),
        (incident_id, fake_usage(900_000, STAGE_INVESTIGATION), T0),  # outra etapa não conta
    ]
    world.repository.usages.extend(spent)
    model = ScriptedLanguageModel()

    await world.run(model)

    question, answer = await world.reply(incident_id)
    assert answer.text == CHAT_LIMIT_REACHED
    assert question.reply_status is ReplyStatus.FAILED
    assert model.questions == []


async def test_below_the_ceiling_the_model_is_called(world: World) -> None:
    incident_id = await world.ask()
    world.repository.usages.append((incident_id, fake_usage(1_000, STAGE_WARROOM_CHAT), T0))
    model = ScriptedLanguageModel()

    await world.run(model)

    assert len(model.questions) == 1


async def test_stuck_question_is_answered_as_interrupted(world: World) -> None:
    incident_id = await world.ask()
    job = await world.queue.claim(T0)
    assert job is not None

    world.clock.advance(timedelta(minutes=3))
    assert await reap_stuck_replies(world.queue, world.repository, world.clock, POLICY) == 0
    world.clock.advance(timedelta(minutes=2))
    assert await reap_stuck_replies(world.queue, world.repository, world.clock, POLICY) == 1

    question, answer = await world.reply(incident_id)
    assert question.reply_status is ReplyStatus.FAILED
    assert answer.text == "não consegui responder: interrompida"
    assert answer.evaluations == ()

    # a resposta do worker que voltava não é gravada de novo
    await world.queue.answer(job, answer, [], [], world.clock.now())
    assert len(await world.repository.chat_of(incident_id)) == 2


def test_context_has_incident_analysis_verification_timeline_and_chat() -> None:
    incident = replace(
        make_incident(services=("pedidos-api", "pedidos-db"), severity=Severity.P1),
        commander="Ana",
    )
    events = [
        IncidentEvent(
            incident.id,
            T0 + timedelta(seconds=n),
            EventType.ACTION_RECORDED,
            {"text": f"ação {n}"},
            "Bruno",
        )
        for n in range(35)
    ]
    messages = [
        ChatMessage(n, incident.id, "Ana", f"mensagem {n}", T0 + timedelta(seconds=n))
        for n in range(25)
    ]

    context = chat_context(
        incident,
        make_analysis(incident.id),
        make_verification(incident.id),
        events,
        messages,
    )

    lines = context.splitlines()
    assert "Serviços: pedidos-api, pedidos-db" in lines
    assert "IC: Ana" in lines
    assert "Severidade: P1" in lines
    assert "- Hipótese: Hipótese 1 (confiança 80%)" in lines
    assert "Causa raiz: Deploy 2.4.1 introduziu erro em POST /capturas às 10:02" in lines
    assert any(line.startswith("- Veredito: Hipótese 1: confirmada") for line in lines)
    assert any(line.startswith("- Plano: Fazer rollback") for line in lines)
    assert "text=ação 4" not in context
    assert "- 10:00:34 Bruno: action_recorded text=ação 34" in lines
    assert "mensagem 4" not in context
    assert "- 10:00:24 Ana: mensagem 24" in lines


def test_context_without_analysis_validation_or_messages() -> None:
    incident = make_incident(services=(SERVICE,))

    context = chat_context(incident, None, None, [], [])

    lines = context.splitlines()
    assert "IC: ninguém assumiu" in lines
    assert "Análise inicial: não pedida" in lines
    assert "Validação: não pedida" in lines
    assert "- nenhuma mensagem" in lines


def test_context_shows_a_running_validation_and_what_was_missing() -> None:
    incident = make_incident(services=(SERVICE,))
    running = make_verification(incident.id, status=AnalysisStatus.RUNNING)
    partial = make_verification(incident.id, status=AnalysisStatus.PARTIAL)

    assert "Validação (em_andamento)" in chat_context(incident, None, running, [], [])
    assert "Faltou: Validação parcial: tempo esgotado." in chat_context(
        incident, None, partial, [], []
    )


async def test_reply_can_look_at_the_cluster(world: World) -> None:
    incident_id = await world.ask("mudou algo no cluster?")
    model = ScriptedLanguageModel(chat_calls=[("cluster_changes", {"service": SERVICE})])

    assert await world.run(model)

    _, answer = await world.reply(incident_id)
    assert [e.tool for e in answer.evaluations] == ["cluster_changes"]
    assert world.cluster.calls == [("changes", SERVICE)]


@pytest.mark.parametrize("scope", [Scope.OFF_TOPIC, Scope.EXECUTION])
async def test_request_out_of_the_domain_gets_the_fixed_refusal(world: World, scope: Scope) -> None:
    incident_id = await world.ask("reinicia o deployment da estoque-api pra mim")
    model = ScriptedLanguageModel(
        chat=ChatDraft("Pronto, reiniciei: kubectl delete pod x", scope), chat_calls=TOOLS
    )

    assert await world.run(model)

    question, answer = await world.reply(incident_id)
    assert question.reply_status is ReplyStatus.ANSWERED
    assert (answer.text, answer.refusal, answer.evaluations) == (REFUSALS[scope], scope, ())
    agent = (await world.repository.events_of(incident_id))[-1]
    assert agent.data["text"] == REFUSALS[scope]
    assert {u.stage for _, u, _ in world.repository.usages} == {STAGE_WARROOM_CHAT}


async def test_request_in_the_domain_is_not_a_refusal(world: World) -> None:
    incident_id = await world.ask("qual o comando para voltar ao normal agora?")
    draft = ChatDraft("kubectl rollout undo deployment/pedidos-api", Scope.DOMAIN)

    assert await world.run(ScriptedLanguageModel(chat=draft))

    _, answer = await world.reply(incident_id)
    assert (answer.text, answer.refusal) == (draft.text, None)


async def test_reply_in_the_domain_keeps_the_commands_in_order(world: World) -> None:
    incident_id = await world.ask("quais comandos eu posso executar pra voltar ao normal e aonde?")
    draft = ChatDraft("Volte as réplicas do antifraude-api.", commands=COMMANDS)

    assert await world.run(ScriptedLanguageModel(chat=draft))

    _, answer = await world.reply(incident_id)
    assert (answer.text, answer.commands) == (draft.text, COMMANDS)


@pytest.mark.parametrize("scope", [Scope.OFF_TOPIC, Scope.EXECUTION])
async def test_refused_reply_never_has_commands(world: World, scope: Scope) -> None:
    incident_id = await world.ask("escala o antifraude-api pra mim")
    draft = ChatDraft("Pronto.", scope, commands=COMMANDS)

    assert await world.run(ScriptedLanguageModel(chat=draft))

    _, answer = await world.reply(incident_id)
    assert (answer.refusal, answer.commands) == (scope, ())


async def test_failed_reply_never_has_commands(world: World) -> None:
    incident_id = await world.ask()
    model = ScriptedLanguageModel(
        chat=ChatDraft("x", commands=COMMANDS), chat_error=LanguageModelError("fora")
    )

    assert await world.run(model)

    question, answer = await world.reply(incident_id)
    assert (question.reply_status, answer.commands) == (ReplyStatus.FAILED, ())

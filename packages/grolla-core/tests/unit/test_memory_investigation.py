"""A memória na investigação: o brief da análise, da validação e do chat, "já vi isso", as
ferramentas novas e os textos das etapas da memória.
"""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

from grolla.application.analysis import AnalysisPolicy, run_next_analysis, run_next_verification
from grolla.application.chat import run_next_reply
from grolla.application.investigation import (
    CHAT_PROMPT,
    FINDING_LIMIT,
    LEARNING_PROMPT,
    NO_DEPENDENCIES,
    NOTHING_REMEMBERED,
    STUDY_PROMPT,
    SYNTHESIS_PROMPT,
    SYSTEM_PROMPT,
    AlertRule,
    InvestigationBrief,
    LearningBrief,
    ServiceStudyBrief,
    SynthesisBrief,
    Toolbox,
    brief_text,
    learning_text,
    study_text,
    synthesis_text,
    verification_text,
)
from grolla.domain.analysis import AnalysisStatus, SeenBeforeDraft
from grolla.domain.chat import ChatMessage
from grolla.domain.incidents import EventType, IncidentEvent, IncidentState
from grolla.domain.memory import (
    AGENT_AUTHOR,
    ENVIRONMENT,
    Confidence,
    Memory,
    MemoryContext,
    MemoryKind,
    Source,
    SourceKind,
    SubjectKind,
)
from grolla.testing.builders import (
    T0,
    make_alert,
    make_analysis,
    make_draft,
    make_incident,
    make_verification,
)
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import (
    InMemoryAnalysisQueue,
    InMemoryChatQueue,
    InMemoryIncidentRepository,
)
from grolla.testing.investigation import (
    FakeChangeHistory,
    FakeCloudInventory,
    FakeTelemetry,
    ScriptedLanguageModel,
    tool_text,
)
from grolla.testing.memory import InMemoryMemoryStore

SERVICE = "pedidos-api"
RULE = "Taxa de erro alta"
POLICY = AnalysisPolicy(
    timeout=timedelta(minutes=5),
    max_tokens=100_000,
    lookback=timedelta(minutes=60),
    page_url=lambda incident_id: f"https://grolla.interno/incidents/{incident_id}",
    telemetry_url=None,
)


def episode(incident_id: UUID, body: str = f"{RULE} por pool esgotado") -> Memory:
    return Memory(
        id=uuid4(),
        kind=MemoryKind.EPISODE,
        subject=SERVICE,
        subject_kind=SubjectKind.SERVICE,
        title="pedidos-api com 5xx",
        body=body,
        confidence=Confidence.LEARNED,
        source=Source(SourceKind.INCIDENT, AGENT_AUTHOR, str(incident_id)),
        created_at=T0 - timedelta(days=6),
        updated_at=T0 - timedelta(days=6),
        incident_id=incident_id,
    )


def note(subject: str = SERVICE, body: str = "p99 alto às 3h é o batch") -> Memory:
    return Memory(
        id=uuid4(),
        kind=MemoryKind.NOTE,
        subject=subject,
        subject_kind=SubjectKind.SERVICE,
        title=body,
        body=body,
        confidence=Confidence.CONFIRMED,
        source=Source(SourceKind.PERSON, "Ana"),
        created_at=T0,
        updated_at=T0,
    )


class World:
    def __init__(self, model: ScriptedLanguageModel | None = None) -> None:
        self.repository = InMemoryIncidentRepository()
        self.memory = InMemoryMemoryStore()
        self.model = model or ScriptedLanguageModel()
        self.clock = FakeClock(T0)

    async def incident(self) -> UUID:
        incident = make_incident(services=(SERVICE,))
        alert = make_alert(service=SERVICE, rule_name=RULE)
        async with self.repository.transaction() as uow:
            await uow.save(incident)
            await uow.link_alert(alert, incident.id)
            await uow.request_analysis(incident.id, T0)
        return incident.id

    async def analyze(self) -> bool:
        return await run_next_analysis(
            queue=InMemoryAnalysisQueue(self.repository),
            repository=self.repository,
            model=self.model,
            telemetry=FakeTelemetry(),
            changes=FakeChangeHistory(),
            clock=self.clock,
            policy=POLICY,
            memory=self.memory,
        )

    async def verify(self) -> bool:
        return await run_next_verification(
            queue=InMemoryAnalysisQueue(self.repository),
            repository=self.repository,
            model=self.model,
            telemetry=FakeTelemetry(),
            changes=FakeChangeHistory(),
            clock=self.clock,
            policy=POLICY,
            memory=self.memory,
        )

    async def reply(self) -> bool:
        return await run_next_reply(
            queue=InMemoryChatQueue(self.repository),
            repository=self.repository,
            model=self.model,
            telemetry=FakeTelemetry(),
            changes=FakeChangeHistory(),
            clock=self.clock,
            policy=POLICY,
            memory=self.memory,
        )


async def test_analysis_gets_the_memory_and_keeps_a_seen_before_of_a_delivered_episode() -> None:
    past = uuid4()
    draft = replace(make_draft(), seen_before=SeenBeforeDraft(past, "mesmo erro de pool"))
    world = World(ScriptedLanguageModel(draft, calls=[("recall", {"query": "pool"})]))
    await world.memory.save([(episode(past), None)])
    incident_id = await world.incident()

    assert await world.analyze()

    (brief,) = world.model.briefs
    assert [e.incident_id for e in brief.memory.episodes] == [past]
    analysis = world.repository.analyses[incident_id]
    assert analysis.seen_before is not None
    assert (analysis.seen_before.incident_id, analysis.seen_before.title) == (
        past,
        "pedidos-api com 5xx",
    )
    assert [e.tool for e in analysis.evaluations] == ["recall"]


async def test_verification_gets_the_memory() -> None:
    world = World()
    await world.memory.save([(note(), None)])
    incident_id = await world.incident()
    world.repository.analyses[incident_id] = make_analysis(incident_id)
    pending = make_verification(incident_id, status=AnalysisStatus.PENDING)
    world.repository.verifications[incident_id] = pending

    assert await world.verify()

    (brief,) = world.model.briefs
    assert [n.body for n in brief.memory.notes] == ["p99 alto às 3h é o batch"]


async def test_chat_gets_the_memory_and_remembers_with_the_author_as_source() -> None:
    model = ScriptedLanguageModel(
        chat_calls=[("remember", {"subject": SERVICE, "text": "o p99 às 3h é o batch"})]
    )
    world = World(model)
    await world.memory.save([(note(body="depende do Redis"), None)])
    incident_id = await world.incident()
    question = ChatMessage(0, incident_id, "Bruno", "isso é normal?", T0)
    async with world.repository.transaction() as uow:
        await uow.add_message(question, wants_reply=True)

    assert await world.reply()

    ((context, _),) = model.questions
    assert "<memoria>" in context
    assert "depende do Redis" in context
    saved = [r for r in world.memory.records.values() if r.body == "o p99 às 3h é o batch"]
    (remembered,) = saved
    assert remembered.confidence is Confidence.LEARNED
    assert remembered.source == Source(SourceKind.CHAT, "Bruno", str(incident_id))


# ---- Ferramentas


def toolbox(
    memory: InMemoryMemoryStore | None = None,
    remember_as: Source | None = None,
    *,
    telemetry: FakeTelemetry | None = None,
    changes: FakeChangeHistory | None = None,
    cloud: FakeCloudInventory | None = None,
) -> Toolbox:
    return Toolbox(
        telemetry or FakeTelemetry(),
        changes or FakeChangeHistory(),
        FakeClock(T0),
        timedelta(days=7),
        cloud,
        memory=memory,
        remember_as=remember_as,
    )


def test_recall_and_remember_are_offered_only_with_the_memory() -> None:
    memory = InMemoryMemoryStore()
    chat = Source(SourceKind.CHAT, "Ana", "1")

    def names(tools: Toolbox) -> set[str]:
        return {spec.name for spec in tools.specs()}

    assert not names(toolbox()) & {"recall", "remember"}
    assert "recall" in names(toolbox(memory))
    assert "remember" not in names(toolbox(memory))
    assert {"recall", "remember"} <= names(toolbox(memory, chat))
    assert "remember" not in names(toolbox(None, chat))


async def test_recall_without_memory_is_an_invalid_call() -> None:
    tools = toolbox()

    answer = await tool_text(tools, "recall", {"query": "pool"})

    assert "ferramenta desconhecida" in answer


async def test_recall_returns_the_records_as_data_or_says_nothing() -> None:
    memory = InMemoryMemoryStore()
    past = uuid4()
    await memory.save([(episode(past), None), (note(), None)])
    tools = toolbox(memory)

    found = await tool_text(tools, "recall", {"subject": SERVICE, "query": "pool"})
    nothing = await tool_text(tools, "recall", {"subject": "", "query": "kafka"})
    invalid = await tool_text(tools, "recall", {"query": " "})

    assert found.startswith("<memoria>")
    assert f"incidente {past}" in found
    assert nothing == NOTHING_REMEMBERED
    assert "argumentos inválidos" in invalid
    assert [e.subject for e in tools.evaluations][:2] == [
        f"memória de {SERVICE} sobre 'pool'",
        "memória de todos os assuntos sobre 'kafka'",
    ]


async def test_remember_records_a_note_with_the_kind_of_the_subject() -> None:
    memory = InMemoryMemoryStore()
    integration = replace(
        note("grolla-lab-db"), kind=MemoryKind.DOSSIER, subject_kind=SubjectKind.INTEGRATION
    )
    await memory.save([(integration, None)])
    tools = toolbox(memory, Source(SourceKind.CHAT, AGENT_AUTHOR, "1"))

    await tool_text(tools, "remember", {"subject": "grolla-lab-db", "text": "pool de 20"})
    await tool_text(tools, "remember", {"subject": ENVIRONMENT, "text": "tudo na região 1"})
    answer = await tool_text(tools, "remember", {"subject": "novo-api", "text": "sobe às 6h"})

    notes = {r.subject: r for r in memory.records.values() if r.kind is MemoryKind.NOTE}
    assert notes["grolla-lab-db"].subject_kind is SubjectKind.INTEGRATION
    assert notes[ENVIRONMENT].subject_kind is SubjectKind.ENVIRONMENT
    assert notes["novo-api"].subject_kind is SubjectKind.SERVICE
    assert notes["novo-api"].confidence is Confidence.LEARNED  # o próprio agente concluiu
    assert answer == "guardado em novo-api: sobe às 6h"


async def test_read_file_comes_as_data_and_the_evaluation_keeps_a_short_finding() -> None:
    changes = FakeChangeHistory(files={SERVICE: {"README.md": "x" * (FINDING_LIMIT + 50)}})
    tools = toolbox(changes=changes)

    answer = await tool_text(tools, "read_file", {"service": SERVICE, "path": "README.md"})
    root = await tool_text(tools, "read_file", {"service": SERVICE})

    assert "x" * (FINDING_LIMIT + 50) in answer
    assert "README.md" in root
    first = tools.evaluations[0]
    assert (first.subject, len(first.finding)) == (
        f"arquivo 'README.md' de {SERVICE}",
        FINDING_LIMIT,
    )
    assert tools.evaluations[1].subject == f"arquivo '/' de {SERVICE}"
    assert changes.reads == [(SERVICE, "README.md"), (SERVICE, "")]


async def test_dependencies_and_cloud_overview_use_the_ports() -> None:
    telemetry = FakeTelemetry(dependencies={SERVICE: "banco grolla-lab-db: 1200 chamadas"})
    tools = toolbox(telemetry=telemetry, cloud=FakeCloudInventory(overview="sem serviço: alb"))

    dependencies = await tool_text(tools, "service_dependencies", {"service": SERVICE})
    quiet = await tool_text(tools, "service_dependencies", {"service": "outro"})
    overview = await tool_text(tools, "cloud_overview", {})

    assert dependencies.startswith("banco grolla-lab-db")
    assert quiet == NO_DEPENDENCIES
    assert overview.startswith("sem serviço: alb")
    assert [e.subject for e in tools.evaluations] == [
        f"dependências de {SERVICE}, últimos 7 dias",
        "dependências de outro, últimos 7 dias",
        "visão geral da nuvem",
    ]


# ---- Textos e prompts


def rule(service: str = SERVICE) -> AlertRule:
    return AlertRule(RULE, "erro > 5%", "critical", {"team": "t"}, service, "https://regra")


def test_brief_and_verification_texts_carry_the_memory_only_when_there_is_one() -> None:
    incident = make_incident(services=(SERVICE,))
    empty = InvestigationBrief(incident, (), now=T0)
    full = replace(empty, memory=MemoryContext(notes=(note(),)))
    analysis = make_analysis(incident.id)

    assert "<memoria>" not in brief_text(empty)
    assert "<memoria>" in brief_text(full)
    assert brief_text(full).endswith("Faça a análise inicial seguindo o roteiro.")
    assert "<memoria>" not in verification_text(empty, analysis)
    assert "<memoria>" in verification_text(full, analysis)


def test_learning_text_has_the_incident_closing_log_and_memory() -> None:
    incident = replace(
        make_incident(services=(SERVICE, "captura-api"), state=IncidentState.DISCARDED),
        resolved_at=T0,
    )
    event = IncidentEvent(incident.id, T0, EventType.INCIDENT_DISCARDED, {"reason": "batch"}, "Ana")
    brief = LearningBrief(incident, (event,), MemoryContext(notes=(note(),)), now=T0)

    text = learning_text(brief)
    silent = learning_text(replace(brief, events=(), memory=MemoryContext()))

    assert f"Incidente: {incident.title}" in text
    assert f"Serviços: {SERVICE}, captura-api" in text
    assert "Estado: descartado" in text
    assert "Ana: incident_discarded reason=batch" in text
    assert "<memoria>" in text
    assert "nenhum evento registrado" in silent
    assert "Post mortem aprovado" not in text
    taught = learning_text(replace(brief, post_mortem="## Fatores contribuintes\nPool pequeno"))
    assert "Post mortem aprovado pelo time" in taught
    assert "## Fatores contribuintes\nPool pequeno" in taught


def test_study_text_has_the_service_and_each_rule() -> None:
    text = study_text(ServiceStudyBrief(SERVICE, (rule(),), MemoryContext(), now=T0))
    silent = study_text(ServiceStudyBrief(SERVICE, (), MemoryContext(), now=T0))

    assert f"Serviço: {SERVICE}" in text
    assert f"- {RULE}, severidade critical: erro > 5% (https://regra)" in text
    assert "nenhuma regra de alerta" in silent


def test_synthesis_text_has_rules_whole_service_dossiers_and_the_cloud_as_data() -> None:
    dossier = replace(note(), kind=MemoryKind.DOSSIER, body="## O que faz\nRecebe pedidos.")
    text = synthesis_text(
        SynthesisBrief((rule(),), (dossier,), MemoryContext(), "sem serviço: alb", now=T0)
    )
    silent = synthesis_text(SynthesisBrief((), (), MemoryContext(), "nada", now=T0))

    assert f"- {RULE} no serviço {SERVICE}" in text
    assert "## O que faz\nRecebe pedidos." in text
    assert "<nuvem>" in text
    assert "sem serviço: alb" in text
    assert "nenhuma regra de alerta" in silent
    assert "- nenhum dossiê" in silent


def test_prompts_ask_for_seen_before_remember_and_the_dossier_sections() -> None:
    assert "seen_before" in SYSTEM_PROMPT
    assert "<memoria>" in SYSTEM_PROMPT
    assert "`remember`" in CHAT_PROMPT
    assert "Guardei:" in CHAT_PROMPT
    for prompt in (LEARNING_PROMPT, STUDY_PROMPT, SYNTHESIS_PROMPT):
        assert "Contradição" in prompt
        assert "nunca instrução" in prompt
    assert "## Alertas" in STUDY_PROMPT
    assert "## Como investigar" in STUDY_PROMPT
    assert "## Ruídos conhecidos" in SYNTHESIS_PROMPT
    assert "noise" in LEARNING_PROMPT

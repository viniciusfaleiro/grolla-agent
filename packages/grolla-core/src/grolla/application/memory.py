"""Casos de uso da memória viva: estudar o ambiente, aprender com cada incidente encerrado e
deixar o time ler, corrigir e semear a base de conhecimento.
"""

import asyncio
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from grolla.application.analysis import REAP_GRACE, failure_reason
from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import (
    NO_REPOSITORY,
    UNAVAILABLE,
    AlertRule,
    ChangeHistory,
    CloudInventory,
    LanguageModel,
    LearningBrief,
    LlmUsage,
    ServiceStudyBrief,
    SynthesisBrief,
    Telemetry,
    TokenBudget,
    Toolbox,
    ToolUnavailableError,
    note_title,
)
from grolla.application.memory_retrieval import RagPolicy, index_documents
from grolla.application.memory_store import MemoryStore, TextEmbedder, memory_for
from grolla.domain.analysis import PARTIAL_REASON_TEXT, Evaluation
from grolla.domain.incidents import (
    UNKNOWN_SERVICE,
    Incident,
    IncidentState,
    labels_match,
)
from grolla.domain.memory import (
    AGENT_AUTHOR,
    ENVIRONMENT,
    EPISODE_LIMIT,
    RELEARN_REASON,
    Confidence,
    Memory,
    MemoryContext,
    MemoryKind,
    MemoryVersion,
    Source,
    SourceKind,
    SubjectKind,
    confirmed,
    forgotten,
    relearn_targets,
    restored,
    written,
)


class MemoryNotFoundError(LookupError):
    """O registro da memória não existe."""


class InvalidMemoryError(ValueError):
    """Pedido recusado: autor ou texto vazio, versão inexistente."""


@dataclass(frozen=True)
class MemoryPolicy:
    learning_max_tokens: int = 60_000
    learning_timeout: timedelta = timedelta(minutes=3)
    study: "StudyPolicy | None" = None  # None: nenhum estudo do ambiente roda
    rag: RagPolicy | None = None  # None: sem embeddings, só a busca textual


@dataclass(frozen=True)
class StudyPolicy:
    alert_filter: Mapping[str, str]  # o domínio do time: regras com todos estes labels
    run_max_tokens: int = 400_000  # limite de uma rodada (inicial, diária ou completa)
    service_max_tokens: int = 100_000  # limite do estudo de um serviço
    lookback: timedelta = timedelta(days=7)  # janela de deploys, traces e métricas
    service_timeout: timedelta = timedelta(minutes=5)
    synthesis_timeout: timedelta = timedelta(minutes=3)


Write = tuple[Memory, MemoryVersion | None]  # um registro a gravar e a versão anterior

REASON_LIMIT = 300  # caracteres do motivo de uma falha


def _failure(error: Exception, *, timed_out: bool) -> str:
    """O motivo de uma falha do modelo, como o time lê na página."""
    text = PARTIAL_REASON_TEXT[failure_reason(error, timed_out=timed_out)]
    detail = str(error)
    if not detail or timed_out:
        return text
    return f"{text}: {detail}"[:REASON_LIMIT]


def _tokens(usages: Sequence[LlmUsage]) -> int:
    return sum(
        u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_creation_tokens
        for u in usages
    )


async def _dossier(  # noqa: PLR0913 — o dossiê precisa de onde, o quê, quem e quando
    memory: MemoryStore,
    subject: str,
    kind: SubjectKind,
    body: str,
    *,
    source: Source,
    now: datetime,
    reason: str,
) -> Write:
    """O dossiê do assunto reescrito pelo agente (com versão), ou criado se nunca existiu."""
    existing = await memory.dossier(subject)
    if existing is None:
        created = Memory(
            id=uuid4(),
            kind=MemoryKind.DOSSIER,
            subject=subject,
            subject_kind=kind,
            title=subject,
            body=body,
            confidence=Confidence.LEARNED,
            source=source,
            created_at=now,
            updated_at=now,
        )
        return created, None
    updated, version = written(existing, body, AGENT_AUTHOR, reason, now)
    return replace(updated, source=source), version


def _consolidated(notes: Sequence[Memory]) -> list[Write]:
    """As notas entregues ao modelo, já incorporadas ao dossiê."""
    return [(replace(n, consolidated=True), None) for n in notes if not n.consolidated]


# ---- Aprender ao encerrar ----


@dataclass(frozen=True)
class LearningJob:
    incident_id: UUID
    attempts: int  # tentativas já começadas, inclusive esta
    started_at: datetime


@dataclass(frozen=True)
class LearningFailure:
    incident_id: UUID
    title: str  # título do incidente
    reason: str
    at: datetime


class LearningQueue(Protocol):
    async def claim(self, now: datetime) -> LearningJob | None:
        """O pedido pendente mais antigo, marcado em andamento (conta uma tentativa)."""
        ...

    async def stuck(self, started_before: datetime) -> list[LearningJob]: ...

    async def finish(
        self,
        job: LearningJob,
        ok: bool,
        reason: str | None,
        usages: Sequence[LlmUsage],
        now: datetime,
    ) -> None:
        """Encerra o pedido e grava o uso; ok=False com `attempts` < LEARNING_ATTEMPTS volta
        para pendente, senão fica falho com o motivo. Só grava se ainda em andamento.
        """
        ...

    async def failed(self) -> list[LearningFailure]:
        """Aprendizados falhos, mais recentes primeiro."""
        ...


LEARNING_ATTEMPTS = 2
INTERRUPTED = "interrompido"
NO_INCIDENT = "incidente não existe"


async def run_next_learning(  # noqa: PLR0913 — o caso de uso liga os ports do aprendizado
    *,
    queue: LearningQueue,
    memory: MemoryStore,
    repository: IncidentRepository,
    model: LanguageModel,
    clock: Clock,
    policy: MemoryPolicy,
) -> bool:
    """Aprende com o próximo incidente encerrado; False se não havia pedido.

    Escreve o episódio de cada serviço afetado (atualizando o existente se o incidente foi
    reaberto), marca ruído no descarte, grava os dossiês reescritos e marca as notas entregues
    como consolidadas. Falha nunca afeta o incidente.
    """
    job = await queue.claim(clock.now())
    if job is None:
        return False
    incident = await repository.get_incident(job.incident_id)
    if incident is None:  # o pedido referencia o incidente: só acontece com dado corrompido
        await queue.finish(job, False, NO_INCIDENT, (), clock.now())
        return True
    context = await memory_for(memory, incident, await repository.alerts_of(incident.id))
    events = tuple(await repository.events_of(incident.id))
    brief = LearningBrief(incident, events, context, now=clock.now())
    budget = TokenBudget(policy.learning_max_tokens)
    deadline = asyncio.timeout(policy.learning_timeout.total_seconds())
    try:
        async with deadline:
            draft = await model.learn(brief, budget)
        now = clock.now()
        noise = draft.noise or incident.state is IncidentState.DISCARDED
        writes = [
            await _episode(
                memory,
                incident,
                service,
                title=draft.episode_title,
                body=draft.episode_body,
                noise=noise,
                now=now,
            )
            for service in incident.services
        ]
        source = Source(SourceKind.INCIDENT, AGENT_AUTHOR, str(incident.id))
        reason = f"incidente: {incident.title}"
        for dossier in {d.subject: d for d in draft.dossiers if d.subject.strip()}.values():
            kind = (
                SubjectKind.ENVIRONMENT if dossier.subject == ENVIRONMENT else dossier.subject_kind
            )
            writes.append(
                await _dossier(
                    memory,
                    dossier.subject,
                    kind,
                    dossier.body,
                    source=source,
                    now=now,
                    reason=reason,
                )
            )
        await memory.save([*writes, *_consolidated(context.notes)])
    except Exception as error:  # qualquer falha só marca o pedido: o incidente fica como está
        reason = _failure(error, timed_out=deadline.expired())
        await queue.finish(job, False, reason, budget.usages, clock.now())
        return True
    await queue.finish(job, True, None, budget.usages, now)
    return True


async def _episode(  # noqa: PLR0913 — o episódio reúne incidente, assunto, texto e quando
    memory: MemoryStore,
    incident: Incident,
    service: str,
    *,
    title: str,
    body: str,
    noise: bool,
    now: datetime,
) -> Write:
    """O episódio do incidente no serviço; reencerrar atualiza o mesmo, com versão."""
    existing = await memory.episode_of(incident.id, service)
    if existing is None:
        created = Memory(
            id=uuid4(),
            kind=MemoryKind.EPISODE,
            subject=service,
            subject_kind=SubjectKind.SERVICE,
            title=title,
            body=body,
            confidence=Confidence.LEARNED,
            source=Source(SourceKind.INCIDENT, AGENT_AUTHOR, str(incident.id)),
            created_at=now,
            updated_at=now,
            noise=noise,
            incident_id=incident.id,
        )
        return created, None
    reason = f"incidente encerrado de novo: {incident.title}"
    updated, version = written(existing, body, AGENT_AUTHOR, reason, now)
    return replace(updated, title=title, noise=noise), version


async def reap_stuck_learnings(queue: LearningQueue, clock: Clock, policy: MemoryPolicy) -> int:
    """Encerra como falhos (interrompidos) os aprendizados presos; devolve quantos."""
    now = clock.now()
    jobs = await queue.stuck(now - policy.learning_timeout - REAP_GRACE)
    for job in jobs:
        await queue.finish(job, False, INTERRUPTED, (), now)
    return len(jobs)


# ---- Estudar o ambiente ----


class StudyMode(StrEnum):
    INITIAL = "inicial"  # base vazia na subida do worker
    DAILY = "diario"
    FULL = "completo"  # pedido na página, mantém a memória
    RELEARN = "reaprender"  # pedido na página, esquece antes de estudar


class StudyStatus(StrEnum):
    PENDING = "pendente"
    RUNNING = "em_andamento"
    DONE = "concluido"
    FAILED = "falhou"


@dataclass(frozen=True)
class StudyRun:
    id: int
    mode: StudyMode
    author: str
    status: StudyStatus
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    tokens: int = 0
    reason: str | None = None


@dataclass(frozen=True)
class ServiceStudy:
    service: str
    studied_at: datetime | None
    ok: bool
    reason: str | None  # falha ou "sem orçamento nesta rodada"


class StudyQueue(Protocol):
    async def request(self, mode: StudyMode, author: str, now: datetime) -> bool:
        """Pede uma rodada; False (nada muda) se já há uma pendente ou em andamento, exceto
        RELEARN, que substitui a pendente.
        """
        ...

    async def claim(self, now: datetime) -> StudyRun | None:
        """A rodada pendente mais antiga, marcada em andamento."""
        ...

    async def stuck(self, started_before: datetime) -> list[StudyRun]: ...

    async def service_order(self, services: Sequence[str], mode: StudyMode) -> list[str]:
        """A ordem de estudo: nunca estudados e os que ficaram sem orçamento ou falharam
        primeiro, depois os estudados há mais tempo. FULL e RELEARN usam a mesma ordem, mas
        estudam todos.
        """
        ...

    async def service_done(
        self,
        run: StudyRun,
        study: ServiceStudy,
        usages: Sequence[LlmUsage],
    ) -> None:
        """Grava o resultado do estudo do serviço e o uso, sem esperar o fim da rodada.

        A síntese é gravada aqui também, com `service` = ENVIRONMENT.
        """
        ...

    async def finish(
        self, run: StudyRun, status: StudyStatus, reason: str | None, now: datetime
    ) -> None:
        """Encerra a rodada; `tokens` vem do uso gravado nela."""
        ...

    async def last(self) -> StudyRun | None:
        """A rodada mais recente, em qualquer estado (página da memória)."""
        ...

    async def services(self) -> list[ServiceStudy]:
        """O último estudo de cada serviço (página da memória)."""
        ...


STUDY_AUTHOR_SYSTEM = "sistema"  # quem pede as rodadas inicial e diária
NO_BUDGET = "sem orçamento nesta rodada"
STUDY_OFF = "estudo do ambiente desligado"
STUDY_STALE = timedelta(hours=2)  # rodada em andamento há mais tempo foi interrompida
STUDY_REASON = {
    StudyMode.INITIAL: "estudo inicial do ambiente",
    StudyMode.DAILY: "estudo diário do ambiente",
    StudyMode.FULL: "estudo completo do ambiente",
    StudyMode.RELEARN: RELEARN_REASON,
}


def domain_rules(rules: Sequence[AlertRule], alert_filter: Mapping[str, str]) -> list[AlertRule]:
    """As regras do domínio do time: as que têm todos os labels do filtro."""
    return [rule for rule in rules if labels_match(rule.labels, alert_filter)]


async def ensure_initial_study(
    studies: StudyQueue, memory: MemoryStore, clock: Clock, policy: MemoryPolicy
) -> bool:
    """Na subida do worker: com o estudo ligado e sem nenhum dossiê, pede a rodada inicial."""
    if policy.study is None:
        return False
    if any(r.kind is MemoryKind.DOSSIER for r in await memory.all_active()):
        return False
    return await studies.request(StudyMode.INITIAL, STUDY_AUTHOR_SYSTEM, clock.now())


async def request_study(
    *,
    studies: StudyQueue,
    memory: MemoryStore,
    mode: StudyMode,
    author: str,
    clock: Clock,
) -> bool:
    """Pede uma rodada (página ou cron). RELEARN esquece antes, com `relearn_targets`, com o
    autor e o motivo RELEARN_REASON. Levanta InvalidMemoryError para autor vazio.
    """
    author = _author(author)
    if mode is StudyMode.RELEARN:
        now = clock.now()
        targets = relearn_targets(await memory.all_active())
        await memory.save([forgotten(r, author, RELEARN_REASON, now) for r in targets])
    return await studies.request(mode, author, clock.now())


@dataclass(frozen=True)
class _Study:
    """O que uma rodada de estudo usa, para não repetir os ports em cada passo."""

    run: StudyRun
    policy: StudyPolicy
    studies: StudyQueue
    memory: MemoryStore
    model: LanguageModel
    telemetry: Telemetry
    changes: ChangeHistory
    cloud: CloudInventory
    clock: Clock
    rag: RagPolicy | None = None
    embedder: TextEmbedder | None = None


async def run_next_study(  # noqa: PLR0913 — o caso de uso liga os ports do estudo
    *,
    studies: StudyQueue,
    memory: MemoryStore,
    model: LanguageModel,
    telemetry: Telemetry,
    changes: ChangeHistory,
    cloud: CloudInventory,
    clock: Clock,
    policy: MemoryPolicy,
    embedder: TextEmbedder | None = None,
) -> bool:
    """Executa a próxima rodada de estudo; False se não havia.

    Ordem: regras do domínio → serviços (das regras e os ativos com repositório) → estudo de
    cada serviço com teto próprio, parando no teto da rodada → síntese do ambiente e das
    integrações. Falha num serviço é registrada e não para os outros.

    Com `policy.rag` e o `embedder`, cada serviço estudado com sucesso tem a documentação do
    repositório indexada; falha nessa indexação fica no motivo do serviço, que segue estudado.
    """
    run = await studies.claim(clock.now())
    if run is None:
        return False
    if policy.study is None:
        await studies.finish(run, StudyStatus.FAILED, STUDY_OFF, clock.now())
        return True
    rag = policy.rag if embedder is not None else None
    study = _Study(
        run, policy.study, studies, memory, model, telemetry, changes, cloud, clock, rag, embedder
    )
    try:
        rules = domain_rules(await telemetry.alert_rules(), policy.study.alert_filter)
    except ToolUnavailableError as error:
        reason = f"regras de alerta: {UNAVAILABLE}: {error}"[:REASON_LIMIT]
        await studies.finish(run, StudyStatus.FAILED, reason, clock.now())
        return True
    services = await _services(study, rules)
    records = await memory.all_active()
    spent, changed = 0, False
    for service in await studies.service_order(services, run.mode):
        if spent >= policy.study.run_max_tokens:
            await studies.service_done(run, ServiceStudy(service, None, False, NO_BUDGET), ())
            continue
        result, usages = await _study_service(study, service, rules, records)
        if result.ok:
            result = await _index_documents(study, result)
        spent += _tokens(usages)
        changed = changed or result.ok
        await studies.service_done(run, result, usages)
    reason = None
    if changed or await memory.dossier(ENVIRONMENT) is None:
        reason = await _synthesize(study, rules, services)
    status = StudyStatus.DONE if reason is None else StudyStatus.FAILED
    await studies.finish(run, status, reason, clock.now())
    return True


DOCUMENTS_NOT_INDEXED = "documentação não indexada"


async def _index_documents(study: _Study, result: ServiceStudy) -> ServiceStudy:
    """Indexa os `.md` do serviço estudado, com RAG; a falha vai para o motivo do serviço."""
    if study.rag is None or study.embedder is None:
        return result
    try:
        await index_documents(
            service=result.service,
            memory=study.memory,
            changes=study.changes,
            embedder=study.embedder,
            clock=study.clock,
            policy=study.rag,
        )
    except Exception as error:  # a documentação não derruba o estudo do serviço
        reason = f"{DOCUMENTS_NOT_INDEXED}: {error or type(error).__name__}"
        return replace(result, reason=reason[:REASON_LIMIT])
    return result


async def _services(study: _Study, rules: Sequence[AlertRule]) -> list[str]:
    """Os serviços das regras do domínio e os ativos na telemetria que têm repositório."""
    services = [rule.service for rule in rules if rule.service != UNKNOWN_SERVICE]
    since = study.clock.now() - study.policy.lookback
    try:
        active = await study.telemetry.active_services(since)
    except ToolUnavailableError:
        active = []
    for service in active:
        if service in services:
            continue
        try:
            deploys = await study.changes.recent_deploys(service, since)
        except ToolUnavailableError:
            continue
        if NO_REPOSITORY not in deploys.summary:
            services.append(service)
    return list(dict.fromkeys(services))


def _subject_context(
    records: Sequence[Memory],
    subjects: Collection[str],
    *,
    all_notes: bool,
    episodes_of: Collection[str] | None = None,
) -> MemoryContext:
    """Dossiês, notas e os episódios mais recentes dos assuntos, a partir dos registros ativos.

    `all_notes` entrega também as notas já consolidadas (reaprender refaz os dossiês que as
    incorporaram); `episodes_of` None entrega os episódios de qualquer assunto.
    """
    episode_subjects = subjects if episodes_of is None else episodes_of
    episodes = sorted(
        (r for r in records if r.kind is MemoryKind.EPISODE and r.subject in episode_subjects),
        key=lambda r: r.updated_at,
        reverse=True,
    )
    return MemoryContext(
        dossiers=tuple(
            r for r in records if r.kind is MemoryKind.DOSSIER and r.subject in subjects
        ),
        notes=tuple(
            r
            for r in records
            if r.kind is MemoryKind.NOTE
            and r.subject in subjects
            and (all_notes or not r.consolidated)
        ),
        episodes=tuple(episodes[:EPISODE_LIMIT]),
    )


def _links(sources: Sequence[str], evaluations: Sequence[Evaluation]) -> str | None:
    """Os links consultados, um por linha, sem repetir."""
    links = dict.fromkeys([*sources, *(e.url for e in evaluations if e.url)])
    return "\n".join(link for link in links if link) or None


async def _study_service(
    study: _Study,
    service: str,
    rules: Sequence[AlertRule],
    records: Sequence[Memory],
) -> tuple[ServiceStudy, Sequence[LlmUsage]]:
    policy, clock = study.policy, study.clock
    relearn = study.run.mode is StudyMode.RELEARN
    context = _subject_context(records, {service}, all_notes=relearn)
    service_rules = tuple(rule for rule in rules if rule.service == service)
    brief = ServiceStudyBrief(service, service_rules, context, now=clock.now())
    toolbox = Toolbox(
        study.telemetry,
        study.changes,
        clock,
        policy.lookback,
        study.cloud,
        memory=study.memory,
        embedder=study.embedder,
    )
    budget = TokenBudget(policy.service_max_tokens)
    deadline = asyncio.timeout(policy.service_timeout.total_seconds())
    try:
        async with deadline:
            draft = await study.model.study_service(brief, toolbox, budget)
        now = clock.now()
        source = Source(SourceKind.STUDY, AGENT_AUTHOR, _links(draft.sources, toolbox.evaluations))
        reason = STUDY_REASON[study.run.mode]
        dossier = await _dossier(
            study.memory,
            service,
            SubjectKind.SERVICE,
            draft.body,
            source=source,
            now=now,
            reason=reason,
        )
        await study.memory.save([dossier, *_consolidated(context.notes)])
    except Exception as error:  # falha num serviço fica registrada e não para os outros
        failure = _failure(error, timed_out=deadline.expired())
        return ServiceStudy(service, clock.now(), False, failure), budget.usages
    return ServiceStudy(service, now, True, None), budget.usages


async def _synthesize(
    study: _Study, rules: Sequence[AlertRule], services: Sequence[str]
) -> str | None:
    """Dossiê do ambiente e das integrações; devolve o motivo da falha, se houver."""
    memory, clock, policy = study.memory, study.clock, study.policy
    records = await memory.all_active()
    service_dossiers = tuple(
        r for r in records if r.kind is MemoryKind.DOSSIER and r.subject in services
    )
    integrations = {r.subject for r in records if r.subject_kind is SubjectKind.INTEGRATION}
    context = _subject_context(
        records,
        {ENVIRONMENT, *integrations},
        all_notes=study.run.mode is StudyMode.RELEARN,
        episodes_of={r.subject for r in records},
    )
    try:
        overview = await study.cloud.overview()
        cloud = (
            overview.summary
            if overview.url is None
            else f"{overview.summary}\nLink: {overview.url}"
        )
    except ToolUnavailableError as error:
        cloud = f"{UNAVAILABLE}: {error}"
    brief = SynthesisBrief(tuple(rules), service_dossiers, context, cloud, now=clock.now())
    budget = TokenBudget(policy.service_max_tokens)
    deadline = asyncio.timeout(policy.synthesis_timeout.total_seconds())
    try:
        async with deadline:
            draft = await study.model.synthesize(brief, budget)
        now = clock.now()
        reason = STUDY_REASON[study.run.mode]
        environment = draft.environment
        writes = [
            await _dossier(
                memory,
                ENVIRONMENT,
                SubjectKind.ENVIRONMENT,
                environment.body,
                source=Source(SourceKind.STUDY, AGENT_AUTHOR, _links(environment.sources, ())),
                now=now,
                reason=reason,
            )
        ]
        drafts = {d.subject: d for d in draft.integrations if d.subject.strip()}
        for subject, integration in drafts.items():
            if subject == ENVIRONMENT or subject in services:
                continue
            source = Source(SourceKind.STUDY, AGENT_AUTHOR, _links(integration.sources, ()))
            writes.append(
                await _dossier(
                    memory,
                    subject,
                    SubjectKind.INTEGRATION,
                    integration.body,
                    source=source,
                    now=now,
                    reason=reason,
                )
            )
        await memory.save([*writes, *_consolidated(context.notes)])
    except Exception as error:  # a síntese falha sem perder os dossiês dos serviços
        failure = _failure(error, timed_out=deadline.expired())
        await study.studies.service_done(
            study.run, ServiceStudy(ENVIRONMENT, clock.now(), False, failure), budget.usages
        )
        return f"síntese: {failure}"
    await study.studies.service_done(
        study.run, ServiceStudy(ENVIRONMENT, now, True, None), budget.usages
    )
    return None


async def reap_stuck_studies(studies: StudyQueue, clock: Clock, policy: MemoryPolicy) -> int:
    """Encerra como falhas (interrompidas) as rodadas presas; devolve quantas."""
    del policy  # uma rodada tem vários serviços: o limite é fixo, STUDY_STALE
    now = clock.now()
    runs = await studies.stuck(now - STUDY_STALE)
    for run in runs:
        await studies.finish(run, StudyStatus.FAILED, INTERRUPTED, now)
    return len(runs)


# ---- Página da memória ----

EDIT_REASON = "editado na página"
FORGET_REASON = "esquecido na página"


def _author(author: str) -> str:
    author = author.strip()
    if not author:
        raise InvalidMemoryError("informe o seu nome antes de mudar a memória")
    return author


def _body(text: str) -> str:
    text = text.strip()
    if not text:
        raise InvalidMemoryError("escreva o texto")
    return text


async def _record(memory: MemoryStore, memory_id: UUID) -> Memory:
    found = await memory.get(memory_id)
    if found is None:
        raise MemoryNotFoundError(f"registro {memory_id} da memória não existe")
    return found


async def add_note(  # noqa: PLR0913 — a nota precisa de onde, o quê, quem e quando
    *,
    memory: MemoryStore,
    subject: str,
    subject_kind: SubjectKind,
    text: str,
    author: str,
    clock: Clock,
) -> Memory:
    """Nota confirmada escrita por uma pessoa; levanta InvalidMemoryError."""
    author, body = _author(author), _body(text)
    subject = subject.strip()
    if not subject:
        raise InvalidMemoryError("informe o assunto")
    now = clock.now()
    note = Memory(
        id=uuid4(),
        kind=MemoryKind.NOTE,
        subject=subject,
        subject_kind=subject_kind,
        title=note_title(body),
        body=body,
        confidence=Confidence.CONFIRMED,
        source=Source(SourceKind.PERSON, author),
        created_at=now,
        updated_at=now,
    )
    await memory.save([(note, None)])
    return note


async def edit_memory(
    *, memory: MemoryStore, memory_id: UUID, body: str, author: str, clock: Clock
) -> Memory:
    """Levanta MemoryNotFoundError ou InvalidMemoryError."""
    author, body = _author(author), _body(body)
    updated, version = written(
        await _record(memory, memory_id), body, author, EDIT_REASON, clock.now()
    )
    await memory.save([(updated, version)])
    return updated


async def confirm_memory(
    *, memory: MemoryStore, memory_id: UUID, author: str, clock: Clock
) -> Memory:
    author = _author(author)
    updated, version = confirmed(await _record(memory, memory_id), author, clock.now())
    await memory.save([(updated, version)])
    return updated


async def forget_memory(
    *, memory: MemoryStore, memory_id: UUID, author: str, clock: Clock
) -> Memory:
    author = _author(author)
    record = await _record(memory, memory_id)
    updated, version = forgotten(record, author, FORGET_REASON, clock.now())
    await memory.save([(updated, version)])
    return updated


async def restore_memory(
    *, memory: MemoryStore, memory_id: UUID, version_at: datetime, author: str, clock: Clock
) -> Memory:
    """Volta o registro à versão gravada em `version_at`; levanta MemoryNotFoundError ou
    InvalidMemoryError (versão inexistente).
    """
    author = _author(author)
    record = await _record(memory, memory_id)
    version = next((v for v in await memory.versions(memory_id) if v.at == version_at), None)
    if version is None:
        raise InvalidMemoryError("versão não encontrada no histórico")
    updated, previous = restored(record, version, author, clock.now())
    await memory.save([(updated, previous)])
    return updated

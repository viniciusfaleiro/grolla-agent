"""Suíte de contrato de MemoryStore.

O adapter sobrescreve a fixture `memory`. A suíte roda também contra o banco real
compartilhado: cada teste usa assuntos e palavras únicos (uuid4) e só confere os próprios
registros. O dossiê do ambiente é único no banco: quem precisa dele reaproveita o existente.

Regras que a suíte fixa:
- `context_for` devolve o dossiê do ambiente, os dos serviços e os das integrações cujo nome
  aparece no dossiê de um desses serviços; as notas ativas e não consolidadas desses assuntos;
  e até EPISODE_LIMIT episódios ativos, primeiro os do serviço que citam uma das regras de
  alerta, depois os que casam com o texto (o texto da suíte tem uma palavra só, para valer em
  qualquer busca textual).
- `search` só devolve ativos; confirmados primeiro, depois os mais recentes.
- `dossier` devolve o dossiê esquecido quando não há ativo.
"""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from grolla.application.memory_store import MemoryStore
from grolla.domain.memory import (
    ENVIRONMENT,
    EPISODE_LIMIT,
    Confidence,
    Memory,
    MemoryKind,
    MemoryVersion,
    Source,
    SourceKind,
    SubjectKind,
    forgotten,
    written,
)
from grolla.testing.builders import T0


def unique(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:10]}"


def record(  # noqa: PLR0913 — cada campo do registro é ajustável no teste
    subject: str,
    kind: MemoryKind = MemoryKind.NOTE,
    *,
    body: str = "fato do ambiente",
    subject_kind: SubjectKind = SubjectKind.SERVICE,
    confidence: Confidence = Confidence.LEARNED,
    at: timedelta = timedelta(0),
    incident_id: UUID | None = None,
    consolidated: bool = False,
    noise: bool = False,
) -> Memory:
    source = (
        Source(SourceKind.INCIDENT, "Grolla", str(incident_id))
        if incident_id
        else Source(SourceKind.PERSON, "Ana")
    )
    return Memory(
        id=uuid4(),
        kind=kind,
        subject=subject,
        subject_kind=subject_kind,
        title=f"{kind.value} de {subject}",
        body=body,
        confidence=confidence,
        source=source,
        created_at=T0 + at,
        updated_at=T0 + at,
        consolidated=consolidated,
        noise=noise,
        incident_id=incident_id,
    )


async def environment_dossier(memory: MemoryStore) -> Memory:
    """O dossiê do ambiente ativo, reaproveitando o que já existe no banco."""
    current = await memory.dossier(ENVIRONMENT)
    if current is None:
        current = record(ENVIRONMENT, MemoryKind.DOSSIER, subject_kind=SubjectKind.ENVIRONMENT)
        await memory.save([(current, None)])
    elif not current.active:
        current, version = written(current, current.body, "Grolla", "teste", T0)
        await memory.save([(current, version)])
    return current


class MemoryStoreContract:
    @pytest.fixture
    def memory(self) -> MemoryStore:
        raise NotImplementedError

    async def test_save_and_get_round_trip(self, memory: MemoryStore) -> None:
        episode = record(unique("svc-"), MemoryKind.EPISODE, incident_id=uuid4(), noise=True)

        await memory.save([(episode, None)])

        assert await memory.get(episode.id) == episode
        assert await memory.get(uuid4()) is None
        assert await memory.versions(episode.id) == []

    async def test_save_updates_by_id_and_keeps_versions_newest_first(
        self, memory: MemoryStore
    ) -> None:
        note = record(unique("svc-"))
        await memory.save([(note, None)])

        first, v1 = written(note, "texto 2", "Ana", "editado", T0 + timedelta(minutes=1))
        second, v2 = written(first, "texto 3", "Bia", "editado", T0 + timedelta(minutes=2))
        await memory.save([(first, v1)])
        await memory.save([(second, v2)])

        assert await memory.get(note.id) == second
        assert await memory.versions(note.id) == [v2, v1]
        assert v1 == MemoryVersion(
            note.id, T0 + timedelta(minutes=1), "Ana", "editado", "fato do ambiente", True
        )

    async def test_save_writes_several_records_at_once(self, memory: MemoryStore) -> None:
        subject = unique("svc-")
        dossier = record(subject, MemoryKind.DOSSIER)
        note = record(subject)

        await memory.save([(dossier, None), (note, None)])

        assert {r.id for r in await memory.of_subject(subject)} == {dossier.id, note.id}

    async def test_dossier_prefers_the_active_one_and_falls_back_to_the_forgotten(
        self, memory: MemoryStore
    ) -> None:
        subject = unique("svc-")
        assert await memory.dossier(subject) is None
        dossier = record(subject, MemoryKind.DOSSIER)
        await memory.save([(dossier, None)])
        assert await memory.dossier(subject) == dossier

        gone, version = forgotten(dossier, "Ana", "reaprender o ambiente", T0 + timedelta(hours=1))
        await memory.save([(gone, version)])

        assert await memory.dossier(subject) == gone

    async def test_episode_of_finds_the_episode_of_the_incident_and_subject(
        self, memory: MemoryStore
    ) -> None:
        subject, other = unique("svc-"), unique("svc-")
        incident_id = uuid4()
        mine = record(subject, MemoryKind.EPISODE, incident_id=incident_id)
        theirs = record(other, MemoryKind.EPISODE, incident_id=incident_id)
        await memory.save([(mine, None), (theirs, None)])

        assert await memory.episode_of(incident_id, subject) == mine
        assert await memory.episode_of(incident_id, other) == theirs
        assert await memory.episode_of(uuid4(), subject) is None

    async def test_all_active_leaves_forgotten_records_out(self, memory: MemoryStore) -> None:
        subject = unique("svc-")
        kept = record(subject)
        gone = replace(record(subject), active=False)
        await memory.save([(kept, None), (gone, None)])

        ids = {r.id for r in await memory.all_active()}

        assert kept.id in ids
        assert gone.id not in ids

    async def test_search_finds_active_records_confirmed_and_recent_first(
        self, memory: MemoryStore
    ) -> None:
        subject, word = unique("svc-"), unique("pool")
        old = record(subject, body=f"{word} esgotado", at=timedelta(hours=1))
        recent = record(subject, body=f"{word} de novo", at=timedelta(hours=2))
        confirmed = record(subject, body=f"{word} explicado", confidence=Confidence.CONFIRMED)
        gone = replace(record(subject, body=f"{word} esquecido"), active=False)
        unrelated = record(subject, body="outro assunto")
        await memory.save([(r, None) for r in (old, recent, confirmed, gone, unrelated)])

        found = await memory.search(subject, word, limit=10)

        assert [r.id for r in found] == [confirmed.id, recent.id, old.id]
        assert [r.id for r in await memory.search(subject, word, limit=2)] == [
            confirmed.id,
            recent.id,
        ]

    async def test_search_without_subject_looks_in_every_subject(self, memory: MemoryStore) -> None:
        word = unique("fila")
        first = record(unique("svc-"), body=f"{word} parada")
        second = record(unique("int-"), body=f"{word} cheia", subject_kind=SubjectKind.INTEGRATION)
        await memory.save([(first, None), (second, None)])

        found = {r.id for r in await memory.search(None, word, limit=10)}

        assert found == {first.id, second.id}
        assert await memory.search(first.subject, unique("nada"), limit=10) == []

    async def test_context_for_brings_dossiers_of_the_environment_services_and_cited_integrations(
        self, memory: MemoryStore
    ) -> None:
        environment = await environment_dossier(memory)
        service, cited, other = unique("svc-"), unique("db-"), unique("db-")
        dossier = record(service, MemoryKind.DOSSIER, body=f"chama o banco {cited}")
        integration = record(cited, MemoryKind.DOSSIER, subject_kind=SubjectKind.INTEGRATION)
        unrelated = record(other, MemoryKind.DOSSIER, subject_kind=SubjectKind.INTEGRATION)
        await memory.save([(r, None) for r in (dossier, integration, unrelated)])

        context = await memory.context_for([service], [], unique("texto"))

        ids = [d.id for d in context.dossiers]
        assert environment.id in ids
        assert dossier.id in ids
        assert integration.id in ids
        assert unrelated.id not in ids

    async def test_context_for_brings_pending_notes_of_those_subjects(
        self, memory: MemoryStore
    ) -> None:
        service, other = unique("svc-"), unique("svc-")
        pending = record(service, confidence=Confidence.CONFIRMED)
        consolidated = record(service, consolidated=True)
        gone = replace(record(service), active=False)
        elsewhere = record(other)
        await memory.save([(r, None) for r in (pending, consolidated, gone, elsewhere)])

        context = await memory.context_for([service], [], unique("texto"))

        ids = {n.id for n in context.notes}
        assert pending.id in ids
        assert not ids & {consolidated.id, gone.id, elsewhere.id}

    async def test_context_for_ranks_episodes_by_rule_then_by_text(
        self, memory: MemoryStore
    ) -> None:
        service, word = unique("svc-"), unique("timeout")
        rule = f"Regra {unique('r')}"
        same_rule = record(
            service, MemoryKind.EPISODE, body=f"alerta {rule} disparou", incident_id=uuid4()
        )
        by_text = record(
            unique("svc-"), MemoryKind.EPISODE, body=f"causa: {word}", incident_id=uuid4()
        )
        gone = replace(
            record(service, MemoryKind.EPISODE, body=f"{rule} {word}", incident_id=uuid4()),
            active=False,
        )
        unrelated = record(
            unique("svc-"), MemoryKind.EPISODE, body="nada a ver", incident_id=uuid4()
        )
        await memory.save([(r, None) for r in (same_rule, by_text, gone, unrelated)])

        context = await memory.context_for([service], [rule], word)

        ids = [e.id for e in context.episodes]
        assert ids[:2] == [same_rule.id, by_text.id]
        assert gone.id not in ids
        assert unrelated.id not in ids
        assert len(ids) <= EPISODE_LIMIT

    async def test_context_for_brings_at_most_the_episode_limit(self, memory: MemoryStore) -> None:
        service, word = unique("svc-"), unique("lento")
        episodes = [
            record(service, MemoryKind.EPISODE, body=f"{word} {n}", incident_id=uuid4())
            for n in range(EPISODE_LIMIT + 2)
        ]
        await memory.save([(e, None) for e in episodes])

        context = await memory.context_for([service], [], word)

        assert len(context.episodes) == EPISODE_LIMIT

    async def test_of_subject_lists_everything_dossier_then_episodes_then_notes(
        self, memory: MemoryStore
    ) -> None:
        subject = unique("svc-")
        note = record(subject, at=timedelta(hours=3))
        gone = replace(record(subject, at=timedelta(hours=2)), active=False)
        episode = record(subject, MemoryKind.EPISODE, incident_id=uuid4())
        dossier = record(subject, MemoryKind.DOSSIER, at=timedelta(hours=1))
        await memory.save([(r, None) for r in (note, gone, episode, dossier)])

        listed = await memory.of_subject(subject)

        assert [r.id for r in listed] == [dossier.id, episode.id, note.id, gone.id]

    async def test_subjects_summarize_by_kind_environment_first(self, memory: MemoryStore) -> None:
        await environment_dossier(memory)
        service, integration = unique("svc-"), unique("int-")
        await memory.save(
            [
                (record(service, at=timedelta(hours=1)), None),
                (record(service, at=timedelta(hours=2)), None),
                (replace(record(service), active=False), None),
                (record(integration, subject_kind=SubjectKind.INTEGRATION), None),
            ]
        )

        summaries = await memory.subjects()

        names = [s.subject for s in summaries]
        assert names[0] == ENVIRONMENT
        assert names.index(service) < names.index(integration)
        mine = next(s for s in summaries if s.subject == service)
        assert (mine.kind, mine.records) == (SubjectKind.SERVICE, 2)
        assert mine.updated_at == T0 + timedelta(hours=2)

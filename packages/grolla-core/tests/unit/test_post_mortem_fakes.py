"""Os fakes do post mortem passam nas suítes de contrato."""

import pytest
from contracts.document_renderer_contract import DocumentRendererContract
from contracts.language_model_contract import PostMortemModelContract
from contracts.post_mortem_store_contract import PostMortemStoreContract
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import (
    STAGE_POST_MORTEM,
    LanguageModel,
    LanguageModelError,
    PostMortemBrief,
    TokenBudget,
)
from grolla.application.post_mortem import DocumentRenderer, PostMortemStore, RenderError
from grolla.domain.post_mortem import PostMortemStatus, SectionKey
from grolla.testing.builders import T0, make_incident
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.investigation import ScriptedLanguageModel, fake_usage
from grolla.testing.post_mortem import (
    FakeDocumentRenderer,
    InMemoryPostMortemStore,
    make_post_mortem,
)


class TestInMemoryPostMortemStoreContract(PostMortemStoreContract):
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        return InMemoryIncidentRepository()

    @pytest.fixture
    def store(self, repository: IncidentRepository) -> PostMortemStore:
        assert isinstance(repository, InMemoryIncidentRepository)
        return InMemoryPostMortemStore(repository)


class TestFakeDocumentRendererContract(DocumentRendererContract):
    @pytest.fixture
    def renderer(self) -> DocumentRenderer:
        return FakeDocumentRenderer()


class TestScriptedLanguageModelPostMortemContract(PostMortemModelContract):
    @pytest.fixture
    def post_mortem_model(self) -> LanguageModel:
        return ScriptedLanguageModel()

    @pytest.fixture
    def failing_post_mortem_model(self) -> LanguageModel:
        return ScriptedLanguageModel(
            post_mortem_error=LanguageModelError("API fora do ar"),
            regeneration_error=LanguageModelError("API fora do ar"),
        )


def test_fake_renderer_carries_the_title_and_can_fail() -> None:
    incident = make_incident()
    pm = make_post_mortem(incident.id, status=PostMortemStatus.APPROVED)

    assert incident.title.encode() in FakeDocumentRenderer().render(pm, incident)
    failing = FakeDocumentRenderer(RenderError("sem fonte"))
    with pytest.raises(RenderError):
        failing.render(pm, incident)
    assert failing.rendered == [(pm, incident)]


async def test_finish_records_the_model_usage_on_the_incident() -> None:
    repository = InMemoryIncidentRepository()
    store = InMemoryPostMortemStore(repository)
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.request_post_mortem(incident.id, T0)
    job = await store.claim(T0)
    assert job is not None
    usage = fake_usage(500, STAGE_POST_MORTEM)

    await store.finish(job, None, versions=[], usages=[usage], reason=None, at=T0)

    assert repository.usages == [(incident.id, usage, T0)]


async def test_scripted_model_records_the_requests_and_returns_the_scripted_text() -> None:
    model = ScriptedLanguageModel(regeneration="Texto novo.")
    incident = make_incident()
    brief = PostMortemBrief(incident, "contexto", current=None, impact=None, now=T0)

    draft = await model.draft_post_mortem(brief, TokenBudget(10_000))
    body = await model.regenerate_section(brief, SectionKey.FACTORS, "mais", TokenBudget(10_000))

    assert draft.sections[SectionKey.SUMMARY] == f"Resumo executivo de {incident.title}."
    assert len(draft.actions) == 2
    assert body == "Texto novo."
    assert model.post_mortem_briefs == [brief]
    assert model.regenerations == [(brief, SectionKey.FACTORS, "mais")]
    default = await ScriptedLanguageModel().regenerate_section(
        brief, SectionKey.FACTORS, "mais", TokenBudget(10_000)
    )
    assert default == "Fatores contribuintes reescrita (mais)."

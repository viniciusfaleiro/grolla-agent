"""Apresentação da memória do Grolla: rótulos, agrupamentos e fontes com link."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from grolla.application.memory import NO_BUDGET, ServiceStudy, StudyMode, StudyStatus
from grolla.application.memory_store import SubjectSummary
from grolla.domain.memory import ENVIRONMENT, Memory, MemoryKind, SourceKind, SubjectKind

SUBJECT_KIND_LABELS = {
    SubjectKind.ENVIRONMENT: "Ambiente",
    SubjectKind.SERVICE: "Serviços",
    SubjectKind.INTEGRATION: "Integrações",
}

# Uma nota num assunto novo escolhe o tipo; o ambiente é um só e já existe pelo nome.
NEW_SUBJECT_KINDS = {
    SubjectKind.SERVICE: "serviço",
    SubjectKind.INTEGRATION: "integração",
}

STUDY_MODE_LABELS = {
    StudyMode.INITIAL: "estudo inicial",
    StudyMode.DAILY: "estudo diário",
    StudyMode.FULL: "estudo completo",
    StudyMode.RELEARN: "reaprender do zero",
}

STUDY_STATUS_LABELS = {
    StudyStatus.PENDING: "na fila",
    StudyStatus.RUNNING: "estudando agora",
    StudyStatus.DONE: "concluído",
    StudyStatus.FAILED: "falhou",
}

SUBJECT_KIND_NAMES = {
    SubjectKind.ENVIRONMENT: "ambiente",
    SubjectKind.SERVICE: "serviço",
    SubjectKind.INTEGRATION: "integração",
}

SUBJECT_MAX_LENGTH = 100


def subject_url(subject: str) -> str:
    return f"/memoria/{quote(subject, safe='')}"


@dataclass(frozen=True)
class Link:
    label: str
    url: str


def source_of(memory: Memory) -> tuple[str, list[Link]]:
    """A fonte em uma linha e os links dela (incidente, chat ou o que o estudo consultou)."""
    source = memory.source
    ref = source.ref or (str(memory.incident_id) if memory.incident_id else None)
    match source.kind:
        case SourceKind.STUDY:
            urls = (url.strip() for url in (source.ref or "").splitlines())
            return "estudo do ambiente", [Link(url, url) for url in urls if url]
        case SourceKind.INCIDENT:
            return "incidente", [Link("abrir o incidente", f"/incidents/{ref}")] if ref else []
        case SourceKind.CHAT:
            links = [Link("abrir o chat", f"/incidents/{ref}")] if ref else []
            return f"chat, por {source.author}", links
        case SourceKind.PERSON:
            return f"escrito por {source.author}", []


@dataclass(frozen=True)
class SubjectGroup:
    label: str
    subjects: tuple[SubjectSummary, ...]


def subject_groups(summaries: Sequence[SubjectSummary]) -> list[SubjectGroup]:
    """Os assuntos por tipo, na ordem ambiente, serviços, integrações; sem grupos vazios."""
    groups = (
        SubjectGroup(label, tuple(s for s in summaries if s.kind is kind))
        for kind, label in SUBJECT_KIND_LABELS.items()
    )
    return [group for group in groups if group.subjects]


@dataclass(frozen=True)
class SubjectRecords:
    dossiers: tuple[Memory, ...]
    episodes: tuple[Memory, ...]
    notes: tuple[Memory, ...]


def _ordered(records: Sequence[Memory], kind: MemoryKind) -> tuple[Memory, ...]:
    """Ativos primeiro, depois os mais recentes."""
    chosen = [r for r in records if r.kind is kind]
    chosen.sort(key=lambda r: r.updated_at, reverse=True)
    chosen.sort(key=lambda r: not r.active)
    return tuple(chosen)


def split_records(records: Sequence[Memory]) -> SubjectRecords:
    return SubjectRecords(
        dossiers=_ordered(records, MemoryKind.DOSSIER),
        episodes=_ordered(records, MemoryKind.EPISODE),
        notes=_ordered(records, MemoryKind.NOTE),
    )


def kind_of(subject: str, records: Sequence[Memory]) -> SubjectKind | None:
    """O tipo do assunto; None para assunto novo, em que a nota escolhe o tipo."""
    if subject == ENVIRONMENT:
        return SubjectKind.ENVIRONMENT
    return records[0].subject_kind if records else None


@dataclass(frozen=True)
class StudyProgress:
    studied: tuple[ServiceStudy, ...]
    pending: tuple[ServiceStudy, ...]  # ficaram sem orçamento: primeiro na próxima rodada
    failed: tuple[ServiceStudy, ...]


def study_progress(services: Sequence[ServiceStudy]) -> StudyProgress:
    return StudyProgress(
        studied=tuple(s for s in services if s.ok),
        pending=tuple(s for s in services if not s.ok and s.reason == NO_BUDGET),
        failed=tuple(s for s in services if not s.ok and s.reason != NO_BUDGET),
    )


def parse_version_at(value: str) -> datetime | None:
    """O instante da versão vindo do form; None se não é uma data."""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None

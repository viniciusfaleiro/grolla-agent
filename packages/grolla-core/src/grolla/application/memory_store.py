"""Port da memória do agente: onde a base de conhecimento, os episódios e as notas vivem.

Fica num módulo próprio porque as ferramentas da investigação (`recall`, `remember`) e os casos
de uso da memória usam o mesmo port. `memory_for` também fica aqui, para a análise, a validação e
o chat a usarem sem import circular com `grolla.application.memory` (que reexporta).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from grolla.domain.incidents import Alert, Incident
from grolla.domain.memory import Memory, MemoryContext, MemoryVersion, SubjectKind


@dataclass(frozen=True)
class SubjectSummary:
    subject: str
    kind: SubjectKind
    records: int  # ativos
    updated_at: datetime
    studied_at: datetime | None  # último estudo do serviço; None para os outros tipos


class MemoryStore(Protocol):
    async def context_for(
        self, services: Sequence[str], alert_names: Sequence[str], text: str
    ) -> MemoryContext:
        """A memória para investigar um incidente dos `services`.

        - dossiês: o do ambiente, o de cada serviço e o de cada integração cujo nome aparece
          no dossiê de um desses serviços;
        - notas: ativas e não consolidadas desses assuntos;
        - episódios: até EPISODE_LIMIT ativos, primeiro os do mesmo serviço com uma das
          `alert_names` no texto, depois por relevância da busca textual de `text`.
        """
        ...

    async def search(self, subject: str | None, query: str, limit: int) -> list[Memory]:
        """Registros ativos que casam com a busca textual; confirmados e recentes primeiro.

        `subject` None busca em todos os assuntos.
        """
        ...

    async def get(self, memory_id: UUID) -> Memory | None: ...

    async def dossier(self, subject: str) -> Memory | None:
        """O dossiê do assunto, ativo ou esquecido; None se nunca existiu."""
        ...

    async def episode_of(self, incident_id: UUID, subject: str) -> Memory | None: ...

    async def all_active(self) -> list[Memory]: ...

    async def save(self, records: Sequence[tuple[Memory, MemoryVersion | None]]) -> None:
        """Grava (insere ou atualiza pelo id) os registros e as versões numa transação."""
        ...

    async def subjects(self) -> list[SubjectSummary]:
        """Assuntos com algum registro: ambiente primeiro, depois serviços e integrações."""
        ...

    async def of_subject(self, subject: str) -> list[Memory]:
        """Todos os registros do assunto, inclusive esquecidos; dossiê, episódios e notas."""
        ...

    async def versions(self, memory_id: UUID) -> list[MemoryVersion]:
        """Mais recentes primeiro."""
        ...


async def memory_for(
    memory: MemoryStore | None, incident: Incident, alerts: Sequence[Alert]
) -> MemoryContext:
    """A memória para investigar o incidente; vazia sem memória ligada."""
    if memory is None:
        return MemoryContext()
    names = list(dict.fromkeys(alert.rule_name for alert in alerts))
    text = " ".join([incident.title, *(alert.summary for alert in alerts)])
    return await memory.context_for(incident.services, names, text)

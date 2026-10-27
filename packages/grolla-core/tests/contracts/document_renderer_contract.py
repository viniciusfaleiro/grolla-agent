"""Suíte de contrato de DocumentRenderer.

O adapter sobrescreve `renderer`. O documento é o PDF do post mortem; a suíte confere que ele sai
para qualquer texto que o time escreva, inclusive o que não cabe no latin-1.
"""

from dataclasses import replace
from datetime import date

import pytest
from grolla.application.post_mortem import DocumentRenderer
from grolla.domain.incidents import Incident, IncidentState, Severity
from grolla.domain.post_mortem import (
    ActionKind,
    ActionStatus,
    PostMortem,
    PostMortemStatus,
    Section,
)
from grolla.testing.builders import T0, make_incident
from grolla.testing.post_mortem import make_action, make_post_mortem

ACCENTS = "Ação, decisão e correção: o índice ficou lento às 03:01 (média 1,5 s)."
OUTSIDE_LATIN1 = "Erro → 5xx ✓ — “aspas” • emoji 🔥 · 中文 · €"


class DocumentRendererContract:
    @pytest.fixture
    def renderer(self) -> DocumentRenderer:
        raise NotImplementedError

    @pytest.fixture
    def incident(self) -> Incident:
        incident = make_incident(
            state=IncidentState.RESOLVED, severity=Severity.P1, services=("pedidos-api",)
        )
        return replace(incident, title=f"Pedidos com erro — {ACCENTS}")

    @pytest.fixture
    def pm(self, incident: Incident) -> PostMortem:
        action = replace(
            make_action(f"Criar alarme {OUTSIDE_LATIN1}", kind=ActionKind.MITIGATE),
            owner="João Conceição",
            due=date(2026, 2, 1),
            status=ActionStatus.DONE,
        )
        pm = make_post_mortem(incident.id, status=PostMortemStatus.APPROVED, actions=[action])
        body = f"## {ACCENTS}\n\n- {OUTSIDE_LATIN1}\n- **negrito** e `código`\n\n{'x' * 3000}"
        sections = tuple(Section(s.key, body, s.author, s.updated_at) for s in pm.sections)
        return replace(pm, sections=sections)

    def test_renders_a_pdf(
        self, renderer: DocumentRenderer, pm: PostMortem, incident: Incident
    ) -> None:
        document = renderer.render(pm, incident)

        assert document.startswith(b"%PDF-")
        assert len(document) > len(b"%PDF-")

    def test_renders_a_closed_post_mortem_without_actions(
        self, renderer: DocumentRenderer, incident: Incident
    ) -> None:
        pm = make_post_mortem(incident.id, status=PostMortemStatus.CLOSED, at=T0)

        assert renderer.render(pm, incident).startswith(b"%PDF-")

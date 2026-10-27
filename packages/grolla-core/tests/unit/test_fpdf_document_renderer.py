"""FpdfDocumentRenderer: PDF de verdade, conferido pela assinatura, pelas páginas e pelo texto."""

import re
import zlib
from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

import pytest
from contracts.document_renderer_contract import DocumentRendererContract
from fpdf import FPDF
from fpdf.errors import FPDFException
from grolla.adapters.fpdf import FpdfDocumentRenderer, latin1
from grolla.application.post_mortem import DocumentRenderer, RenderError
from grolla.domain.incidents import Incident, IncidentState, Severity
from grolla.domain.post_mortem import (
    SECTION_TITLES,
    ActionItem,
    ActionKind,
    ActionStatus,
    PostMortem,
    PostMortemStatus,
    Section,
)
from grolla.testing.builders import T0, make_incident

BODY = """# Contexto
O **pedidos-api** devolveu 5xx depois do deploy — “1.4.2” 🚨.
## Detalhes
- pool de conexões esgotado
* rollback em 4 min

Sorte: fora do horário de pico…"""


def incident() -> Incident:
    resolved = replace(
        make_incident(
            state=IncidentState.RESOLVED, severity=Severity.P1, services=("pedidos-api",)
        ),
        resolved_at=T0 + timedelta(minutes=30),
    )
    return replace(resolved, title="Pedidos com 5xx")


def post_mortem(body: str = BODY, actions: tuple[ActionItem, ...] | None = None) -> PostMortem:
    item = ActionItem(
        uuid4(), "Alarme de conexões no RDS", ActionKind.DETECT, "ana", date(2026, 11, 3)
    )
    return PostMortem(
        incident_id=uuid4(),
        status=PostMortemStatus.APPROVED,
        sections=tuple(Section(key, body, "Grolla", T0) for key in SECTION_TITLES),
        actions=(item, replace(item, id=uuid4(), owner=None, due=None, status=ActionStatus.DONE))
        if actions is None
        else actions,
        created_at=T0,
        updated_at=T0,
        status_by="ana",
        status_at=T0 + timedelta(hours=2),
    )


def text_of(pdf: bytes) -> str:
    """O texto dos `Tj` de cada stream de página (as fontes core gravam latin-1 direto)."""
    streams = re.findall(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.DOTALL)
    content = b"".join(zlib.decompress(stream) for stream in streams)
    return "".join(part.decode("latin-1") for part in re.findall(rb"\((.*?)(?<!\\)\) ?Tj", content))


def pages_of(pdf: bytes) -> int:
    return len(re.findall(rb"/Type /Page\b", pdf))


def test_renders_a_pdf_with_the_header_the_sections_and_the_action_plan() -> None:
    pdf = FpdfDocumentRenderer().render(post_mortem(), incident())

    assert pdf.startswith(b"%PDF-")
    text = text_of(pdf)
    for expected in (
        "Post mortem: Pedidos com 5xx",
        "Severidade: P1",
        "Serviços: pedidos-api",
        "Status: Aprovado por ana em",
        "Resumo executivo",
        "Avaliação do agente",
        "Alarme de conexões no RDS",
        "Detectar antes",
        "03/11/2026",
        "Concluído",
        '"1.4.2"',
    ):
        assert expected in text
    assert "**" not in text
    assert "# Contexto" not in text


def test_long_text_breaks_into_pages() -> None:
    long = "\n".join(f"- linha {n} " + "texto " * 30 for n in range(80))

    assert pages_of(FpdfDocumentRenderer().render(post_mortem(long), incident())) > 1


def test_without_actions_says_the_plan_is_empty() -> None:
    pdf = FpdfDocumentRenderer().render(post_mortem(actions=()), incident())

    assert "Nenhum item no plano de ação." in text_of(pdf)


def test_text_outside_latin1_is_replaced_instead_of_failing() -> None:
    assert latin1("“ação” — ok… 🚨 → x") == '"ação" - ok... ? -> x'


def test_fpdf_failure_becomes_render_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_self: FPDF) -> None:
        raise FPDFException("sem página")

    monkeypatch.setattr(FPDF, "add_page", broken)

    with pytest.raises(RenderError, match="sem página"):
        FpdfDocumentRenderer().render(post_mortem(), incident())


class TestFpdfDocumentRendererContract(DocumentRendererContract):
    @pytest.fixture
    def renderer(self) -> DocumentRenderer:
        return FpdfDocumentRenderer()

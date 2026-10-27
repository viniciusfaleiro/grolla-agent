"""PDF do post mortem com fpdf2 (puro Python, sem dependências de sistema).

A4, fonte core Helvetica: o texto vai em latin-1, que cobre os acentos do português; aspas
curvas, travessões e reticências viram o equivalente ASCII, e o resto que não couber (emoji)
vira "?". Nunca falha por causa de um caractere.

O documento tem o cabeçalho (título do incidente, severidade, serviços, status e datas), as
seções na ordem do template, com o markdown simples convertido (títulos `#`, listas `- ` e o
negrito `**` removido), o plano de ação como tabela logo depois da seção "Plano de ação" e os
campos da avaliação do time logo depois da seção "Avaliação do agente".
"""

from datetime import UTC, datetime

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.errors import FPDFException

from grolla.application.post_mortem import RenderError
from grolla.domain.incidents import Incident
from grolla.domain.post_mortem import (
    NONE,
    SECTION_TITLES,
    ActionItem,
    ActionKind,
    ActionStatus,
    AgentEvaluation,
    PostMortem,
    PostMortemStatus,
    SectionKey,
)

FONT = "Helvetica"
LINE = 5.0  # altura da linha do corpo, em mm

STATUS_LABELS = {
    PostMortemStatus.DRAFT: "Rascunho",
    PostMortemStatus.REVIEW: "Em revisão",
    PostMortemStatus.APPROVED: "Aprovado",
    PostMortemStatus.CLOSED: "Encerrado",
}
KIND_LABELS = {
    ActionKind.PREVENT: "Prevenir",
    ActionKind.DETECT: "Detectar antes",
    ActionKind.MITIGATE: "Mitigar mais rápido",
}
ACTION_STATUS_LABELS = {
    ActionStatus.OPEN: "Aberto",
    ActionStatus.DONE: "Concluído",
    ActionStatus.ACCEPTED: "Aceito",
}

_ASCII = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\u2022": "-",
        "\u2192": "->",
    }
)


def latin1(text: str) -> str:
    """O texto que a fonte core aceita: equivalentes ASCII e "?" para o que não houver."""
    return text.translate(_ASCII).encode("latin-1", "replace").decode("latin-1")


class FpdfDocumentRenderer:
    def render(self, pm: PostMortem, incident: Incident) -> bytes:
        try:
            pdf = FPDF(format="A4")
            pdf.set_title(latin1(f"Post mortem: {incident.title}"))
            pdf.set_auto_page_break(auto=True, margin=15)
            pdf.add_page()
            _header(pdf, pm, incident)
            bodies = {section.key: section.body for section in pm.sections}
            for key, title in SECTION_TITLES.items():
                _heading(pdf, title, 13)
                _markdown(pdf, bodies.get(key, ""))
                if key is SectionKey.ACTIONS:
                    _actions(pdf, pm.actions)
                if key is SectionKey.AGENT:
                    _evaluation(pdf, pm.evaluation)
            return bytes(pdf.output())
        except FPDFException as error:
            raise RenderError(f"falha ao gerar o PDF: {error}") from error


def _header(pdf: FPDF, pm: PostMortem, incident: Incident) -> None:
    pdf.set_font(FONT, "B", 16)
    _line(pdf, f"Post mortem: {incident.title}", 8)
    pdf.set_font(FONT, size=10)
    status = STATUS_LABELS[pm.status]
    if pm.status_at is not None:
        status += f" por {pm.status_by} em {_when(pm.status_at)}"
    resolved = _when(incident.resolved_at) if incident.resolved_at else "-"
    for text in (
        f"Severidade: {incident.severity.value}",
        f"Serviços: {', '.join(incident.services)}",
        f"Status: {status}",
        f"Detectado em {_when(incident.detected_at)}; resolvido em {resolved}",
    ):
        _line(pdf, text)


def _heading(pdf: FPDF, title: str, size: int) -> None:
    pdf.ln(3)
    pdf.set_font(FONT, "B", size)
    _line(pdf, title, size * 0.5)
    pdf.set_font(FONT, size=10)


def _markdown(pdf: FPDF, body: str) -> None:
    """Markdown simples: títulos viram negrito, listas ganham recuo e o negrito sai."""
    for raw in body.replace("**", "").splitlines():
        text = raw.strip()
        if not text:
            pdf.ln(2)
        elif text.startswith("#"):
            _heading(pdf, text.lstrip("#").strip(), 11)
        elif text.startswith(("- ", "* ")):
            pdf.set_x(pdf.l_margin + 4)
            _line(pdf, f"- {text[2:]}")
        else:
            _line(pdf, text)


def _actions(pdf: FPDF, actions: tuple[ActionItem, ...]) -> None:
    pdf.ln(2)
    if not actions:
        _line(pdf, "Nenhum item no plano de ação.")
        return
    header = ("Descrição", "Tipo", "Dono", "Prazo", "Situação")
    with pdf.table(col_widths=(70, 30, 30, 22, 22)) as table:
        for cells in (
            header,
            *(
                (
                    a.description,
                    KIND_LABELS[a.kind],
                    a.owner or "-",
                    a.due.strftime("%d/%m/%Y") if a.due else "-",
                    ACTION_STATUS_LABELS[a.status],
                )
                for a in actions
            ),
        ):
            row = table.row()
            for cell in cells:
                row.cell(latin1(cell))


def _answer(value: bool | None) -> str:
    return "sem resposta" if value is None else "sim" if value else "não"


def _evaluation(pdf: FPDF, evaluation: AgentEvaluation) -> None:
    pdf.ln(2)
    minutes = NONE if evaluation.minutes_to_right is None else str(evaluation.minutes_to_right)
    _line(pdf, f"Hipótese principal certa: {_answer(evaluation.main_hypothesis_correct)}")
    _line(pdf, f"Minutos até a hipótese certa: {minutes}")
    _line(pdf, f"Memória ajudou: {_answer(evaluation.memory_helped)}")
    if evaluation.author and evaluation.at is not None:
        _line(pdf, f"Avaliado por {evaluation.author} em {_when(evaluation.at)}")


def _line(pdf: FPDF, text: str, height: float = LINE) -> None:
    pdf.multi_cell(0, height, latin1(text), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _when(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%d/%m/%Y %H:%M UTC")

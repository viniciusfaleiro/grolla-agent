"""Apresentação do console: a versão da conversa para o polling e os gráficos em SVG, desenhados
no servidor com a série real da telemetria (o número nunca vem do modelo).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from grolla.domain.chat import ReplyStatus
from grolla.domain.console import ChartRequest, ConsoleMessage
from markupsafe import Markup, escape

SIGNAL_LABELS = {
    "taxa_de_erro": "Taxa de erro",
    "latencia_p99": "Latência p99",
    "vazao": "Vazão",
}
SIGNAL_UNITS = {"taxa_de_erro": "%", "latencia_p99": "ms", "vazao": "req/s"}

CHART_UNAVAILABLE = "gráfico indisponível"
CHART_EMPTY = "sem dados na janela"

CONSOLE_SUGGESTIONS = (
    "O autorizador está saudável?",
    "O que mudou ontem no pedidos-api?",
    "Por que o p99 sobe às 3h?",
)

# Geometria do SVG (unidades do viewBox; o CSS faz ele ocupar a largura da bolha).
WIDTH, HEIGHT = 640, 220
LEFT, RIGHT, TOP, BOTTOM = 64, 16, 12, 30
Y_TICKS = 3  # 0, metade e o topo


def is_waiting(message: ConsoleMessage) -> bool:
    return message.reply_status in (ReplyStatus.PENDING, ReplyStatus.RUNNING)


def console_version(messages: Sequence[ConsoleMessage]) -> str:
    """Muda quando chega mensagem ou uma pergunta deixa de esperar: o polling só troca aí."""
    waiting = sum(1 for message in messages if is_waiting(message))
    return f"{len(messages)}-{waiting}"


def chart_title(chart: ChartRequest) -> str:
    """'Latência p99 do pedidos-api, últimos 60 min'."""
    label = SIGNAL_LABELS.get(chart.signal, chart.signal)
    return f"{label} do {chart.service}, últimos {chart.minutes} min"


def format_value(signal: str, value: float) -> str:
    """'12,5%', '1.240 ms' ou '12,3 req/s'."""
    if signal == "latencia_p99":
        return f"{value:,.0f} ms".replace(",", ".")
    number = f"{value:.1f}".removesuffix(".0").replace(".", ",")
    return f"{number}%" if signal == "taxa_de_erro" else f"{number} req/s"


@dataclass(frozen=True)
class ChartView:
    title: str
    svg: Markup | None  # None: sem série (falha ou janela vazia), com o motivo em `note`
    legend: str = ""
    note: str = ""


def chart_view(chart: ChartRequest, points: Sequence[tuple[datetime, float]] | None) -> ChartView:
    """O gráfico pronto para a página; `points` None é falha da telemetria."""
    title = chart_title(chart)
    if points is None:
        return ChartView(title, None, note=CHART_UNAVAILABLE)
    if not points:
        return ChartView(title, None, note=CHART_EMPTY)
    ordered = sorted(points)
    peak = max(value for _, value in ordered)
    label = SIGNAL_LABELS.get(chart.signal, chart.signal)
    unit = SIGNAL_UNITS.get(chart.signal, "")
    legend = (
        f"{label} ({unit}) · agora {format_value(chart.signal, ordered[-1][1])} · "
        f"máximo {format_value(chart.signal, peak)}"
    )
    return ChartView(title, chart_svg(title, chart.signal, ordered), legend)


def chart_svg(title: str, signal: str, points: Sequence[tuple[datetime, float]]) -> Markup:
    """Linha do sinal no tempo: eixo y de 0 ao máximo com o valor e a unidade, eixo x em HH:MM."""
    start, end = points[0][0], points[-1][0]
    span = (end - start).total_seconds() or 1.0
    top = max(value for _, value in points) * 1.1 or 1.0
    plot_w, plot_h = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM

    def x(at: datetime) -> float:
        return LEFT + plot_w * (at - start).total_seconds() / span

    def y(value: float) -> float:
        return TOP + plot_h * (1 - value / top)

    parts = [
        f'<svg class="chart-svg" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
        f'aria-label="{escape(title)}"><title>{escape(title)}</title>'
    ]
    for tick in range(Y_TICKS):
        value = top * tick / (Y_TICKS - 1)
        ty = y(value)
        parts.append(
            f'<line class="chart-grid" x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{ty:.1f}" '
            f'y2="{ty:.1f}"/><text class="chart-tick" x="{LEFT - 6}" y="{ty + 4:.1f}" '
            f'text-anchor="end">{escape(format_value(signal, value))}</text>'
        )
    times = (start, start + (end - start) / 2, end) if end > start else (start,)
    anchors = ("start", "middle", "end") if end > start else ("middle",)
    for at, anchor in zip(times, anchors, strict=True):
        parts.append(
            f'<text class="chart-tick" x="{x(at):.1f}" y="{HEIGHT - 8}" '
            f'text-anchor="{anchor}">{at.strftime("%H:%M")}</text>'
        )
    line = " ".join(f"{x(at):.1f},{y(value):.1f}" for at, value in points)
    last_at, last = points[-1]
    parts.append(
        f'<polyline class="chart-line" points="{line}"/>'
        f'<circle class="chart-dot" cx="{x(last_at):.1f}" cy="{y(last):.1f}" r="3.5"/></svg>'
    )
    return Markup("".join(parts))  # noqa: S704 — só números e texto escapado


# Sugestões com os serviços que a memória conhece; sem nenhum, as genéricas.
_SERVICE_SUGGESTIONS = (
    "A {} está saudável?",
    "O que mudou no {} hoje?",
    "Mostra a taxa de erro da {} nas últimas 6 h",
)


def console_suggestions(services: Sequence[str]) -> tuple[str, ...]:
    if not services:
        return CONSOLE_SUGGESTIONS
    return tuple(
        text.format(services[n % len(services)]) for n, text in enumerate(_SERVICE_SUGGESTIONS)
    )

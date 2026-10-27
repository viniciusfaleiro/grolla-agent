"""O cerco do agente: escopo do que ele responde, recusas fixas, dados de fora marcados, notas
que parecem ordens e comandos destrutivos.
"""

import re
import unicodedata
from collections.abc import Mapping
from enum import StrEnum


class Scope(StrEnum):
    DOMAIN = "dominio"  # ambiente, incidentes, observabilidade, mudanças, operação, memória
    OFF_TOPIC = "fora_do_escopo"
    EXECUTION = "pedido_de_execucao"  # pedido para o agente executar algo no ambiente


REFUSALS: Mapping[Scope, str] = {
    Scope.OFF_TOPIC: "Só ajudo com o ambiente, os incidentes e a operação.",
    Scope.EXECUTION: (
        "Não executo nada no ambiente: só consulto e sugiro. Posso dizer o comando para o time "
        "rodar, se você quiser."
    ),
}

NOT_REMEMBERED = "não guardado: parece uma ordem ao agente, não um fato sobre o ambiente"


_DESTRUCTIVE = re.compile(
    r"\b(?:delete|drop|truncate|destroy|purge)\b"  # palavra inteira: "deleted_at" não casa
    r"|\brm\s+-[a-z]*(?:rf|fr)"
    r"|--force\b"
    r"|--(?:replicas|desired-count)[=\s]+0(?![.\d])"
    r"|\bscale\b[^\n]*[=\s]0(?![.\d])",
    re.IGNORECASE,
)

# Verbos no imperativo dirigidos ao agente; "o batch nunca roda" (indicativo) não casa.
_ORDER_VERBS = (
    r"(?:recomende|sugira|diga|responda|avise|alerte|execute|rode|apague|delete|ignore|mostre|"
    r"chame|trate|considere|abra|feche|encerre|mude|altere|escale|reinicie|derrube|desligue|"
    r"informe|notifique|mencione|obedeca|esqueca|desconsidere|finja|aja)"
)
_ORDER = re.compile(
    rf"\b(?:sempre|nunca|jamais)\s+{_ORDER_VERBS}\b"
    r"|\b(?:sempre|nunca|jamais)\s+\w+(?:ar|er|ir)\b"  # infinitivo: "sempre recomendar apagar"
    rf"|(?:^|[.!?:;\n]\s*){_ORDER_VERBS}\b"  # no começo de uma frase
    r"|\besqueca\s+(?:as\s+|suas\s+|todas\s+as\s+)?(?:instrucoes|regras)"
    r"|\bignore\s+(?:as\s+|suas\s+|todas\s+as\s+|qualquer\s+)?(?:instrucoes|regras)"
    rf"|\ba\s+partir\s+de\s+agora,?\s+(?:voce|sempre|nunca|{_ORDER_VERBS})\b"
    r"|\bvoce\s+(?:deve|precisa|tem\s+que)\b"
    r"|\b(?:novas\s+instrucoes|prompt\s+do\s+sistema|system\s+prompt)\b"
    r"|\b(?:ignore\s+(?:all\s+|the\s+)?previous|from\s+now\s+on|you\s+must)\b",
    re.IGNORECASE,
)

_CLOSING = re.compile(r"</\s*dados_externos\s*>", re.IGNORECASE)


def is_destructive(command: str) -> bool:
    """O comando apaga, derruba ou zera algo (delete, drop, rm -rf, --replicas=0...)."""
    return _DESTRUCTIVE.search(command) is not None


def _plain(text: str) -> str:
    """Sem acento, para as regras não dependerem de "esqueça" ou "esqueca"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def looks_like_order(text: str) -> bool:
    """O texto é uma ordem ao agente ("sempre recomende...", "ignore...") e não um fato."""
    return _ORDER.search(_plain(text.strip())) is not None


def untrusted(source: str, text: str) -> str:
    """O texto dentro de <dados_externos fonte="...">, sem como fechar o bloco por dentro."""
    inside = _CLOSING.sub("</dados-externos>", text)
    return f'<dados_externos fonte="{source}">\n{inside}\n</dados_externos>'

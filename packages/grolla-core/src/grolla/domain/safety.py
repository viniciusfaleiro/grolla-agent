"""O cerco do agente: escopo do que ele responde, recusas fixas, dados de fora marcados, notas
que parecem ordens e comandos destrutivos.

Contrato do agente cercado: os corpos são do grolla-dominio.
"""

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


def is_destructive(command: str) -> bool:
    """O comando apaga, derruba ou zera algo (delete, drop, rm -rf, --replicas=0...)."""
    raise NotImplementedError


def looks_like_order(text: str) -> bool:
    """O texto é uma ordem ao agente ("sempre recomende...", "ignore...") e não um fato."""
    raise NotImplementedError


def untrusted(source: str, text: str) -> str:
    """O texto dentro de <dados_externos fonte="...">, sem como fechar o bloco por dentro."""
    raise NotImplementedError

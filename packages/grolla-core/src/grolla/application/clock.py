"""Port do relógio: a única fonte de "agora" do núcleo, controlável nos testes."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Fonte do horário atual.

    Contrato: `now()` devolve um datetime com fuso UTC e nunca anda para trás entre duas chamadas.
    """

    def now(self) -> datetime: ...

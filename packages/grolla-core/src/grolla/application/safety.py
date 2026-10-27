"""Limite de mensagens por pessoa no chat do war room e no console.

Contrato do agente cercado: os corpos são do grolla-dominio.
"""

from datetime import datetime, timedelta
from typing import Protocol

WINDOW = timedelta(hours=1)


class MessageCounter(Protocol):
    async def sent_since(self, author: str, since: datetime) -> list[datetime]:
        """Quando o autor mandou cada mensagem desde `since`, no chat e no console juntos."""
        ...


class RateLimitedError(Exception):
    """O autor passou do limite; pode escrever de novo em `retry_at`."""

    def __init__(self, limit: int, retry_at: datetime) -> None:
        super().__init__(f"limite de {limit} mensagens por hora")
        self.limit = limit
        self.retry_at = retry_at


async def ensure_under_limit(
    counter: MessageCounter, author: str, now: datetime, per_hour: int
) -> None:
    """Levanta RateLimitedError quando o autor já mandou `per_hour` mensagens na última hora."""
    raise NotImplementedError

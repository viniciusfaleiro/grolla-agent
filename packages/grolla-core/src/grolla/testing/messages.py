"""Fake do contador de mensagens por pessoa."""

from datetime import datetime


class FakeMessageCounter:
    def __init__(self) -> None:
        self.sent: list[tuple[str, datetime]] = []

    async def sent_since(self, author: str, since: datetime) -> list[datetime]:
        return [at for who, at in self.sent if who == author and at >= since]

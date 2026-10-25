from datetime import UTC, datetime, timedelta


class FakeClock:
    """Relógio parado que só anda quando o teste manda."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)
        if self._now.utcoffset() != timedelta(0):
            raise ValueError("FakeClock precisa começar num horário UTC")

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        if delta < timedelta(0):
            raise ValueError("o relógio não anda para trás")
        self._now += delta

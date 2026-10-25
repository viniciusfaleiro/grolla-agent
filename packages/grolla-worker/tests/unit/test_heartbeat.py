from datetime import datetime

from grolla.testing.clock import FakeClock
from grolla_worker.heartbeat import beat


async def test_beat_records_the_clock_time() -> None:
    clock = FakeClock()
    recorded: list[datetime] = []

    async def record(at: datetime) -> None:
        recorded.append(at)

    await beat(clock, record)

    assert recorded == [clock.now()]

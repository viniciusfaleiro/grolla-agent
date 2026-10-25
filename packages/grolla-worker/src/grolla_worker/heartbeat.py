from collections.abc import Awaitable, Callable
from datetime import datetime

from grolla.application.clock import Clock

RecordHeartbeat = Callable[[datetime], Awaitable[None]]

# Segundos entre heartbeats (cron com campo de segundos).
HEARTBEAT_CRON = "* * * * * */15"


async def beat(clock: Clock, record: RecordHeartbeat) -> None:
    """Grava o horário atual: prova de vida do worker, lida pelo monitoramento e pelo e2e."""
    await record(clock.now())

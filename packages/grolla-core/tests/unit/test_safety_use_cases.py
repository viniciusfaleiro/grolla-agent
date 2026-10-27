"""Limite de mensagens por pessoa no chat do war room e no console."""

from datetime import timedelta

import pytest
from grolla.application.safety import WINDOW, RateLimitedError, ensure_under_limit
from grolla.testing.builders import T0
from grolla.testing.messages import FakeMessageCounter

NOW = T0 + timedelta(hours=5)


def counter(*minutes_ago: int, author: str = "Ana") -> FakeMessageCounter:
    fake = FakeMessageCounter()
    fake.sent += [(author, NOW - timedelta(minutes=m)) for m in minutes_ago]
    return fake


async def test_below_the_limit_passes() -> None:
    await ensure_under_limit(counter(1, 2), "Ana", NOW, per_hour=3)


async def test_at_the_limit_is_refused_until_the_oldest_leaves_the_window() -> None:
    with pytest.raises(RateLimitedError) as raised:
        await ensure_under_limit(counter(5, 50, 30), "Ana", NOW, per_hour=3)

    assert raised.value.limit == 3
    assert raised.value.retry_at == NOW - timedelta(minutes=50) + WINDOW
    assert str(raised.value) == "limite de 3 mensagens por hora"


async def test_retry_at_frees_exactly_one_message_when_above_the_limit() -> None:
    with pytest.raises(RateLimitedError) as raised:
        await ensure_under_limit(counter(10, 20, 40, 55), "Ana", NOW, per_hour=2)

    assert raised.value.retry_at == NOW - timedelta(minutes=20) + WINDOW


async def test_old_messages_and_other_authors_do_not_count() -> None:
    fake = counter(61, 90, 120)
    fake.sent += [("Bia", NOW - timedelta(minutes=1))] * 5

    await ensure_under_limit(fake, "Ana", NOW, per_hour=1)

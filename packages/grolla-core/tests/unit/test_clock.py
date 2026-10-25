from datetime import UTC, datetime, timedelta, timezone

import pytest
from contracts.clock_contract import ClockContract
from grolla.adapters.system_clock import SystemClock
from grolla.application.clock import Clock
from grolla.testing.clock import FakeClock


class TestSystemClockContract(ClockContract):
    @pytest.fixture
    def clock(self) -> Clock:
        return SystemClock()


class TestFakeClockContract(ClockContract):
    @pytest.fixture
    def clock(self) -> Clock:
        return FakeClock()


def test_fake_clock_only_moves_when_told() -> None:
    start = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)
    clock = FakeClock(start)

    assert clock.now() == start
    clock.advance(timedelta(minutes=5))
    assert clock.now() == start + timedelta(minutes=5)


def test_fake_clock_refuses_to_go_back() -> None:
    with pytest.raises(ValueError, match="para trás"):
        FakeClock().advance(timedelta(seconds=-1))


def test_fake_clock_refuses_non_utc_start() -> None:
    with pytest.raises(ValueError, match="UTC"):
        FakeClock(datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=-3))))

"""Suíte de contrato do port Clock: todo adapter e todo fake herdam desta classe."""

from datetime import timedelta

import pytest
from grolla.application.clock import Clock


class ClockContract:
    @pytest.fixture
    def clock(self) -> Clock:
        raise NotImplementedError

    def test_now_is_utc(self, clock: Clock) -> None:
        assert clock.now().utcoffset() == timedelta(0)

    def test_now_never_goes_back(self, clock: Clock) -> None:
        first = clock.now()
        second = clock.now()
        assert second >= first

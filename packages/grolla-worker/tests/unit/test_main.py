import pytest
from grolla_worker.main import main


def test_refuses_to_start_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROLLA_DATABASE_URL", raising=False)

    with pytest.raises(SystemExit, match="GROLLA_DATABASE_URL"):
        main()

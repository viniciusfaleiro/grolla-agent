import pytest
from grolla.adapters.postgres.urls import sqlalchemy_url


@pytest.mark.parametrize("scheme", ["postgresql", "postgres"])
def test_converts_libpq_url_to_psycopg_dialect(scheme: str) -> None:
    assert sqlalchemy_url(f"{scheme}://u:p@db:5432/x") == "postgresql+psycopg://u:p@db:5432/x"


@pytest.mark.parametrize("url", ["mysql://u@db/x", "db:5432/x"])
def test_rejects_non_postgres_urls(url: str) -> None:
    with pytest.raises(ValueError, match=r"postgresql://"):
        sqlalchemy_url(url)

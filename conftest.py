"""Configuração de testes compartilhada por todos os pacotes do workspace.

- Todo teste roda com a rede bloqueada (pytest-socket), exceto os de `integration/` e o runner
  do e2e (`e2e/test_*.py`).
- `database_url` sobe um Postgres real por sessão para os testes de integração.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from testcontainers.community.postgres import PostgresContainer

POSTGRES_IMAGE = "postgres:16-alpine"


def needs_network(path: Path) -> bool:
    return "integration" in path.parts or path.parent.name == "e2e"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if not needs_network(Path(item.path)):
            item.add_marker(pytest.mark.disable_socket)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer(POSTGRES_IMAGE, driver=None) as postgres:
        yield postgres.get_connection_url()

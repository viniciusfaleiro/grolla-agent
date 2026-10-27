"""O embedder da memória com RAG no bootstrap: desligado sem `memory.rag`, e modelo ausente na
imagem impede o worker de subir com a mensagem apontando o campo.
"""

from pathlib import Path

import pytest
from grolla.bootstrap import TeamConfig, WorkerSettings
from grolla.bootstrap.container import _embedder  # pyright: ignore[reportPrivateUsage] — só o ponto testado
from grolla.bootstrap.settings import ConfigError
from pydantic import SecretStr

SETTINGS = WorkerSettings(
    database_url="postgresql://grolla:grolla@localhost:5433/grolla",
    config_file=Path("/etc/grolla/grolla.yaml"),
    teams_webhook_url=SecretStr("http://chat"),
    anthropic_api_key=SecretStr("chave"),
    azure_devops_token=SecretStr("token"),
)


def test_no_rag_section_means_no_embedder() -> None:
    config = TeamConfig(team="t", page_base_url="http://grolla")

    assert _embedder(SETTINGS, config) is None


def test_missing_model_stops_the_worker_pointing_at_the_field() -> None:
    config = TeamConfig.model_validate(
        {"team": "t", "page_base_url": "http://grolla", "memory": {"rag": {"size": "large"}}}
    )

    with pytest.raises(ConfigError, match=r"memory\.rag\.size: .*multilingual-e5-large"):
        _embedder(SETTINGS, config)  # o cache padrão /opt/grolla/models não existe nos testes

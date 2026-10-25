import uvicorn
from grolla.bootstrap import ConfigError, load_settings, prepare

from grolla_api.app import create_app

# Dentro do container a api precisa aceitar conexões de fora do próprio processo.
HOST = "0.0.0.0"  # noqa: S104
PORT = 8000


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as error:
        raise SystemExit(f"grolla-api: {error}") from error
    container = prepare(settings)
    app = create_app(container.database.ping)
    uvicorn.run(app, host=HOST, port=PORT, log_level=settings.log_level.lower())

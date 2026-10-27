import uvicorn
from grolla.bootstrap import ApiContainer, ConfigError, prepare_api

from grolla_api.app import Services, create_app

# Dentro do container a api precisa aceitar conexões de fora do próprio processo.
HOST = "0.0.0.0"  # noqa: S104
PORT = 8000


def services_from(container: ApiContainer) -> Services:
    return Services(
        database_is_up=container.database.ping,
        latest_heartbeat=container.database.latest_heartbeat,
        ingress=container.ingress,
        repository=container.repository,
        intake=container.intake,
        clock=container.clock,
        memory=container.memory,
        studies=container.studies,
        learning_queue=container.learning_queue,
        study_enabled=container.config.memory_policy().study is not None,
        post_mortems=container.post_mortems,
        renderer=container.renderer,
        metrics=container.metrics,
        impact=container.impact_policy,
        console=container.console if container.console_policy is not None else None,
        telemetry=container.telemetry,
    )


def main() -> None:
    try:
        container = prepare_api()
    except ConfigError as error:
        raise SystemExit(f"grolla-api: {error}") from error
    app = create_app(services_from(container))
    log_level = container.settings.log_level.lower()
    uvicorn.run(app, host=HOST, port=PORT, log_level=log_level)

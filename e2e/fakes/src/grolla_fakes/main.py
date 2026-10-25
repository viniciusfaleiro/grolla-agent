import uvicorn

from grolla_fakes.app import create_app

# Dentro do container os fakes precisam aceitar conexões dos outros serviços do compose.
HOST = "0.0.0.0"  # noqa: S104
PORT = 9000


def main() -> None:
    uvicorn.run(create_app(), host=HOST, port=PORT)

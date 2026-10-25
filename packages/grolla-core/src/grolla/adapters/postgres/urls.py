def sqlalchemy_url(database_url: str) -> str:
    """Converte a URL libpq (`postgresql://...`) para o dialeto psycopg 3 do SQLAlchemy."""
    scheme, separator, rest = database_url.partition("://")
    if not separator or scheme not in {"postgres", "postgresql"}:
        raise ValueError("a URL do banco precisa começar com postgresql://")
    return f"postgresql+psycopg://{rest}"

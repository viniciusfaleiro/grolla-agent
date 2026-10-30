"""Gera os arquivos de uma instância a partir do arquivo de implantação (deploy/implantar.sh).

    python scripts/configurar_instancia.py <implantacao.yaml> <pasta de saída>

Escreve `.env` (variáveis do compose), `grolla.env` (segredos do agente), `grolla.yaml` (a
configuração do time, validada como o agente valida), e, quando pedidos, `signoz-mcp.env` e
`Caddyfile`. Na saída padrão, um resumo `chave=valor` para o script de implantação. O hash da
senha da página vem de GROLLA_WEB_HASH (gerado pelo Caddy). Erro de configuração: código 1 e
a lista do que corrigir.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml
from grolla.bootstrap.settings import ConfigError
from grolla.bootstrap.team_config import load_team_config

FAKE_TEAMS_URL = "http://fakes:9000/teams/webhook"
REQUIRED_SECRETS = (
    "GROLLA_ALERTS_WEBHOOK_USER",
    "GROLLA_ALERTS_WEBHOOK_PASSWORD",
    "GROLLA_AZURE_DEVOPS_TOKEN",
)
CONTAINER_KUBECONFIG = "/etc/grolla/kubeconfig.yaml"
# Lidos pelo agente, que roda com outro usuário no container; não têm segredo.
PUBLIC_FILES = frozenset({"grolla.yaml"})
PUBLIC_MODE = 0o644


class DeployConfigError(Exception):
    """O arquivo de implantação tem problemas; a mensagem lista todos."""


@dataclass
class Instance:
    """O que a implantação precisa: os arquivos a escrever e o resumo para o script."""

    files: dict[str, str] = field(default_factory=dict[str, str])
    summary: dict[str, str] = field(default_factory=dict[str, str])


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value: object = raw.get(name) or {}
    if not isinstance(value, dict):
        raise DeployConfigError(f"{name}: precisa ser um mapa")
    return cast(dict[str, Any], value)


def _env_line(name: str, value: object) -> str:
    text = str(value)
    if "\n" in text or "'" in text:
        raise DeployConfigError(f"{name}: valor com quebra de linha ou aspas simples")
    return f"{name}='{text}'"  # aspas simples: o compose não interpola o valor


def _caddyfile(https: dict[str, Any], web_hash: str) -> str:
    user = https.get("usuario", "grolla")
    site = f"""(grolla) {{
\t@aberto path /webhooks/* /health /health/*
\thandle @aberto {{
\t\treverse_proxy api:8000
\t}}
\thandle {{
\t\tbasic_auth {{
\t\t\t{user} {web_hash}
\t\t}}
\t\treverse_proxy api:8000
\t}}
}}

{https["host"]} {{
\timport grolla
}}
"""
    cloudfront = https.get("cloudfront")
    if cloudfront:
        site += f"""
http://{cloudfront["origem"]} {{
\t@cloudfront header X-Grolla-Origin {cloudfront["segredo"]}
\thandle @cloudfront {{
\t\timport grolla
\t}}
\trespond 403
}}
"""
    return "# Gerado pelo deploy/implantar.sh a partir do arquivo de implantação.\n" + site


def _problems(raw: dict[str, Any], web_hash: str) -> list[str]:
    """Tudo o que falta ou está errado no arquivo, de uma vez."""
    problems: list[str] = []
    base, https, mcp = (
        _section(raw, "instancia"),
        _section(raw, "https"),
        _section(raw, "signoz_mcp"),
    )
    secrets, team = _section(raw, "segredos"), _section(raw, "grolla")
    if not base.get("senha_banco"):
        problems.append(
            "instancia.senha_banco: obrigatória (vale na primeira subida, quando o banco é criado)"
        )
    if not team:
        problems.append("grolla: falta a configuração do time")
    if not _section(raw, "teams").get("webhook_url"):
        problems.append('teams.webhook_url: a URL do Workflow do canal, ou "fake" no laboratório')
    required: list[str] = list(REQUIRED_SECRETS)
    if _section(team, "llm").get("provider", "anthropic") == "anthropic":
        required.append("GROLLA_ANTHROPIC_API_KEY")
    problems += [f"segredos.{name}: obrigatório" for name in required if not secrets.get(name)]
    if https and not (https.get("host") and https.get("senha")):
        problems.append("https: precisa de host e senha")
    if https and not web_hash:
        problems.append("https: falta o hash da senha (GROLLA_WEB_HASH)")
    if mcp and not (mcp.get("url") and mcp.get("api_key")):
        problems.append("signoz_mcp: precisa de url e api_key")
    return problems


def build(raw: dict[str, Any], web_hash: str = "") -> Instance:
    """Monta os arquivos da instância; levanta DeployConfigError com todos os problemas."""
    problems = _problems(raw, web_hash)
    if problems:
        raise DeployConfigError("\n".join(problems))
    instance = Instance()
    base = _section(raw, "instancia")
    https = _section(raw, "https")
    teams = _section(raw, "teams")
    mcp = _section(raw, "signoz_mcp")
    secrets = {str(k): v for k, v in _section(raw, "segredos").items()}
    team = _section(raw, "grolla")

    profiles: list[str] = []
    if teams["webhook_url"] == "fake":
        profiles.append("teams-fake")
        secrets["GROLLA_TEAMS_WEBHOOK_URL"] = FAKE_TEAMS_URL
    else:
        secrets["GROLLA_TEAMS_WEBHOOK_URL"] = teams["webhook_url"]
    if raw.get("kubeconfig"):
        secrets["KUBECONFIG"] = CONTAINER_KUBECONFIG
    if mcp:
        profiles.append("signoz-mcp")
        instance.files["signoz-mcp.env"] = (
            "\n".join(
                [_env_line("SIGNOZ_URL", mcp["url"]), _env_line("SIGNOZ_API_KEY", mcp["api_key"])]
            )
            + "\n"
        )
    if https:
        profiles.append("https")
        instance.files["Caddyfile"] = _caddyfile(https, web_hash)
        team.setdefault("page_base_url", f"https://{https['host']}")

    port = int(base.get("porta", 8000))
    compose_env = {
        "COMPOSE_PROFILES": ",".join(profiles),
        "GROLLA_DB_PASSWORD": base["senha_banco"],
        "GROLLA_API_PORT": port,
    }
    header = "# Gerado pelo deploy/implantar.sh a partir do arquivo de implantação.\n"
    instance.files[".env"] = (
        header + "\n".join(_env_line(k, v) for k, v in compose_env.items()) + "\n"
    )
    instance.files["grolla.env"] = (
        header + "\n".join(_env_line(k, v) for k, v in sorted(secrets.items())) + "\n"
    )
    instance.files["grolla.yaml"] = header + yaml.safe_dump(
        team, allow_unicode=True, sort_keys=False
    )

    memory = _section(team, "memory")
    rag: object = base.get("rag") or _section(memory, "rag").get("size") or "small large"
    instance.summary = {
        "pasta": str(base.get("pasta", "~/grolla")),
        "porta": str(port),
        "perfis": ",".join(profiles) or "nenhum",
        "url": str(team.get("page_base_url", f"http://<host>:{port}")),
        "rag": str(rag),
        "kubeconfig": str(raw.get("kubeconfig", "")),
        "time": str(team.get("team", "")),
    }
    return instance


def _read(source: Path) -> dict[str, Any]:
    raw: object = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise DeployConfigError("o arquivo precisa ser um mapa YAML")
    return cast(dict[str, Any], raw)


def main(argv: list[str]) -> int:
    source, out = Path(argv[1]), Path(argv[2])
    try:
        instance = build(_read(source), os.environ.get("GROLLA_WEB_HASH", ""))
        for name, content in instance.files.items():
            path = out / name
            path.write_text(content, encoding="utf-8")
            path.chmod(PUBLIC_MODE if name in PUBLIC_FILES else 0o600)
        load_team_config(out / "grolla.yaml")  # a mesma validação do agente na subida
    except (OSError, yaml.YAMLError, DeployConfigError, ConfigError) as error:
        print(error, file=sys.stderr)
        return 1
    for key, value in instance.summary.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

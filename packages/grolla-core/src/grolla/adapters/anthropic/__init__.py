"""Anthropic (Claude): o modelo de linguagem da investigação, com laço de ferramentas manual."""

from dataclasses import dataclass

from grolla.application.investigation import InvestigationBrief, TokenBudget, Toolbox
from grolla.domain.analysis import AnalysisDraft


@dataclass(frozen=True)
class ModelSettings:
    """Parâmetros da etapa, lidos do YAML do time (`llm.stages.<etapa>`)."""

    model: str
    max_tokens: int
    effort: str | None = None


class ClaudeLanguageModel:
    def __init__(self, api_key: str, settings: ModelSettings, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._settings = settings
        self._base_url = base_url  # só o e2e aponta para o fake

    async def investigate(
        self, brief: InvestigationBrief, toolbox: Toolbox, budget: TokenBudget
    ) -> AnalysisDraft:
        raise NotImplementedError

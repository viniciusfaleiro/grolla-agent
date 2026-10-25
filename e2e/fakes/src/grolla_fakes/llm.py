"""Messages API fake (`POST /anthropic/v1/messages`), no formato da API da Anthropic.

O modo vem de `POST /_control/llm {"mode": ...}`:

- `completa` (padrão): sem resultados de ferramenta na conversa, pede `query_metric`,
  `recent_deploys` e `search_logs` (tool_use); com eles, devolve o JSON final da análise
  citando trechos dos resultados;
- `fora_do_ar`: 529 overloaded, como a API sobrecarregada;
- `lenta`: espera além do timeout da investigação do e2e antes de responder.

Os argumentos de cada ferramenta saem do `input_schema` recebido: enum vira o primeiro valor,
booleano vira true e `service` vira o primeiro serviço da linha "Serviços: ..." do brief.
"""

import asyncio
import json
import re
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from grolla_fakes.state import FakesState, RecordedRequest

MESSAGES_PATH = "/anthropic/v1/messages"
FIRST_TOOLS = ("query_metric", "recent_deploys", "search_logs")
SLOW_SECONDS = 60.0  # bem acima do investigation.timeout_seconds do e2e
UNKNOWN_SERVICE = "servico-desconhecido"
_SERVICES_LINE = re.compile(r"Serviços?:\s*([^\n,]+)")


class LlmMode(StrEnum):
    COMPLETE = "completa"
    DOWN = "fora_do_ar"
    SLOW = "lenta"


class LlmControl(BaseModel):
    mode: LlmMode


def llm_router(state: FakesState) -> APIRouter:
    router = APIRouter()

    @router.post("/_control/llm", status_code=204)
    async def set_mode(control: LlmControl) -> None:  # pyright: ignore[reportUnusedFunction]
        state.llm_mode = control.mode

    @router.post(MESSAGES_PATH)
    async def messages(request: Request) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        body: dict[str, Any] = await request.json()
        mode = LlmMode(state.llm_mode)
        status = 529 if mode is LlmMode.DOWN else 200
        state.requests.append(
            RecordedRequest(method="POST", path=MESSAGES_PATH, body=body, status=status)
        )
        if mode is LlmMode.DOWN:
            # retry-after-ms curto: o SDK refaz logo e desiste, sem gastar o timeout do e2e.
            return JSONResponse(
                {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
                status_code=529,
                headers={"retry-after-ms": "10"},
            )
        if mode is LlmMode.SLOW:
            await asyncio.sleep(SLOW_SECONDS)
        return JSONResponse(reply(body))

    return router


def reply(body: dict[str, Any]) -> dict[str, Any]:
    """Resposta do modo `completa` para a conversa recebida."""
    messages: list[dict[str, Any]] = body["messages"]
    results = _tool_results(messages)
    if not results:
        return _message(body, _tool_calls(body), "tool_use")
    return _message(body, [{"type": "text", "text": json.dumps(_draft(results))}], "end_turn")


def _message(body: dict[str, Any], content: list[dict[str, Any]], stop: str) -> dict[str, Any]:
    return {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 1200,
            "output_tokens": 300,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 800,
        },
    }


def _tool_calls(body: dict[str, Any]) -> list[dict[str, Any]]:
    service = _service(body["messages"][0])
    tools = {tool["name"]: tool for tool in body.get("tools", [])}
    return [
        {
            "type": "tool_use",
            "id": f"toolu_{index}",
            "name": name,
            "input": _arguments(tools[name]["input_schema"], service),
        }
        for index, name in enumerate(FIRST_TOOLS)
        if name in tools
    ]


def _arguments(schema: dict[str, Any], service: str) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    properties: dict[str, Any] = schema.get("properties", {})
    for name in schema.get("required", []):
        prop: dict[str, Any] = properties.get(name, {})
        if "enum" in prop:
            arguments[name] = prop["enum"][0]
        elif prop.get("type") == "boolean":
            arguments[name] = True
        else:
            arguments[name] = service if name == "service" else "erro"
    return arguments


def _service(first: dict[str, Any]) -> str:
    content = first["content"]
    text = content if isinstance(content, str) else " ".join(_texts(content))
    match = _SERVICES_LINE.search(text)
    return match.group(1).strip() if match else UNKNOWN_SERVICE


def _texts(blocks: list[dict[str, Any]]) -> list[str]:
    return [block["text"] for block in blocks if block.get("type") == "text"]


def _tool_results(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Nome da ferramenta → texto do resultado, de todas as rodadas da conversa."""
    names: dict[str, str] = {}
    results: dict[str, str] = {}
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            continue
        for block in content:
            if block.get("type") == "tool_use":
                names[block["id"]] = block["name"]
            elif block.get("type") == "tool_result":
                results[names.get(block["tool_use_id"], "?")] = _result_text(block)
    return results


def _result_text(block: dict[str, Any]) -> str:
    content = block.get("content", "")
    return content if isinstance(content, str) else " ".join(_texts(content))


def _draft(results: dict[str, str]) -> dict[str, Any]:
    deploys = results.get("recent_deploys", "sem dados de deploy")
    metric = results.get("query_metric", "sem dados de métrica")
    logs = results.get("search_logs", "sem logs")
    return {
        "summary": f"A taxa de erro subiu logo depois de um deploy. {_excerpt(metric)}",
        "hypotheses": [
            {
                "title": "Deploy recente introduziu a falha",
                "confidence": 0.7,
                "evidence": [
                    {"text": _excerpt(deploys), "url": None},
                    {"text": _excerpt(metric), "url": None},
                ],
            },
            {
                "title": "Dependência externa instável",
                "confidence": 0.3,
                "evidence": [{"text": _excerpt(logs), "url": None}],
            },
        ],
        "plan": [
            "Comparar a taxa de erro antes e depois do último deploy",
            "Avaliar o rollback do último deploy com o time dono do serviço",
        ],
    }


def _excerpt(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"

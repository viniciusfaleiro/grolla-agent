"""Messages API fake (`POST /anthropic/v1/messages`), no formato da API da Anthropic.

Atende as duas fases da investigação, reconhecidas pelo schema em `output_config.format`: com a
propriedade `verdicts`, é a validação; senão, a análise inicial. O modo de cada fase vem de
`POST /_control/llm {"mode": ..., "verification_mode": ...}`. Os dois campos são opcionais, o
padrão é `completa`, e o `/_control/reset` volta ao padrão.

- `completa`: sem resultados de ferramenta na conversa, pede ferramentas (tool_use): na análise
  inicial `query_metric`, `recent_deploys` e `search_logs`; na validação `query_metric` e
  `recent_deploys`. Com os resultados, devolve o JSON final citando trechos deles. Na validação,
  a primeira hipótese da análise sai confirmada, a segunda rejeitada, a causa raiz cita o
  serviço e o plano é "Fazer rollback do <serviço> para o deploy anterior";
- `inconclusiva` (só na validação): o mesmo roteiro, mas todas as hipóteses inconclusivas, sem
  causa raiz, sem plano e com o que faltou;
- `fora_do_ar`: 529 overloaded, como a API sobrecarregada;
- `lenta`: espera além do timeout do e2e antes de responder (a fase termina parcial).

Os argumentos de cada ferramenta saem do `input_schema` recebido: enum vira o primeiro valor,
booleano vira true e `service` vira o primeiro serviço da linha "Serviços: ..." da mensagem.
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
VERIFICATION_TOOLS = ("query_metric", "recent_deploys")
SLOW_SECONDS = 60.0  # bem acima dos timeouts de análise e de validação do e2e
UNKNOWN_SERVICE = "servico-desconhecido"
FALLBACK_HYPOTHESIS = "Hipótese da análise inicial"
_SERVICES_LINE = re.compile(r"Serviços?:\s*([^\n,]+)")
_HYPOTHESES = "Hipóteses da análise inicial:"
_HYPOTHESIS_LINE = re.compile(r"^- (.+?) \(confiança [^)]*\)$")


class LlmMode(StrEnum):
    COMPLETE = "completa"
    DOWN = "fora_do_ar"
    SLOW = "lenta"
    INCONCLUSIVE = "inconclusiva"  # só faz diferença na validação


class LlmControl(BaseModel):
    mode: LlmMode = LlmMode.COMPLETE
    verification_mode: LlmMode = LlmMode.COMPLETE


def llm_router(state: FakesState) -> APIRouter:
    router = APIRouter()

    @router.post("/_control/llm", status_code=204)
    async def set_mode(control: LlmControl) -> None:  # pyright: ignore[reportUnusedFunction]
        state.llm_mode = control.mode
        state.llm_verification_mode = control.verification_mode

    @router.post(MESSAGES_PATH)
    async def messages(request: Request) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        body: dict[str, Any] = await request.json()
        verifying = is_verification(body)
        mode = LlmMode(state.llm_verification_mode if verifying else state.llm_mode)
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
        if verifying:
            return JSONResponse(verification_reply(body, mode is LlmMode.INCONCLUSIVE))
        return JSONResponse(reply(body))

    return router


def is_verification(body: dict[str, Any]) -> bool:
    """A validação pede o schema com `verdicts`; a análise inicial, o do rascunho."""
    output: dict[str, Any] = body.get("output_config") or {}
    output_format: dict[str, Any] = output.get("format") or {}
    schema: dict[str, Any] = output_format.get("schema") or {}
    return "verdicts" in (schema.get("properties") or {})


def reply(body: dict[str, Any]) -> dict[str, Any]:
    """Resposta do modo `completa` da análise inicial para a conversa recebida."""
    results = _tool_results(body["messages"])
    if not results:
        return _message(body, _tool_calls(body, FIRST_TOOLS), "tool_use")
    return _message(body, [{"type": "text", "text": json.dumps(_draft(results))}], "end_turn")


def verification_reply(body: dict[str, Any], inconclusive: bool) -> dict[str, Any]:
    """Resposta da validação: ferramentas primeiro, depois os vereditos."""
    results = _tool_results(body["messages"])
    if not results:
        return _message(body, _tool_calls(body, VERIFICATION_TOOLS), "tool_use")
    first = _first_text(body["messages"][0])
    service = _service(first)
    hypotheses = _hypotheses(first)
    result = (
        _inconclusive(hypotheses, results)
        if inconclusive
        else _confirmed(hypotheses, results, service)
    )
    return _message(body, [{"type": "text", "text": json.dumps(result)}], "end_turn")


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


def _tool_calls(body: dict[str, Any], names: tuple[str, ...]) -> list[dict[str, Any]]:
    service = _service(_first_text(body["messages"][0]))
    tools = {tool["name"]: tool for tool in body.get("tools", [])}
    return [
        {
            "type": "tool_use",
            "id": f"toolu_{index}",
            "name": name,
            "input": _arguments(tools[name]["input_schema"], service),
        }
        for index, name in enumerate(names)
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


def _first_text(first: dict[str, Any]) -> str:
    content = first["content"]
    return content if isinstance(content, str) else " ".join(_texts(content))


def _service(text: str) -> str:
    match = _SERVICES_LINE.search(text)
    return match.group(1).strip() if match else UNKNOWN_SERVICE


def _hypotheses(text: str) -> list[str]:
    """Os títulos da seção "Hipóteses da análise inicial:" da mensagem da validação."""
    _, _, section = text.partition(_HYPOTHESES)
    titles: list[str] = []
    for line in section.splitlines():
        match = _HYPOTHESIS_LINE.match(line)
        if match:
            titles.append(match.group(1))
        elif titles and not line.startswith(" "):
            break
    return titles or [FALLBACK_HYPOTHESIS]


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


def _evidence(results: dict[str, str]) -> list[dict[str, Any]]:
    deploys = results.get("recent_deploys", "sem dados de deploy")
    metric = results.get("query_metric", "sem dados de métrica")
    return [{"text": _excerpt(deploys), "url": None}, {"text": _excerpt(metric), "url": None}]


def _confirmed(hypotheses: list[str], results: dict[str, str], service: str) -> dict[str, Any]:
    first, *others = hypotheses
    verdicts = [
        {
            "hypothesis": first,
            "verdict": "confirmada",
            "reasoning": "A taxa de erro mudou de patamar logo depois do último deploy.",
            "evidence": _evidence(results),
        },
        *(
            {
                "hypothesis": title,
                "verdict": "rejeitada",
                "reasoning": "Nenhuma evidência nas ferramentas sustenta esta hipótese.",
                "evidence": [{"text": _excerpt(results.get("query_metric", "")), "url": None}],
            }
            for title in others
        ),
    ]
    return {
        "verdicts": verdicts,
        "root_cause": f"O último deploy do {service} introduziu a falha que elevou a taxa de erro",
        "actions": [
            {
                "action": f"Fazer rollback do {service} para o deploy anterior",
                "target": service,
                "expected": "A taxa de erro volta ao patamar anterior ao deploy",
                "link": None,
            }
        ],
        "missing": "",
    }


def _inconclusive(hypotheses: list[str], results: dict[str, str]) -> dict[str, Any]:
    return {
        "verdicts": [
            {
                "hypothesis": title,
                "verdict": "inconclusiva",
                "reasoning": "Os dados das ferramentas não confirmam nem descartam a hipótese.",
                "evidence": _evidence(results),
            }
            for title in hypotheses
        ],
        "root_cause": None,
        "actions": [],
        "missing": "Faltou o histórico de mudanças da dependência para confirmar a causa",
    }


def _excerpt(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"

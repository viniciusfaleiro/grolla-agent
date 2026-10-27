"""Messages API fake (`POST /anthropic/v1/messages`), no formato da API da Anthropic.

Atende as três fases, reconhecidas pelo schema em `output_config.format`: com a propriedade
`verdicts`, é a validação; com `reply`, a resposta do chat do war room; senão, a análise
inicial. O modo de cada fase vem de
`POST /_control/llm {"mode": ..., "verification_mode": ..., "chat_mode": ...}`. Os campos são
opcionais e um não mexe nos outros; o padrão é `completa`, e o `/_control/reset` volta todos ao
padrão.

- `completa`: sem resultados de ferramenta na conversa, pede ferramentas (tool_use): na análise
  inicial `query_metric`, `recent_deploys` e `search_logs`; na validação `query_metric` e
  `recent_deploys`. Com os resultados, devolve o JSON final citando trechos deles. Na validação,
  a primeira hipótese da análise sai confirmada, a segunda rejeitada, a causa raiz cita o
  serviço e o plano é "Fazer rollback do <serviço> para o deploy anterior". No chat, pede
  `search_logs` e responde em português citando os logs e o começo da pergunta (o texto depois
  do último "Pergunta:" da mensagem);
- `inconclusiva` (só na validação): o mesmo roteiro, mas todas as hipóteses inconclusivas, sem
  causa raiz, sem plano e com o que faltou;
- `fora_do_ar`: 529 overloaded, como a API sobrecarregada;
- `lenta`: espera além do timeout do e2e antes de responder (a fase termina parcial); na
  análise inicial, a resposta tardia não traz hipóteses.

Os modos `inconclusiva` na análise inicial e no chat se comportam como `completa`.

Na análise, se a mensagem traz um bloco `<memoria>` com episódio ("### [episodio] ... incidente
<id>"), o resultado aponta `seen_before` para o primeiro episódio; senão, `seen_before` é null.

O acompanhamento da crise é reconhecido pela propriedade `trend` no schema e tem o próprio modo
(`follow_up_mode`: `completa`, `fora_do_ar` ou `lenta`). No modo `completa`, pede `query_metric`
do serviço e depois devolve o roteiro de `POST /_control/llm {"follow_up": {...}}` (`trend`,
`error_rate`, `changed` e `summary`; o padrão é `normalizado`, 0% de erro, sem mudança e o resumo
"Taxa de erro em <x>% no <serviço>."), com a métrica como evidência. O roteiro troca inteiro a
cada controle e o `/_control/reset` volta ao padrão.

As fases da memória são reconhecidas pelo schema (`episode_title`: aprendizado; `dossier`:
estudo de um serviço; `environment`: síntese) e respondem sempre como `completa`, qualquer que
seja o modo das outras fases:
- estudo: pede `read_file` (`README.md`) e `service_dependencies` do serviço da linha
  "Serviço:"; depois devolve o dossiê com a mensagem recebida inteira (as regras de alerta), os
  trechos das ferramentas e uma linha "Integração: <dependência> chamada por <serviço>" por
  dependência vista;
- síntese: o dossiê do ambiente com os serviços e as integrações, e um dossiê por linha
  "Integração: ..." da mensagem, citando quem chama;
- aprendizado: o episódio "<serviço>: <título do incidente>", com `noise` verdadeiro só se a
  mensagem cita `incident_discarded` ou "descartado", e sem dossiês.

As fases do post mortem também respondem sempre como `completa`, sem ferramentas:
- rascunho (schema com `avaliacao_agente`): um texto determinístico por seção, citando o
  serviço da linha "Serviços:" e o título da linha "Incidente:", e dois itens no plano de ação
  ("Criar alarme de conexões do <serviço>", `detectar`; "Revisar o pool de conexões do
  <serviço>", `prevenir`);
- regeneração de uma seção (schema com `body`): "Seção reescrita pelo Grolla para o
  <serviço>, seguindo a instrução: <texto da linha "Instrução:">" ("sem instrução" quando a
  linha não vem ou está vazia).

A triagem do incidente novo (schema com `same_as`) também responde sempre como `completa`, sem
ferramentas, com o roteiro de `POST /_control/llm {"triage": {...}}` (`same_as`, `same_reason`,
`severity` e `severity_reason`; o padrão é sem junção e sem severidade). `same_as` pode ser:
null; um UUID, devolvido como está; o nome de um serviço, que vira o id do outro incidente aberto
da mensagem com esse serviço (null se nenhum tiver); ou `auto`, o primeiro outro incidente da
mensagem. O roteiro troca inteiro a cada controle e o `/_control/reset` volta ao padrão.

O console de operação (schema com `charts`) também responde sempre como `completa`. Lê a pergunta
(o texto depois do último "Pergunta:" da mensagem, ou a mensagem inteira) e o serviço dela (o
primeiro nome terminado em `-api` na pergunta; senão, o último da mensagem). Sem resultados de
ferramenta, pede `query_metric` do serviço e, se a pergunta começa com "Para registrar:", também
`remember` (assunto o serviço, texto o resto da pergunta). Com os resultados, responde "Consultei
o <serviço>: <trecho da métrica>", ou "Guardei: <fato>. Consultei o <serviço>: ..." quando chamou
`remember`, e pede um gráfico de `taxa_de_erro` do serviço por 60 min quando a pergunta cita
"gráfico" ou "erro" e não é "Para registrar:" (sem gráfico, senão).

Os argumentos de cada ferramenta saem do `input_schema` recebido: enum vira o primeiro valor,
booleano vira true e `service` vira o primeiro serviço da linha "Serviços: ..." da mensagem.
"""

import asyncio
import json
import re
from enum import StrEnum
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from grolla_fakes.state import FakesState, RecordedRequest

MESSAGES_PATH = "/anthropic/v1/messages"
FIRST_TOOLS = ("query_metric", "recent_deploys", "search_logs")
VERIFICATION_TOOLS = ("query_metric", "recent_deploys")
CHAT_TOOLS = ("search_logs",)
FOLLOW_UP_TOOLS = ("query_metric",)
CONSOLE_TOOLS = ("query_metric",)
REGISTER = "Para registrar:"  # no console, a pergunta que ensina um fato ao agente
CHART_WORDS = ("gráfico", "erro")
CHART_MINUTES = 60
_API_SERVICE = re.compile(r"\b[a-z0-9][a-z0-9-]*-api\b")
SLOW_SECONDS = 60.0  # bem acima dos timeouts de análise e de validação do e2e
UNKNOWN_SERVICE = "servico-desconhecido"
FALLBACK_HYPOTHESIS = "Hipótese da análise inicial"
_SERVICES_LINE = re.compile(r"Serviços?:\s*([^\n,]+)")
_HYPOTHESES = "Hipóteses da análise inicial:"
_HYPOTHESIS_LINE = re.compile(r"^- (.+?) \(confiança [^)]*\)$")
_QUESTION = "Pergunta:"
STUDY_TOOLS = ("read_file", "service_dependencies")
_ARGUMENTS = {"path": "README.md"}  # argumentos de texto obrigatórios que não são o serviço
_INCIDENT_LINE = re.compile(r"Incidente:\s*([^\n]+)")
# Cabeçalho de episódio do bloco <memoria>: "### [episodio] svc: título (..., incidente <id>)".
_EPISODE = re.compile(
    r"### \[episodio\] [^:\n]+: (?P<title>[^\n]*?) \([^\n]*?incidente "
    r"(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)
# Nome de cada dependência no resumo de `service_dependencies`: "banco x (nome): N chamadas".
_DEPENDENCY = re.compile(r"(?:\(([^)]+)\)|(?:serviço|host) ([^\s:;(]+)): \d+ chamadas")
_INTEGRATION_LINE = re.compile(r"Integração: (\S+) chamada por (\S+)")
_INSTRUCTION_LINE = re.compile(r"Instrução:[ \t]*([^\n]*)")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_OTHERS = "Outros incidentes abertos"  # cabeçalho da lista na mensagem da triagem
AUTO = "auto"


class LlmMode(StrEnum):
    COMPLETE = "completa"
    DOWN = "fora_do_ar"
    SLOW = "lenta"
    INCONCLUSIVE = "inconclusiva"  # só faz diferença na validação


class Phase(StrEnum):
    ANALYSIS = "analise"
    VERIFICATION = "validacao"
    CHAT = "chat"
    LEARNING = "aprendizado"
    STUDY = "estudo"
    SYNTHESIS = "sintese"
    FOLLOW_UP = "acompanhamento"
    POST_MORTEM = "post_mortem"
    REGENERATION = "regeneracao"
    TRIAGE = "triagem"
    CONSOLE = "console"


class FollowUpScript(BaseModel):
    """O que o ciclo de acompanhamento responde; `summary` vazio vira o resumo padrão."""

    trend: Literal["piorando", "estavel", "melhorando", "normalizado"] = "normalizado"
    error_rate: float | None = 0.0
    changed: bool = False
    summary: str = ""


class TriageScript(BaseModel):
    """O que a triagem responde; `same_as` é null, um UUID, um serviço ou `auto`."""

    same_as: str | None = None
    same_reason: str = ""
    severity: Literal["P0", "P1", "P2", "P3"] | None = None
    severity_reason: str = ""


# Fases da memória, pela propriedade que só o schema delas tem; sempre no modo `completa`.
_MEMORY_PHASES = {
    "episode_title": Phase.LEARNING,
    "dossier": Phase.STUDY,
    "environment": Phase.SYNTHESIS,
}
# Fases do post mortem, idem: sem ferramentas e sempre no modo `completa`.
_POST_MORTEM_PHASES = {"avaliacao_agente": Phase.POST_MORTEM, "body": Phase.REGENERATION}
# Fases sem modo: sempre `completa`, sem ferramentas ou com as próprias.
_FIXED_PHASES = {
    **_MEMORY_PHASES,
    **_POST_MORTEM_PHASES,
    "same_as": Phase.TRIAGE,
    "charts": Phase.CONSOLE,
}


class LlmControl(BaseModel):
    mode: LlmMode | None = None  # campo ausente: a fase fica como está
    verification_mode: LlmMode | None = None
    chat_mode: LlmMode | None = None
    follow_up_mode: LlmMode | None = None
    follow_up: FollowUpScript | None = None  # troca o roteiro inteiro
    triage: TriageScript | None = None  # idem


def llm_router(state: FakesState) -> APIRouter:
    router = APIRouter()

    @router.post("/_control/llm", status_code=204)
    async def set_mode(control: LlmControl) -> None:  # pyright: ignore[reportUnusedFunction]
        if control.mode is not None:
            state.llm_mode = control.mode
        if control.verification_mode is not None:
            state.llm_verification_mode = control.verification_mode
        if control.chat_mode is not None:
            state.llm_chat_mode = control.chat_mode
        if control.follow_up_mode is not None:
            state.llm_follow_up_mode = control.follow_up_mode
        if control.follow_up is not None:
            state.llm_follow_up = control.follow_up.model_dump()
        if control.triage is not None:
            state.llm_triage = control.triage.model_dump()

    @router.post(MESSAGES_PATH)
    async def messages(request: Request) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        body: dict[str, Any] = await request.json()
        current = phase(body)
        if current in _FIXED_PHASES.values():
            state.requests.append(
                RecordedRequest(method="POST", path=MESSAGES_PATH, body=body, status=200)
            )
            return JSONResponse(fixed_reply(body, current, state))
        modes = {
            Phase.ANALYSIS: state.llm_mode,
            Phase.VERIFICATION: state.llm_verification_mode,
            Phase.CHAT: state.llm_chat_mode,
            Phase.FOLLOW_UP: state.llm_follow_up_mode,
        }
        mode = LlmMode(modes[current])
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
            if current is Phase.ANALYSIS:  # se ainda houver quem espere, sai sem hipóteses
                return JSONResponse(_final(body, {**_draft({}), "hypotheses": []}))
        return JSONResponse(phase_reply(body, current, mode, state))

    return router


def fixed_reply(body: dict[str, Any], current: Phase, state: FakesState) -> dict[str, Any]:
    """A resposta das fases sem modo, sempre `completa`."""
    if current is Phase.TRIAGE:
        return triage_reply(body, TriageScript(**state.llm_triage))
    if current is Phase.CONSOLE:
        return console_reply(body)
    if current in _POST_MORTEM_PHASES.values():
        return post_mortem_reply(body, current)
    return memory_reply(body, current)


def phase_reply(
    body: dict[str, Any], current: Phase, mode: LlmMode, state: FakesState
) -> dict[str, Any]:
    """A resposta da fase no modo `completa` (ou `inconclusiva`, na validação)."""
    if current is Phase.VERIFICATION:
        return verification_reply(body, mode is LlmMode.INCONCLUSIVE)
    if current is Phase.CHAT:
        return chat_reply(body)
    if current is Phase.FOLLOW_UP:
        return follow_up_reply(body, FollowUpScript(**state.llm_follow_up))
    return reply(body)


def phase(body: dict[str, Any]) -> Phase:
    """A validação pede o schema com `verdicts`; o chat, com `reply`; o acompanhamento, com
    `trend`; a análise, o do rascunho."""
    output: dict[str, Any] = body.get("output_config") or {}
    output_format: dict[str, Any] = output.get("format") or {}
    schema: dict[str, Any] = output_format.get("schema") or {}
    properties: dict[str, Any] = schema.get("properties") or {}
    for key, fixed_phase in _FIXED_PHASES.items():
        if key in properties:
            return fixed_phase
    if "verdicts" in properties:
        return Phase.VERIFICATION
    if "reply" in properties:
        return Phase.CHAT
    if "trend" in properties:
        return Phase.FOLLOW_UP
    return Phase.ANALYSIS


def chat_reply(body: dict[str, Any]) -> dict[str, Any]:
    """Resposta do chat: consulta os logs do serviço e responde citando-os e a pergunta."""
    results = _tool_results(body["messages"])
    if not results:
        return _message(body, _tool_calls(body, CHAT_TOOLS), "tool_use")
    first = _first_text(body["messages"][0])
    _, _, question = first.rpartition(_QUESTION)
    logs = results.get("search_logs", "sem logs")
    text = (
        f"Consultei os logs de {_service(first)}: {_excerpt(logs)} "
        f'Sobre "{_excerpt(question.strip(), 80)}": os logs acima são o que encontrei; '
        "se quiser, registre a ação no war room."
    )
    return _final(body, {"reply": text})


def console_reply(body: dict[str, Any]) -> dict[str, Any]:
    """Console: consulta a métrica do serviço da pergunta (e guarda o fato, se pedido) e
    responde citando-a, com o gráfico da taxa de erro quando a pergunta fala de gráfico ou erro.
    """
    first = _first_text(body["messages"][0])
    _, _, question = first.rpartition(_QUESTION)
    question = question.strip()
    found = _API_SERVICE.findall(question) or _API_SERVICE.findall(first)[-1:]
    service = found[0] if found else UNKNOWN_SERVICE
    fact = question.removeprefix(REGISTER).strip() if question.startswith(REGISTER) else None
    results = _tool_results(body["messages"])
    if not results:
        calls = _tool_calls(body, CONSOLE_TOOLS, service)
        tools = {tool["name"] for tool in body.get("tools", [])}
        if fact and "remember" in tools:
            remember = {"subject": service, "text": fact}
            calls.append(
                {"type": "tool_use", "id": "toolu_remember", "name": "remember", "input": remember}
            )
        return _message(body, calls, "tool_use")
    metric = _excerpt(results.get("query_metric", "sem dados de métrica"))
    text = f"Consultei o {service}: {metric}"
    if "remember" in results:
        text = f"Guardei: {fact}. {text}"
    wants_chart = fact is None and any(word in question.lower() for word in CHART_WORDS)
    charts = (
        [{"service": service, "signal": "taxa_de_erro", "minutes": CHART_MINUTES}]
        if wants_chart
        else []
    )
    return _final(body, {"text": text, "charts": charts})


def follow_up_reply(body: dict[str, Any], script: FollowUpScript) -> dict[str, Any]:
    """Ciclo de acompanhamento: consulta a métrica do serviço e devolve o roteiro."""
    results = _tool_results(body["messages"])
    if not results:
        return _message(body, _tool_calls(body, FOLLOW_UP_TOOLS), "tool_use")
    service = _service(_first_text(body["messages"][0]))
    rate = (
        "não medida" if script.error_rate is None else f"{script.error_rate:g}%".replace(".", ",")
    )
    summary = script.summary or f"Taxa de erro em {rate} no {service}."
    metric = results.get("query_metric", "sem dados de métrica")
    return _final(
        body,
        {
            "trend": script.trend,
            "error_rate": script.error_rate,
            "summary": summary,
            "changed": script.changed,
            "evidence": [{"text": _excerpt(metric), "url": None}],
        },
    )


def reply(body: dict[str, Any]) -> dict[str, Any]:
    """Resposta do modo `completa` da análise inicial para a conversa recebida."""
    results = _tool_results(body["messages"])
    if not results:
        return _message(body, _tool_calls(body, FIRST_TOOLS), "tool_use")
    seen = seen_before(_first_text(body["messages"][0]))
    return _final(body, {**_draft(results), "seen_before": seen})


def seen_before(text: str) -> dict[str, str] | None:
    """ "Já vi isso": o primeiro episódio do bloco <memoria> da mensagem, se houver."""
    _, _, memory = text.partition("<memoria>")
    memory, _, _ = memory.partition("</memoria>")
    match = _EPISODE.search(memory)
    if match is None:
        return None
    reason = f"mesmo serviço e mesmo sintoma do episódio {match.group('title').strip()}"
    return {"incident_id": match.group("id"), "reason": reason}


def triage_reply(body: dict[str, Any], script: TriageScript) -> dict[str, Any]:
    """Triagem: o roteiro, com `same_as` resolvido contra os outros incidentes da mensagem."""
    same_as = script.same_as
    if same_as is not None and not _UUID.fullmatch(same_as):
        others = _other_incidents(_first_text(body["messages"][0]))
        found = [i for i, entry in others if same_as == AUTO or same_as in entry]
        same_as = found[0] if found else None
    return _final(body, {**script.model_dump(), "same_as": same_as})


def _other_incidents(text: str) -> list[tuple[str, str]]:
    """(id, trecho) de cada outro incidente aberto: o trecho vai da linha com o id até a linha
    com o id seguinte, fora o bloco <memoria>."""
    before, _, rest = text.partition("<memoria>")
    _, _, after = rest.partition("</memoria>")
    _, _, others = (before + after).partition(_OTHERS)
    entries: list[tuple[str, str]] = []
    for line in others.splitlines():
        match = _UUID.search(line)
        if match:
            entries.append((match.group(), line))
        elif entries:
            incident, entry = entries[-1]
            entries[-1] = (incident, f"{entry}\n{line}")
    return entries


def memory_reply(body: dict[str, Any], current: Phase) -> dict[str, Any]:
    """Aprendizado, estudo de um serviço e síntese, com conteúdo determinístico."""
    first = _first_text(body["messages"][0])
    if current is Phase.LEARNING:
        return _final(body, _learned(first))
    if current is Phase.SYNTHESIS:
        return _final(body, _synthesized(first))
    results = _tool_results(body["messages"])
    if not results:
        return _message(body, _tool_calls(body, STUDY_TOOLS), "tool_use")
    return _final(body, {"dossier": _dossier(_service(first), first, results)})


def post_mortem_reply(body: dict[str, Any], current: Phase) -> dict[str, Any]:
    """Rascunho do post mortem ou regeneração de uma seção, com conteúdo determinístico."""
    first = _first_text(body["messages"][0])
    service = _service(first)
    if current is Phase.REGENERATION:
        match = _INSTRUCTION_LINE.search(first)
        instruction = (match.group(1).strip() if match else "") or "sem instrução"
        text = f"Seção reescrita pelo Grolla para o {service}, seguindo a instrução: {instruction}"
        return _final(body, {"body": text})
    title = _INCIDENT_LINE.search(first)
    incident = title.group(1).strip() if title else "incidente"
    return _final(
        body,
        {
            "resumo": f"O {service} teve o incidente {incident}, resolvido pelo time.",
            "impacto": f"Parte das requisições do {service} falhou durante o incidente.",
            "deteccao": "O alerta de taxa de erro detectou o problema.",
            "fatores": f"- Deploy recente do {service}\n- Pool de conexões sem folga",
            "hipoteses": "- Deploy recente introduziu a falha: confirmada",
            "licoes": "Funcionou: o rollback. Não funcionou: o alarme de conexões. Sorte: nenhuma.",
            "plano": f"Itens para que o {service} não repita o incidente.",
            "avaliacao_agente": "O agente apontou a causa raiz antes do time.",
            "actions": [
                {"description": f"Criar alarme de conexões do {service}", "kind": "detectar"},
                {"description": f"Revisar o pool de conexões do {service}", "kind": "prevenir"},
            ],
        },
    )


def _learned(text: str) -> dict[str, Any]:
    title = _INCIDENT_LINE.search(text)
    incident = title.group(1).strip() if title else "incidente"
    service = _service(text)
    noise = "incident_discarded" in text or "descartad" in text.lower()
    outcome = "alarme falso, descartado pelo time" if noise else "resolvido pelo time"
    return {
        "episode_title": f"{service}: {incident}",
        "episode_body": (
            f"O incidente {incident} no {service} foi {outcome}. Causa vista na investigação: "
            "erro de conexão com o banco depois do último deploy."
        ),
        "noise": noise,
        "dossiers": [],
    }


def _dossier(service: str, brief: str, results: dict[str, str]) -> str:
    """Dossiê do serviço: cita a mensagem recebida (com as regras) e o que as ferramentas
    mostraram; cada dependência vira uma linha "Integração: <dep> chamada por <serviço>".
    """
    dependencies = results.get("service_dependencies", "")
    lines = [f"# {service}", "", "## O que recebi para estudar", brief.strip(), ""]
    lines += ["## Repositório", _excerpt(results.get("read_file", "sem leitura"), 400), ""]
    lines += ["## Dependências", _excerpt(dependencies or "nenhuma consultada", 400), ""]
    names = dict.fromkeys(m.group(1) or m.group(2) for m in _DEPENDENCY.finditer(dependencies))
    lines += [f"Integração: {name} chamada por {service}" for name in names]
    return "\n".join(lines)


def _synthesized(text: str) -> dict[str, Any]:
    callers: dict[str, list[str]] = {}
    for name, caller in _INTEGRATION_LINE.findall(text):
        callers.setdefault(name.strip(), []).append(caller.strip())
    services = sorted({c for found in callers.values() for c in found})
    environment = (
        "# Ambiente\n\nServiços estudados: "
        + (", ".join(services) or "nenhum")
        + ". Integrações vistas nos traces: "
        + (", ".join(callers) or "nenhuma")
        + "."
    )
    integrations = [
        {
            "subject": name,
            "body": f"# {name}\n\nDependência chamada por {', '.join(sorted(set(found)))}.",
        }
        for name, found in callers.items()
    ]
    return {"environment": environment, "integrations": integrations}


def _final(body: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return _message(body, [{"type": "text", "text": json.dumps(result)}], "end_turn")


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
    return _final(body, result)


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


def _tool_calls(
    body: dict[str, Any], names: tuple[str, ...], service: str | None = None
) -> list[dict[str, Any]]:
    service = service or _service(_first_text(body["messages"][0]))
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
            arguments[name] = _ARGUMENTS.get(name, "erro") if name != "service" else service
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
        "seen_before": None,
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

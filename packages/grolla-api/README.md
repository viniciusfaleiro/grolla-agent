<div align="center">

<img src="../../docs/assets/grolla.svg" alt="Grolla" width="96">

# grolla-api

**Deployable HTTP do agent-grolla: recebe os webhooks de alerta e serve o war room, o console e o post mortem.**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-uvicorn-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Kind](https://img.shields.io/badge/tipo-deployable-orange)](#-como-rodar)
[![Docker](https://img.shields.io/badge/imagem-grolla--api-2496ED?logo=docker&logoColor=white)](Dockerfile)

</div>

---

## ✨ O que é

O processo que precisa responder rápido, sempre: é por ele que os alertas chegam. A api é fina:
autentica e traduz o webhook pelo port `AlertIngress`, chama o caso de uso `receive_alerts` e
renderiza as páginas (Jinja2 + HTMX e um `war-room.js` pequeno, servidos como estáticos) a partir do
`IncidentRepository`. No war room, as ações e as mensagens viram chamadas aos casos de uso `act`
e `ask` da `grolla.application.war_room`; no post mortem, aos casos de uso da
`grolla.application.post_mortem`, lendo do `PostMortemStore` e gerando o PDF pelo `DocumentRenderer`; na página da memória, aos casos de uso da
`grolla.application.memory`, lendo do `MemoryStore`, do `StudyQueue` e do `LearningQueue`. A api
só lê o form e o autor e devolve o fragmento ou recarrega a página. Não tem regra de
negócio nem faz trabalho pesado — investigação, timers e notificações ficam no `grolla-worker`. Depende só do
`grolla-core`; no startup aplica as migrações pendentes e monta as dependências pelo `bootstrap`.

## 🚀 Features

| Área | O que entrega | Endpoint |
|---|---|---|
| **Health check** | `200 {"status": "ok"}` com o banco acessível; `503` sem banco | `GET /health` |
| **Saúde do worker** | `200` se o último heartbeat tem até 2 min, senão `503`; corpo `{"last_heartbeat": <iso ou null>}` | `GET /health/worker` |
| **Webhook de alertas** | HTTP Basic: `401` sem credencial válida (sem efeito), `422` em payload inválido ou não-JSON, `202` depois de processar os alertas | `POST /webhooks/alerts` |
| **Lista de incidentes** | HTML server-side: título, severidade, estado em português, serviços e detecção, mais recentes primeiro | `GET /incidents` |
| **War room** | A página do incidente, em duas colunas (uma no celular) e com tema escuro, na identidade do Grolla. Na condução: severidade, título, estado, serviços, o pulso do incidente na cor do estado, o ciclo Detectado → Em tratamento → Mitigado → Resolvido com a etapa atual marcada, IC, duração, TTA/TTM/TTR e o impacto estimado. Abaixo: a ação principal do estado em destaque, registrar ação, seletor de severidade, encerrar e descartar em popover com o motivo, e a causa raiz e o plano de ação sugerido. Depois, a análise (hipóteses com confiança, descarte de hipótese na própria hipótese e a descartada marcada com autor e motivo), a validação (veredito por hipótese) e as consultas recolhidas; alertas e timeline com o autor e sem o ruído de enfileiramento. `404` se não existe | `GET /incidents/{id}` |
| **Autor (fase 1, sem SSO)** | Sem o cookie `grolla_author`, a página pede o nome; o form grava o cookie (HttpOnly, `SameSite=Lax`, 30 dias, nome URL-encoded) e redireciona (`303`) só para um caminho do próprio site; com o cookie, "Você é Ana (trocar)". Não é autenticação: a página fica na rede interna | `POST /author` |
| **Ações do war room** | Forms HTMX: `assume`, `severity` (`severidade`), `record` (`texto`), `discard-hypothesis` (`titulo`, `motivo`; um botão por hipótese da análise), `mitigate`, `reopen`, `resolve` e `discard` (`motivo`). `200` com o fragmento do cabeçalho e das ações; `401` "informe seu nome" sem cookie; `422` com a mensagem do `InvalidActionError`; `404` para ação ou incidente inexistente. Os erros voltam com `HX-Retarget` para a área de erro do form | `POST /incidents/{id}/actions/{acao}` |
| **Chat com o agente** | Painel de mensagens fixo ao lado: as suas mensagens à direita, as dos outros à esquerda e o Grolla com o avatar do mascote; a resposta do agente com markdown mínimo e seguro (parágrafos, listas, negrito, links) e as consultas recolhidas; "Grolla está investigando…" enquanto responde; sugestões de pergunta com um clique no chat vazio; Enter envia, Shift+Enter quebra a linha e a conversa rola sozinha até a mensagem nova. O envio (`texto`) responde `200` com a conversa atualizada, `401` sem cookie, `422` texto vazio, `404` incidente inexistente | `POST /incidents/{id}/chat` |
| **Polling (3 s)** | Fragmentos do chat e da timeline com `hx-trigger="every 3s"`; a timeline traz junto, fora de banda, o acompanhamento, a proposta e o impacto. A URL carrega a versão vista (`?visto=`): sem nada novo, `204` e o HTMX não troca nada (um `<details>` aberto continua aberto) | `GET /incidents/{id}/chat` · `GET /incidents/{id}/timeline` |
| **Acompanhamento da crise** | Seção "Acompanhamento" acima da análise com os últimos 6 ciclos terminados, o mais recente primeiro: horário, tendência com selo colorido (piorando, estável, melhorando, normalizado), taxa de erro ("12,5% de erro") e resumo; ciclo parcial com o motivo. Escondida quando o incidente não tem ciclo. No cabeçalho, "Impacto: R$ 3.000,00 estimado" quando `impact_of` devolve valor. Enquanto a última proposta (`AGENT_PROPOSAL`) vale para o estado atual, o aviso "Grolla sugere marcar como mitigado" ou "Grolla sugere encerrar" com o motivo e o botão da ação (encerrar abre o popover do motivo). A timeline rotula os eventos novos ("Acompanhamento: piorando — <resumo>", parcial, parado, a proposta e "Mitigado → Em tratamento: alerta voltou a disparar"). Os três blocos chegam pelo polling da timeline (`hx-swap-oob`) | `GET /incidents/{id}` · `GET /incidents/{id}/timeline` |
| **"Já vi isso"** | Na análise do incidente, quando ela aponta um caso parecido da memória: "Já vi isso: <título> (dd/mm)" com o link para o incidente passado e o motivo da semelhança | `GET /incidents/{id}` |
| **Memória do Grolla** | Link "Memória" na barra. O dossiê do ambiente em destaque (markdown seguro, com as seções como títulos), os assuntos por tipo (ambiente, serviços, integrações) com registros, última atualização e último estudo, o estado da última rodada de estudo (modo, quem pediu, quando, status, tokens; serviços estudados, pendentes por orçamento e falhos com o motivo), os aprendizados que falharam com o link do incidente e "ensinar sobre outro assunto" (`?assunto=` redireciona para a página dele). Estado vazio "Ainda não estudei o ambiente"; sem `memory.study` no YAML, diz que o estudo está desligado e esconde os botões | `GET /memoria` |
| **Assunto da memória** | Dossiê, episódios (link do incidente, marca de ruído) e notas, cada um com o selo aprendido/confirmado, a fonte com link (incidente, chat, links consultados pelo estudo, pessoa) e as datas; esquecidos esmaecidos. Com nome: editar, confirmar (só aprendido), esquecer e o formulário "adicionar conhecimento", que funciona em assunto sem dossiê (escolhe serviço ou integração). O histórico de versões carrega ao abrir, com "restaurar esta versão" | `GET /memoria/{assunto}` · `GET /memoria/registros/{id}/versoes` |
| **Escrita na memória** | Forms HTMX sobre os casos de uso `add_note` (`texto`, `tipo` só em assunto novo), `edit_memory` (`texto`), `confirm_memory`, `forget_memory`, `restore_memory` (`versao`, ISO) e `request_study` (`FULL` em estudar; `RELEARN` em reaprender, que exige `confirmar=sim` e mostra num popover o que é esquecido e o que fica). Sucesso: `204` com `HX-Redirect` para a página (HTMX) ou `303` (form comum). `401` "informe seu nome" sem cookie, `422` com a mensagem do `InvalidMemoryError`, `409` se já há estudo na fila, `404` para registro inexistente; os erros voltam com `HX-Retarget` para a área de erro do registro, da nota ou do estudo | `POST /memoria/{assunto}/notas` · `POST /memoria/registros/{id}[/confirmar\|/esquecer\|/restaurar]` · `POST /memoria/estudar` · `POST /memoria/reaprender` |
| **Lista de post mortems** | Link "Post mortems" na barra. Os post mortems, os mais recentes primeiro, com severidade, título, status (Rascunho, Em revisão, Aprovado, Encerrado), serviços, data da detecção e "N itens vencidos" em destaque. Filtros na query: `status` (`rascunho`, `em_revisao`, `aprovado`, `encerrado`), `severidade`, `servico`, `de` e `ate` (`YYYY-MM-DD`, detecção do incidente); valor inválido vale como sem filtro. Estados vazios: "Nenhum post mortem ainda" e "Nenhum post mortem com esses filtros" | `GET /postmortems` |
| **Post mortem** | Cabeçalho com o ciclo Rascunho → Em revisão → Aprovado → Encerrado, quem moveu e quando, e só os botões válidos: "Começar revisão", "Aprovar", "Encerrar" e "Voltar para revisão"; "Baixar PDF" só em aprovado e encerrado. As 10 seções na ordem do template, em markdown seguro, com autor e data; seção "não gerada: …" em destaque. Com nome: editar (textarea) e regenerar com instrução opcional (fora de métricas e timeline, e fora de aprovado e encerrado), que confirma o pedido sem recarregar. Histórico de versões por seção, carregado ao abrir. No plano de ação, a tabela com descrição, tipo, dono, prazo e situação, com o item vencido marcado e o aviso no topo; concluir, aceitar, editar (popover) e adicionar. Sem documento: "O rascunho está sendo escrito" (incidente resolvido) ou "Ainda não há post mortem". A página do incidente resolvido tem o link "Post mortem" | `GET /postmortems/{id}` · `GET /postmortems/{id}/secoes/{secao}/versoes` |
| **Escrita no post mortem** | Forms HTMX sobre `edit_section` (`texto`), `request_regeneration` (`instrucao`, opcional), `move_status` (`para`), `add_action` (`descricao`, `tipo` = `prevenir`\|`detectar`\|`mitigar`, `dono` e `prazo` opcionais) e `update_action` (`descricao`, `tipo`, `dono`, `prazo`, `situacao` = `aberto`\|`concluido`\|`aceito`; campo ausente fica como está, vazio limpa dono e prazo). Sucesso: `204` com `HX-Redirect` (HTMX) ou `303`; a regeneração pelo HTMX responde `202` com "Pedido enviado: o Grolla está regenerando esta seção…". `401` "informe seu nome", `422` com a mensagem do `InvalidPostMortemError` (ex.: aprovar com item sem dono) ou de campo inválido, `404` para incidente, post mortem, seção ou item inexistente; os erros voltam com `HX-Retarget` para `#erro-<secao>`, `#erro-status` ou `#erro-plano` | `POST /postmortems/{id}/secoes/{secao}[/regenerar]` · `POST /postmortems/{id}/status` · `POST /postmortems/{id}/plano[/{item}]` |
| **PDF** | `application/pdf` com `Content-Disposition: attachment; filename="post-mortem-<id>.pdf"`, gerado pelo `DocumentRenderer` via `render_pdf`. `409` em rascunho ou em revisão, `503` se o renderer falha, `404` sem post mortem | `GET /postmortems/{id}.pdf` |
| **Estáticos** | HTMX 2.0.4 (sem build; `htmx-config` faz as respostas `4xx` também aparecerem), `war-room.css` (tokens de cor e tipografia, tema escuro), `war-room.js` (Enter envia, caixa cresce, rolagem) e o mascote | `GET /static/…` |

## ⚡ Como rodar

```bash
docker compose up -d db
export GROLLA_DATABASE_URL=postgresql://grolla:grolla@localhost:5433/grolla
uv run grolla-api                      # http://localhost:8000/health
```

Imagem própria (só a api e o core dentro):

```bash
docker build -f packages/grolla-api/Dockerfile -t grolla-api:local .
```

| Variável | Obrigatória | Padrão |
|---|---|---|
| `GROLLA_DATABASE_URL` | sim | — |
| `GROLLA_CONFIG_FILE` | sim | — |
| `GROLLA_ALERTS_WEBHOOK_USER` | sim | — |
| `GROLLA_ALERTS_WEBHOOK_PASSWORD` | sim | — |
| `GROLLA_LOG_LEVEL` | não | `INFO` |

## 🧪 Testes

```bash
uv run pytest packages/grolla-api
```

- `tests/unit/` — `create_app` recebe `Services` (checagem de banco, heartbeat e os ports), montado
  com os fakes de `grolla.testing`: rotas de saúde, webhook, páginas, war room, memória e post mortem (com os
  casos de uso reais sobre os fakes da memória e do post mortem) testadas com `TestClient`,
  sem banco e sem rede; e a recusa em subir sem `GROLLA_DATABASE_URL`.
- `tests/integration/` — `/health` contra Postgres real (Testcontainers).

## 🗂 Layout

```
src/grolla_api/
  app.py     create_app(): rotas HTTP (saúde, webhook, páginas, war room, memória, post mortem, autor)
  war_room.py  apresentação do war room: rótulos, ciclo, tendência e proposta do acompanhamento, ação em destaque, durações, markdown seguro, leitura do form e do cookie
  memory_pages.py  apresentação da memória: rótulos, assuntos por tipo, fontes com link, progresso do estudo
  post_mortem_pages.py  apresentação do post mortem: rótulos, transições por status, filtros da lista e leitura dos forms do plano
  templates/ páginas Jinja2 (base, lista, incidente, memory, memory_subject, postmortems, postmortem) e fragmentos _war_room, _follow_up (acompanhamento, impacto e proposta), _chat, _chat_messages, _timeline, _memory_versions, _post_mortem_versions
  static/    htmx.min.js · war-room.css · war-room.js · grolla.svg · grolla-avatar.svg
  main.py    entrypoint `grolla-api`: settings → migrações → uvicorn
tests/       unit/ · integration/
Dockerfile
```

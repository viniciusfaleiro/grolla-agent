<div align="center">

<img src="docs/assets/grolla.svg" alt="Grolla, o mascote do agent-grolla" width="160">

# agent-grolla

**Agente de SRE para qualquer sistema em produção: detecta, investiga, aciona o on-call e conduz o incidente até o post mortem — chegando antes do humano, com as evidências organizadas.**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-workspace-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![FastAPI](https://img.shields.io/badge/FastAPI-api-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-4169E1?logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)](docker-compose.yml)
[![HTMX](https://img.shields.io/badge/HTMX-war%20room-3366CC?logo=htmx&logoColor=white)](https://htmx.org/)

[![Claude](https://img.shields.io/badge/LLM-Claude%20(Anthropic%20%C2%B7%20Bedrock)-D97757?logo=anthropic&logoColor=white)](https://www.anthropic.com/)
[![Memória viva](https://img.shields.io/badge/mem%C3%B3ria-viva-8A2BE2)](#-a-memória-viva-do-grolla)
[![RAG](https://img.shields.io/badge/RAG-h%C3%ADbrido%20%C2%B7%20embeddings%20locais-FF6F00?logo=onnx&logoColor=white)](#-como-funciona-o-rag)
[![MCP](https://img.shields.io/badge/MCP-SigNoz-6E56CF)](#-features)

[![Architecture](https://img.shields.io/badge/arquitetura-hexagonal-blueviolet)](#-arquitetura)
[![Boundaries](https://img.shields.io/badge/fronteiras-import--linter-informational)](#-gates-de-qualidade--o-harness)
[![Types](https://img.shields.io/badge/tipos-pyright%20strict-1f6feb)](#-gates-de-qualidade--o-harness)
[![Tests](https://img.shields.io/badge/testes-pytest%20%2B%20Testcontainers%20%2B%20e2e-25A162?logo=pytest&logoColor=white)](#-testes-integrados-e2e)
[![Coverage floor](https://img.shields.io/badge/cobertura-domain%2090%2F80%20%C2%B7%20application%2080%2F70-success)](#-gates-de-qualidade--o-harness)
[![Code style](https://img.shields.io/badge/estilo-ruff-D7FF64?logo=ruff&logoColor=black)](https://docs.astral.sh/ruff/)
[![Workflow](https://img.shields.io/badge/workflow-spec--driven%20(OpenSpec)-9cf)](#-spec-driven-development)
[![Status](https://img.shields.io/badge/status-modelo%20no%20Bedrock%20%E2%9C%93-brightgreen)](#-roadmap)

### ▶️ Veja o Grolla em ação

<a href="docs/assets/grolla-demo.mp4"><img src="docs/assets/grolla-demo.gif" alt="Demonstração do Grolla: lista de incidentes, war room com a causa raiz e o alarme normalizando ao vivo" width="880"></a>

<sub>Um incidente real no laboratório (CPU da estoque-api cortada para 25m): o Grolla abre o incidente, confirma a causa raiz, responde no chat com o comando para voltar ao normal, avisa ao vivo quando o alarme normaliza e escreve o post mortem. <a href="docs/assets/grolla-demo.mp4"><b>Assista ao vídeo completo (mp4)</b></a>.</sub>

</div>

---

## ✨ O que é

O `agent-grolla` acompanha qualquer ambiente em produção 24x7, de qualquer domínio. Ele recebe os alertas do
sistema de observabilidade, decide a severidade, aciona o on-call quando é P0/P1, investiga a
causa com um LLM, conduz o incidente num **war room web** e, no fim, escreve o rascunho do post
mortem para revisão humana.

O nome é uma homenagem: o projeto é a tentativa de ter um agente que faz o que fazia **um dos
melhores profissionais de SRE** com quem o autor já trabalhou — o Grolla. Chegar primeiro,
separar sinal de ruído, levantar hipóteses com evidência e manter o registro de tudo o que
aconteceu durante a crise.

E, como um bom SRE, ele **conhece o ambiente que cuida e aprende com o tempo** — é a
[memória viva](#-a-memória-viva-do-grolla), o coração do agente.

### 🧠 A memória viva do Grolla

O melhor SRE do time não é o que sabe consultar uma métrica: é o que **conhece o ambiente de
cor** e **lembra do que já viu**. O Grolla mantém uma base de conhecimento própria, que ele
escreve, consulta e reorganiza sozinho:

- **Estuda o ambiente assim que sobe.** Parte dos alertas do domínio do time, chega aos serviços,
  lê os `.md` dos repositórios, entende as pipelines, mapeia a infraestrutura na AWS e descobre as
  integrações pelos traces. O resultado é um **dossiê por assunto** — o ambiente, cada serviço e
  cada integração — com tudo que importa num troubleshooting: o que faz, de quem depende, como
  faz deploy, o que cada alerta significa e o que olhar primeiro, o comportamento normal e as
  fragilidades conhecidas.
- **Mantém em dia.** Um estudo diário relê os alertas, estuda primeiro o que é novo e atualiza o
  que ficou velho.
- **Aprende com cada incidente.** Ao encerrar, escreve o episódio (sintomas, causa, o que resolveu,
  o que despistou, se era ruído) e reescreve os dossiês afetados.
- **Aprende com o time.** No chat do war room, uma correção como "isso é normal, é o batch das
  3h" vira conhecimento na hora ("Guardei: …").
- **Lembra quando importa.** Toda análise, validação e resposta do chat recebe o dossiê do
  ambiente, dos serviços e das integrações e os casos parecidos. Quando é o mesmo problema de
  novo, a análise avisa **"Já vi isso"** com o link do incidente passado e usa a causa de então
  como primeira hipótese a confirmar.
- **Aprende sozinho, o time corrige.** Tudo entra na hora como *aprendido*; o que uma pessoa
  escreve ou confirma vira *confirmado* e prevalece. Na página **Memória** (`/memoria`) o time
  lê, edita, confirma, esquece, restaura versões, semeia conhecimento — e pode pedir para o
  Grolla **reaprender o ambiente do zero**. Nada é apagado de fato: toda mudança tem autor e
  versão.

A memória é tratada como dado, nunca como instrução, e nunca muda severidade nem aciona ninguém.

### 🔎 Como funciona o RAG

Para lembrar, o Grolla usa **RAG** (*retrieval-augmented generation*): antes de investigar ou
responder, ele **recupera** da memória o que importa e entrega isso ao modelo junto com o
incidente. A diferença para um RAG de livro é que a base não é uma pilha de documentos: é a
memória que o próprio Grolla escreve, mais a documentação dos repositórios.

```mermaid
flowchart LR
  subgraph indexar["Indexar (worker, a cada 30 s)"]
    reg["dossiês · episódios · notas"] --> rec["recorte por seção"]
    md[".md dos repositórios<br/>(no estudo)"] --> rec
    rec --> emb["embeddings locais<br/>(modelo small ou large)"]
    emb --> pg[("Postgres + pgvector<br/>trechos: vetor + texto")]
  end
  subgraph lembrar["Lembrar (cada análise, validação e chat)"]
    inc["alerta + incidente"] --> q["consulta"]
    q --> det["1. determinístico<br/>mesmo serviço e regra"]
    q --> vet["2. vetor<br/>por significado"]
    q --> txt["3. texto<br/>palavras exatas"]
    det --> rrf["fusão de rankings (RRF)"]
    vet --> rrf
    txt --> rrf
    rrf --> ctx["contexto do modelo<br/>dossiês, casos parecidos e trechos da doc"]
  end
  pg --> vet
  pg --> txt
```

1. **Recorte.** Cada dossiê vira um trecho por seção ("Alertas", "Fragilidades conhecidas",
   "Como investigar"…). Cada episódio e cada nota viram um trecho só. Os `.md` dos repositórios
   (READMEs, runbooks) são recortados do mesmo jeito, com o link do arquivo.
2. **Embeddings locais.** Um modelo multilíngue roda **dentro do worker**, com ONNX: o texto não
   sai da máquina e não há fornecedor nem custo por token. O YAML escolhe o tamanho
   (`memory.rag.size: small | large`), e os dois vêm embutidos na imagem.
3. **Índice no Postgres.** Os trechos ficam no mesmo banco, com o vetor (pgvector) e o índice de
   texto em português. Quando um registro muda ou é esquecido, o job reindexa ou tira do índice.
4. **Busca híbrida.** A consulta é o próprio incidente. Primeiro vem o critério determinístico
   (mesmo serviço e mesma regra de alerta, que num incidente é o sinal mais forte). Depois, dois
   rankings: o **vetorial**, que acha o mesmo problema escrito com outras palavras ("timeout
   aguardando conexão" × "esgotamento do pool pela rotina de reconciliação"), e o **textual**,
   que acha nomes exatos (`valor_desconto`, `grolla-lab-db`). Eles são juntados por
   *Reciprocal Rank Fusion*.
5. **Contexto.** O modelo recebe os dossiês do serviço, até 5 casos parecidos e até 3 trechos de
   documentação, num bloco marcado como dado. A ferramenta `recall` faz a mesma busca quando ele
   precisa de mais.

O ganho é medido: o eval de recuperação (`make evals-rag`, sem custo) compara a busca textual
sozinha com a híbrida num corpus com distratores. Sem `memory.rag` no YAML, tudo funciona só com
a busca textual.

Duas decisões atravessam o projeto inteiro:

- **Somente leitura.** O agente sugere ações; quem executa e quem encerra o incidente é uma pessoa.
- **O núcleo decide, o LLM investiga.** Estado, severidade e acionamento são determinísticos;
  o LLM só devolve hipóteses, que entram como eventos.

A **[spec de produto](docs/initial-spec.md)** é a fonte de verdade do *o quê*; as specs vivas
por capacidade ficam em [`openspec/`](openspec/). As decisões que atravessam o projeto, com o racional, estão no
**[registro de decisões](docs/decisoes.md)**.

## 🚀 Features

| Área | O que entrega | Ponto de entrada |
|---|---|---|
| **Webhook de alertas** | Recebe os alertas do SigNoz com HTTP Basic, filtra pelo time e deduplica reenvios | `POST /webhooks/alerts` |
| **Incidentes** | Agrupa alertas do mesmo serviço numa janela de 15 min, aplica as regras de severidade do YAML (só sobe) e muda o estado (Detectado / Informado) | `receive_alerts` |
| **Investigação de todo alerta** | Card "Estou investigando" na hora e, em até 5 min, "Análise pronta": o que avaliou (métricas, logs, traces, deploys), hipóteses com confiança e evidência, plano proposto e botões para o incidente e o SigNoz. Falha, timeout ou orçamento viram **análise parcial**, nunca silêncio | job `run_analyses` · `make evals` |
| **Causa raiz** | Depois da análise inicial, o agente valida cada hipótese em detalhe (confirmada, rejeitada ou inconclusiva) e posta **"Causa raiz"** com um plano de ação concreto, como "fazer rollback para o build X" ou "religar o RDS Y", para o time validar e executar. O plano nunca traz passo de investigação | job `run_verifications` |
| **Ferramentas neutras** | `query_metric` (pontos de 1 min, com o minuto da mudança), `search_logs` (WARN e acima, mensagens agrupadas), `find_traces` e `recent_deploys` (com a mensagem do commit), sobre o MCP do SigNoz (só leitura, lista fechada) e a API de builds do Azure DevOps | `grolla.application.investigation.Toolbox` |
| **Nuvem (AWS)** | `cloud_resources`, `cloud_changes` e `cloud_alarms`, só leitura: estado do RDS, EC2 e ELB do serviço (tag `service=<nome>`), mudanças no CloudTrail e alarmes do CloudWatch | `grolla.adapters.aws.AwsCloudInventory` |
| **Cards no Teams** | Investigando, análise, causa raiz, severidade elevada e normalizado (que fecha a história com a duração, a causa raiz e o plano sugerido), entregues por outbox com retentativa e em ordem por incidente | job periódico `deliver_notifications` |
| **Páginas** | Lista de incidentes com "Precisa de você agora" (cartões dos abertos com a pílula do alarme, a causa, o IC, o próximo passo e o "esquecido?"), encerrados em tabela com causa, duração e post mortem, e filtros por severidade, estado, serviço, alarme, IC, período e texto; contador de alarmes disparando no menu | `GET /incidents` · `GET /incidents/{id}` |
| **War room** | A página do incidente lida de cima para baixo: barra fixa com a pílula do alarme, o ciclo, IC, TTA/TTM/TTR, **um botão** com o próximo passo e "Mais ações" (severidade, juntar, transcrição, descartar, reabrir); cartão **Agora** com a causa raiz num título curto em letra grande e a explicação abaixo (ou a hipótese mais provável), os números e o primeiro passo com o comando para copiar; **Como o Grolla chegou lá** com cada hipótese uma vez e o veredito; linha do tempo com registrar ação, filtros e os eventos de sistema recolhidos; alertas com o link de cada um; no celular, atalhos e o próximo passo fixo embaixo | `GET /incidents/{id}` · `POST /incidents/{id}/actions/{acao}` |
| **Chat com o agente** | Perguntas e pedidos ao agente durante o incidente; ele responde com evidência usando as mesmas ferramentas da investigação, sem executar nada, com teto de tokens por incidente | `POST /incidents/{id}/chat` · job `run_replies` |
| **🧠 Memória viva** | Base de conhecimento do ambiente montada pelo próprio agente (alertas do domínio → serviços → repositórios → pipelines → AWS → integrações nos traces), estudo diário, episódio de cada incidente encerrado, notas ensinadas no chat, "Já vi isso" na análise e reaprender do zero. A página "O que o Grolla sabe" mostra os assuntos ao lado, com o frescor de cada um ("sem estudo" no serviço ainda não estudado) e a busca | `GET /memoria` · jobs `run_studies` e `run_learnings` |
| **🔎 RAG da memória** | Trechos de dossiês, episódios, notas e dos `.md` dos repositórios com embeddings locais (`small` ou `large`) no pgvector; busca híbrida (determinístico → vetor + texto por RRF) no contexto da análise e no `recall` | job `index_memory` · `make evals-rag` |
| **Ferramentas da memória** | `recall` (busca na memória), `read_file` (lê o repositório do serviço), `service_dependencies` (integrações vistas nos traces), `cloud_overview` (arquitetura na nuvem) e `remember` (só no chat) | `grolla.application.investigation.Toolbox` |
| **Acompanhamento da crise** | P0/P1 abertos recebem um ciclo a cada 5 min (tendência, taxa de erro, resumo), card "Atualização" só quando muda e curto a cada 15 min, ciclo antecipado por alerta ou mensagem, proposta de mitigar ou encerrar após 15 min normalizado (quem decide é o time), volta de mitigado para em tratamento quando o alerta dispara de novo e impacto financeiro estimado | jobs `schedule_cycles` e `run_cycles` · seção "Acompanhamento" no war room |
| **Post mortem** | Rascunho blameless ao resolver (métricas TTD/TTA/TTM/TTR e timeline do log, demais seções escritas pelo agente), edição por seção com versões e autor, regenerar seção com instrução, plano de ação com dono, prazo e tipo, ciclo de vida até encerrado e PDF da versão aprovada. Documento com a próxima ação em destaque, os números do incidente, índice com o que falta (sem dono, vencidos, avaliar) e texto em fonte de leitura; lista com totais (rascunhos, em revisão, vencidos, encerrados no mês), filtros (status, severidade, serviço, vencidos, sem avaliação, período), a causa e a situação do plano | `GET /postmortems` · `GET /postmortems/{id}` · job `run_post_mortems` |
| **Métricas do agente** | Post mortem aprovado reescreve o episódio na memória como confirmado; "era o mesmo problema?" no "já vi isso"; avaliação do agente no post mortem (hipótese certa, minutos, memória ajudou) e a página "Como o Grolla está indo", por semana ou mês, com o período atual em destaque, o gráfico dos resolvidos e o detalhe por período | `GET /metricas` |
| **Menos ruído** | Triagem de todo incidente novo: o agente sugere juntar com outro aberto na janela ("Parece o mesmo problema que…", e quem junta é o time com "Juntar com…") e sobe a severidade quando nenhuma regra casou, com o motivo na timeline | job `run_triages` · `POST /incidents/{id}/juntar` |
| **Chat (console de operação)** | Primeiro item do menu. Conversa com o agente sem incidente aberto, com a lista de conversas ao lado e sugestões que citam os serviços conhecidos: sessões salvas, respostas curtas com evidência, gráficos de taxa de erro, p99 e vazão desenhados no servidor com os dados reais, "Abrir incidente" a partir da conversa e teto diário de tokens próprio | `/console` · fila `console` no worker |
| **Transcrição de call** | Envie o `.vtt`, `.srt` ou texto da call no war room: o agente extrai decisões, ações com dono, horários ditos, participantes e o que a call disse das hipóteses, e tudo entra na timeline e no post mortem com "origem: transcrição" | `POST /incidents/{id}/transcricao` · job `run_transcripts` |
| **AWS completa** | A investigação também lê ECS e EKS, eventos do AWS Health (tolerante a conta sem plano), cotas perto do limite (RDS, vCPUs, balanceadores) e várias contas por uma role somente leitura assumida, com a falha de uma conta isolada | ferramentas `cloud_health` e `cloud_quotas` · `cloud.accounts` |
| **Cluster somente leitura** | Troubleshooting dentro do Kubernetes, em vários clusters (contas PCI e não PCI): estado dos pods (reinícios, OOMKilled, CrashLoop, probes, HPA no teto, eventos) e o que mudou fora do pipeline (revisões, variáveis, recursos, réplicas, ConfigMaps, HPA, NetworkPolicies) com horário e ferramenta (`kubectl-edit`, `helm`…); `enabled` por cluster e `show_values: false` para nenhum valor de configuração do PCI chegar ao LLM | ferramentas `cluster_state` e `cluster_changes` · `clusters` · `deploy/k8s-somente-leitura.yaml` |
| **🛡️ Agente cercado** | O Grolla só investiga: recusa com texto fixo pedidos fora do domínio e pedidos para executar algo; trata o que vem de logs, alertas, repositórios e memória como dado, nunca como ordem; nota ensinada no chat entra como `aprendido` e nota com cara de ordem é recusada; comando destrutivo sugerido ganha o selo "destrutivo, confira antes"; limite de mensagens por pessoa; capacidade só de leitura conferida no `make check` e evals de ataque em `make evals-seguranca` | `GET /console` · `POST /incidents/{id}/chat` · `make evals-seguranca` |
| **Alarme ao vivo no war room** | A pílula ao lado do título diz se o alarme está disparando (vermelha, pulsando de leve), normalizado ("às 15:03 · há 2 min") ou se voltou a disparar; quando muda com a página aberta, aparece um aviso e o título da aba pisca. Barra, botões e alertas se atualizam sozinhos, inclusive quando o alarme reabre um incidente mitigado. Ninguém encerra sozinho | `GET /incidents/{id}` · `GET /incidents/{id}/timeline` |
| **Log de eventos** | Todo fato vira evento imutável (trigger no banco): base da auditoria e da memória do agente | tabela `incident_events` |
| **Configuração por time** | YAML com filtro, labels de serviço, janela e regras; segredos só no ambiente | `GROLLA_CONFIG_FILE` |
| **Health check** | Api saudável só com o banco acessível; worker vivo pelo último heartbeat (2 min) | `GET /health` · `GET /health/worker` |
| **Heartbeat do worker** | Prova de vida gravada no banco a cada 15 s | job periódico `heartbeat` |
| **Deploy manual** | Compose de produção, um YAML de exemplo de time e passo a passo | [`deploy/`](deploy/) |
| **Migrações** | Schema versionado por Alembic, aplicado no startup sob advisory lock | `grolla.adapters.postgres.migrate` |
| **Harness** | Todos os gates num comando só | `make check` |
| **Testes integrados** | Stack completo com fakes dos serviços externos e cenários YAML | `make e2e` |
| **Console de testes** | Dispara alarmes, escolhe o roteiro do LLM fake e mostra os cards do agente como num canal do Teams | `http://localhost:9000/_console` |

> Até aqui: o agente estuda o ambiente e monta a própria base de conhecimento; todo alerta vira incidente com "estou investigando", a análise inicial (com "já vi isso" quando é repetido), a causa raiz validada com um plano de ação concreto, e o time conduz o incidente no war room conversando com o agente — que aprende com cada incidente e com o que o time ensina.
> Acionamento do on-call e post mortem chegam nos próximos slices — ver [Roadmap](#-roadmap).

## 🏛 Arquitetura

**Hexagonal (ports & adapters)**, imposta pelo **import-linter**: dependências só apontam para
dentro, e cada fornecedor externo fica isolado num adapter próprio, porque as ferramentas
(Teams, AWS, SigNoz, Spike.sh…) podem mudar.

```mermaid
flowchart LR
  api["grolla-api<br/>webhooks + web"] --> boot
  worker["grolla-worker<br/>jobs + timers"] --> boot
  boot["bootstrap<br/>liga port → adapter"] --> adapters
  boot --> app
  adapters["adapters/&lt;fornecedor&gt;<br/>postgres · signoz · teams · anthropic · azure_devops · aws · fastembed · system_clock"] --> app
  app["application<br/>casos de uso + ports"] --> dom["domain<br/>regras puras"]
  adapters --> dom
```

| Camada / pacote | Responsabilidade | Pode depender de |
|---|---|---|
| `grolla.domain` | Regras de negócio puras — **sem framework, sem infraestrutura, sem nome de fornecedor** | — |
| `grolla.application` | Casos de uso + ports (`Protocol`) com nome de capacidade | `domain` |
| `grolla.adapters.<fornecedor>` | Implementação de um port; **independentes entre si** | `application`, `domain` |
| `grolla.bootstrap` | Composition root — único lugar que liga port → adapter | todos |
| `grolla-api` / `grolla-worker` | Deployables; cada um com imagem própria | `grolla-core` |
| `grolla-fakes` | Fakes dos serviços externos, só no e2e | — |

**Ports por capacidade, nunca por fornecedor:** `AlertIngress`, `AlertSource`, `Telemetry`,
`ChangeHistory`, `CloudInventory`, `Pager`, `ChatNotifier`, `LanguageModel`, `EventLog`,
`IncidentRepository`, `Scheduler`, `DocumentRenderer` e `Clock`. Cada um nasce no slice que o
usa, com suíte de contrato, fake e adapter real. Hoje existem `Clock`, `AlertIngress` e `Telemetry`
(SigNoz), `ChatNotifier` (Teams), `LanguageModel` (Anthropic), `ChangeHistory` (Azure DevOps),
`CloudInventory` (AWS), `IncidentRepository`, `NotificationOutbox`, `AnalysisQueue`,
`ChatQueue`, `MemoryStore`, `LearningQueue` e `StudyQueue` (Postgres) e `TextEmbedder` (fastembed).

## 🧰 Stack

- **Python 3.12** em **uv workspace** (um lock para tudo).
- **FastAPI** + uvicorn na api; **procrastinate** (fila no próprio Postgres) no worker.
- **PostgreSQL 16** com **pgvector** via **SQLAlchemy Core** + **psycopg 3**, migrações **Alembic**.
- **fastembed** (ONNX Runtime) para os embeddings da memória com RAG, rodando no worker sem rede.
- **pydantic** / **pydantic-settings** para contratos e configuração (`GROLLA_*`).
- **Jinja2** para as páginas server-side; **httpx2** como cliente HTTP dos adapters.
- **Anthropic SDK** (Claude) com laço de ferramentas manual e structured outputs; **mcp** como
  cliente do servidor MCP do SigNoz. Modelo por etapa no YAML, pela API da Anthropic ou pelo
  Amazon Bedrock (`llm.provider: bedrock`, com a role da AWS e sem chave).
- **Docker Compose**: uma imagem por deployable; deploy manual numa EC2, uma instância por time
  ([`deploy/`](deploy/)).

## ⚡ Como rodar

**Pré-requisitos:** [uv](https://docs.astral.sh/uv/), Docker, [openspec](https://github.com/Fission-AI/OpenSpec)
e [gitleaks](https://github.com/gitleaks/gitleaks) (sem o binário, o gate usa a imagem Docker).

```bash
# 1. Dependências + git hooks (uma vez por clone)
make setup

# 2. Todos os gates (o Testcontainers sobe o próprio Postgres)
make check

# 3. Stack completo com os fakes + cenários e2e
make e2e
```

Para desenvolver contra um banco local:

```bash
docker compose up -d db    # Postgres em localhost:5433 (grolla/grolla)
export GROLLA_DATABASE_URL=postgresql://grolla:grolla@localhost:5433/grolla
uv run grolla-api          # http://localhost:8000/health
uv run grolla-worker       # grava um heartbeat a cada 15 s
```

| Variável | Obrigatória | Padrão | Uso |
|---|---|---|---|
| `GROLLA_DATABASE_URL` | sim | — | URL libpq do Postgres (`postgresql://…`) |
| `GROLLA_LOG_LEVEL` | não | `INFO` | `DEBUG`, `INFO`, `WARNING` ou `ERROR` |

Sem uma variável obrigatória, o processo não sobe e a mensagem diz qual falta.

## 🗂 Layout

```
packages/
  grolla-core/     domain · application · adapters · bootstrap · testing   # biblioteca
  grolla-api/      deployable HTTP (FastAPI)
  grolla-worker/   deployable de jobs (procrastinate)
e2e/
  fakes/           grolla-fakes: substitutos controláveis dos serviços externos
  scenarios/       cenários YAML (given / when / then)
  test_scenarios.py  runner: um teste por cenário
evals/             evals do LLM (a partir do slice de investigação)
docs/              initial-spec.md (spec de produto) · assets/
openspec/          changes, specs vivas e config do fluxo spec-driven
scripts/           gates próprios (vocabulário, pisos de cobertura) e e2e.sh
hooks/             git hooks versionados
```

## 🛡 Gates de qualidade — o harness

Tudo abaixo roda em **`make check`** e precisa estar verde, nos pacotes, em `e2e/` e em `scripts/`:

- **ruff format** — a formatação é a fonte de verdade (`make fmt` corrige).
- **ruff check** — zero warnings, incluindo as regras de segurança do bandit (`S`).
- **pyright strict** — tipos em todo o código, testes incluídos.
- **import-linter** — camadas, núcleo sem libs de infraestrutura, adapters independentes,
  deployables sem dependência mútua.
- **Vocabulário** — nome de fornecedor em `domain`/`application` quebra o build.
- **pytest** — unitários **sem rede** (pytest-socket) + integração com **Testcontainers**;
  warnings viram erro.
- **Cobertura com branch** — pisos de **90% linha / 80% branch** em `grolla.domain` e
  **80% / 70%** em `grolla.application`.
- **pip-audit** — CVEs conhecidas nas dependências travadas no `uv.lock`.
- **gitleaks** — segredos no repositório.
- **openspec validate --strict** — changes e specs bem formadas.

Os hooks reforçam localmente: `pre-commit` roda format + lint, `pre-push` roda `make check`.

## 🧪 Testes integrados (e2e)

`make e2e` constrói as imagens, sobe **api + worker + db + grolla-fakes** (profile `e2e` do
compose), roda os cenários e derruba tudo — mesmo se algo falhar.

Nenhum serviço externo real é chamado: o `grolla-fakes` faz o papel de SigNoz, Anthropic,
Teams, Spike.sh e MCPs. O runner reinicia os fakes, carrega o roteiro do cenário, executa as
ações e confere os efeitos (respostas, linhas no banco, requisições recebidas pelos fakes).

```yaml
# e2e/scenarios/fumaca.yaml
name: fumaça do stack
given:
  fakes: {}
when: []
then:
  - expect_status: { service: api, path: /health, status: 200 }
  - wait_for_db_row: { sql: SELECT count(*) FROM heartbeats, at_least: 1, timeout_seconds: 60 }
```

Cenário novo é só um YAML novo em `e2e/scenarios/`.

**Testar à mão:** o console de testes do `grolla-fakes` dispara alarmes e mostra os cards do
agente como num canal do Teams.

```bash
docker compose --profile e2e --project-name grolla-demo up -d --build --wait
# http://localhost:9000/_console  ·  incidentes em http://localhost:8000/incidents
```

## 🧭 Spec-driven development

Mudança de comportamento passa pelo **[OpenSpec](openspec/)** — é obrigatório, e
`openspec/specs/` nunca é editado à mão:

1. `/opsx:propose "<ideia>"` → cria a change com `proposal.md`, `specs/`, `design.md`, `tasks.md`.
2. `/opsx:apply` → implementa as tarefas.
3. `/opsx:archive` → arquiva a change e promove os deltas para `openspec/specs/`.

Toda change fecha revisando os READMEs, com `make e2e` e `make check` verdes.

## 🤝 Como contribuir

1. **Branch** a partir da `master` — nunca commite direto nela.
2. **Ative os hooks** uma vez por clone: `make setup`.
3. **Simplicidade primeiro** — a solução mais simples que cumpre a spec; sem abstração especulativa.
4. **Respeite as fronteiras** — fornecedor só dentro de `grolla.adapters.<fornecedor>`.
5. **Mantenha os gates verdes** — nunca desligue regra, suprima aviso ou baixe piso para passar.
6. **Atualize os READMEs** dos componentes que você tocou.
7. **Mais de uma área na mesma change?** Ela roda com um agente por área e um agente pai que
   fecha os contratos, integra e roda o e2e (ver `CLAUDE.md`).
8. **Commits** — uma linha imperativa curta, em português; `make check` antes do push.

## 🗺 Roadmap

Construído em **slices de valor**: cada um fecha uma funcionalidade de ponta a ponta e vai para
produção ([`openspec/roadmap.md`](openspec/roadmap.md)):

- **S1 — Fundação** · workspace, harness, esqueleto da api e do worker, e2e com fakes ✅
- **S2 — Alertas viram incidentes** · deduplicados, agrupados, com severidade, card no Teams, página do incidente e console de testes ✅ (aceite em produção junto com o S3)
- **S3 — O agente investiga cada alerta** · "estou investigando" em segundos e, em até 5 min, a análise: o que avaliou, hipóteses com evidência, plano proposto e botão para o incidente ✅ (aceite em produção junto com o S2)
- **S3.1 — Até a causa raiz** · validação de cada hipótese, causa raiz com plano de ação concreto e consulta somente leitura à AWS (o S11 adiantado com escopo mínimo) ✅
- **War room com o agente** · ações humanas (IC, severidade, mitigar, encerrar com motivo), TTA/TTM/TTR, cards no Teams e chat com o agente que investiga sob demanda ✅ (adiantado do S4/S5)
- **S3.2 — Memória viva** · base de conhecimento do ambiente estudada pelo agente, estudo diário, episódios, aprendizado no chat, "já vi isso" e reaprender do zero ✅ (substitui o antigo S7)
- **S3.3 — Memória com RAG** · embeddings locais (`small`/`large`), pgvector, documentação dos repositórios indexada e busca híbrida medida por eval ✅
- **S4 — On-call acionado sozinho** · Spike.sh para P0/P1 (começa em modo sombra) — adiado até haver conta no Spike.sh
- **S5 — Agente acompanha a crise** · ciclos em P0/P1, updates só quando muda, impacto financeiro estimado e propostas de mitigar e encerrar ✅
- **S6 — Post mortem em minutos** · rascunho automático, edição, aprovação e PDF ✅
- **S7 — Post mortem alimenta a memória** · post mortems aprovados viram conhecimento e métricas de acerto do agente por período ✅
- **S8 — Menos ruído** · triagem pelo agente, sugestão de junção e severidade sugerida ✅
- **S9 — Console de operação** · conversa fora de incidente, gráficos e abrir incidente pela conversa ✅
- **S10 — Transcrição de call** · decisões, ações, horários e hipóteses da call na timeline ✅
- **S11 — AWS completa** · ECS/EKS, Health, cotas e várias contas ✅
- **Cluster somente leitura** · estado e mudanças no Kubernetes, vários clusters, PCI sem valores ✅
- **Alarme normalizado na tela** · faixa ao vivo no war room, aviso e título piscando, selo na lista ✅
- **War room redesenhado** · barra fixa, cartão Agora, hipóteses com veredito, linha do tempo filtrável ✅
- **Lista de incidentes com filtros** · o que precisa de você agora, encerrados com a causa, filtros ✅
- **Post mortem redesenhado** · totais e filtros na lista, próxima ação, índice com o que falta ✅
- **Chat, memória e métricas redesenhados** · conversas ao lado, assuntos com o frescor, período atual em destaque ✅
- **Agente cercado** · só investiga e só fala do domínio: recusas fixas, dados externos marcados, memória sem envenenamento, selo destrutivo, evals de ataque ✅
- **Modelo no Bedrock** · o mesmo Claude pelo Amazon Bedrock da conta do time, com a role da AWS e sem chave, escolhido no YAML ✅
- **S4 — On-call acionado sozinho** · aguardando conta no Spike.sh
- **S12 — Times, login e administração** · times ligados ao domínio de alarmes (cada um com os seus alarmes e métricas, tudo separado), login com Entra ID, painel admin com perfis e troca de time para quem participa de mais de um

## 📄 Licença

Ainda sem arquivo de licença — todos os direitos reservados ao autor até que um seja adicionado.

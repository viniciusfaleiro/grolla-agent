<div align="center">

<img src="docs/assets/grolla.svg" alt="Grolla, o mascote do agent-grolla" width="160">

# agent-grolla

**Agente de SRE para adquirência e cartões: detecta, investiga, aciona o on-call e conduz o incidente até o post mortem — chegando antes do humano, com as evidências organizadas.**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-workspace-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![FastAPI](https://img.shields.io/badge/FastAPI-api-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-4169E1?logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)](docker-compose.yml)
[![HTMX](https://img.shields.io/badge/HTMX-war%20room-3366CC?logo=htmx&logoColor=white)](https://htmx.org/)

[![Claude](https://img.shields.io/badge/LLM-Claude%20(Anthropic)-D97757?logo=anthropic&logoColor=white)](https://www.anthropic.com/)
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
[![Status](https://img.shields.io/badge/status-acompanha%20a%20crise%20%E2%9C%93-brightgreen)](#-roadmap)

</div>

---

## ✨ O que é

O `agent-grolla` acompanha o ambiente de adquirência e cartões 24x7. Ele recebe os alertas do
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
| **Páginas** | Lista de incidentes e página com estado, severidade, análise completa, alertas e timeline | `GET /incidents` · `GET /incidents/{id}` |
| **War room** | Na página do incidente: assumir como IC, mudar severidade, registrar ação, descartar hipótese, mitigar, reabrir e encerrar ou descartar com motivo. Mostra duração, TTA/TTM/TTR e o autor de cada ação; cada ação vira evento e card no Teams | `POST /incidents/{id}/actions/{acao}` |
| **Chat com o agente** | Perguntas e pedidos ao agente durante o incidente; ele responde com evidência usando as mesmas ferramentas da investigação, sem executar nada, com teto de tokens por incidente | `POST /incidents/{id}/chat` · job `run_replies` |
| **🧠 Memória viva** | Base de conhecimento do ambiente montada pelo próprio agente (alertas do domínio → serviços → repositórios → pipelines → AWS → integrações nos traces), estudo diário, episódio de cada incidente encerrado, notas ensinadas no chat, "Já vi isso" na análise e reaprender do zero | `GET /memoria` · jobs `run_studies` e `run_learnings` |
| **🔎 RAG da memória** | Trechos de dossiês, episódios, notas e dos `.md` dos repositórios com embeddings locais (`small` ou `large`) no pgvector; busca híbrida (determinístico → vetor + texto por RRF) no contexto da análise e no `recall` | job `index_memory` · `make evals-rag` |
| **Ferramentas da memória** | `recall` (busca na memória), `read_file` (lê o repositório do serviço), `service_dependencies` (integrações vistas nos traces), `cloud_overview` (arquitetura na nuvem) e `remember` (só no chat) | `grolla.application.investigation.Toolbox` |
| **Acompanhamento da crise** | P0/P1 abertos recebem um ciclo a cada 5 min (tendência, taxa de erro, resumo), card "Atualização" só quando muda e curto a cada 15 min, ciclo antecipado por alerta ou mensagem, proposta de mitigar ou encerrar após 15 min normalizado (quem decide é o time), volta de mitigado para em tratamento quando o alerta dispara de novo e impacto financeiro estimado | jobs `schedule_cycles` e `run_cycles` · seção "Acompanhamento" no war room |
| **Log de eventos** | Todo fato vira evento imutável (trigger no banco): base da auditoria e da memória do agente | tabela `incident_events` |
| **Configuração por time** | YAML com filtro, labels de serviço, janela e regras; segredos só no ambiente | `GROLLA_CONFIG_FILE` |
| **Health check** | Api saudável só com o banco acessível; worker vivo pelo último heartbeat (2 min) | `GET /health` · `GET /health/worker` |
| **Heartbeat do worker** | Prova de vida gravada no banco a cada 15 s | job periódico `heartbeat` |
| **Deploy manual** | Compose de produção, config da Credenciadora e passo a passo | [`deploy/`](deploy/) |
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
  cliente do servidor MCP do SigNoz. Modelo por etapa no YAML.
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
- **S6 — Post mortem em minutos** · rascunho automático, edição, aprovação e PDF
- **S7 — Post mortem alimenta a memória** · post mortems aprovados viram conhecimento e métricas de acerto do agente por período
- **S8–S11** · menos ruído (agrupamento/triagem pelo LLM) · console de operação · transcrição de call · AWS completa (ECS/EKS, quotas, Health, multi-conta)

## 📄 Licença

Ainda sem arquivo de licença — todos os direitos reservados ao autor até que um seja adicionado.

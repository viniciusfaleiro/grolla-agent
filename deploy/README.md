<div align="center">

<img src="../docs/assets/grolla.svg" alt="Grolla" width="96">

# deploy

**Deploy manual de uma instância do agent-grolla numa EC2: compose de produção, configuração do time e passo a passo.**

[![Docker Compose](https://img.shields.io/badge/deploy-docker%20compose-2496ED?logo=docker&logoColor=white)](compose.yml)
[![Kind](https://img.shields.io/badge/tipo-manual-orange)](#-primeiro-deploy)

</div>

---

## ✨ O que é

Tudo o que uma instância precisa para rodar fora do ambiente de desenvolvimento: Postgres, api,
worker e o servidor MCP do SigNoz num compose só, construídos a partir deste repositório na própria máquina. Uma instância
atende um time; a primeira é a da **Credenciadora**. ECR e CI entram quando o deploy deixar de
ser manual.

| Arquivo | Papel |
|---|---|
| [`compose.yml`](compose.yml) | `db`, `api`, `worker` e `signoz-mcp` com `restart: unless-stopped` e volume do banco |
| [`credenciadora.yaml`](credenciadora.yaml) | Configuração do time: filtro, labels de serviço, janela, regras de severidade e a investigação (modelo, limites, SigNoz, Azure DevOps) |
| [`.env.example`](.env.example) | Modelo do `.env` com os segredos (o `.env` nunca vai para o git) |

## 📋 Pré-requisitos

- EC2 na rede interna com Docker e o plugin Compose, e acesso ao repositório.
- Saída HTTPS da EC2 para o Teams (Workflow do canal) e entrada do SigNoz na porta da api.
- A URL do Workflow do Teams do canal do time ("Post to a channel when a webhook request is
  received").
- Para a investigação (S3):
  - **chave da API da Anthropic**;
  - **API key do SigNoz de papel Viewer**, sem permissão de escrita;
  - **PAT do Azure DevOps somente leitura**, com escopos `Build (Read)` e `Code (Read)`.
- Saída da EC2 para `api.anthropic.com`, para a instância do SigNoz e para `dev.azure.com`.

## 🔎 Investigação

Cada incidente novo recebe dois cards: "Estou investigando", na hora, e "Análise pronta" (ou
"Análise parcial") em até `investigation.timeout_seconds`. A análise usa:

| Fonte | Como | Configuração |
|---|---|---|
| **LLM** | API da Anthropic, modelo da etapa `investigation` | `llm.stages.investigation` · `GROLLA_ANTHROPIC_API_KEY` |
| **SigNoz** | Container `signoz-mcp` (servidor MCP oficial), só na rede interna. O agente chama apenas ferramentas de leitura de uma lista fechada | `signoz.mcp_url` e `signoz.base_url` · `GROLLA_SIGNOZ_URL` e `GROLLA_SIGNOZ_API_KEY` |
| **Azure DevOps** | API REST de builds, para os deploys recentes | `azure_devops.base_url` e `projects` · `GROLLA_AZURE_DEVOPS_TOKEN` |

Não é preciso mapear todos os serviços. Sem entrada em `service_repo_map`, o agente procura um
repositório com o nome do serviço nos `projects`. Só as exceções vão no mapa. Se não achar, a
análise registra "serviço sem repositório mapeado" e segue.

Se o LLM, o SigNoz ou o Azure DevOps falharem, o time recebe a **análise parcial** com o que deu
para avaliar. O custo fica em `llm_usage`:

```bash
docker compose exec db psql -U grolla -c \
  "SELECT date(at), model, sum(input_tokens), sum(output_tokens) FROM llm_usage GROUP BY 1, 2 ORDER BY 1 DESC"
```

## 🚀 Primeiro deploy

```bash
git clone <repositório> agent-grolla && cd agent-grolla/deploy
git checkout master                     # ou o commit da versão a subir
cp .env.example .env && chmod 600 .env  # preencha senhas e a URL do Teams
docker compose up -d --build
docker compose ps                       # api healthy, worker running
curl -fsS localhost:8000/health         # {"status":"ok"}
curl -fsS localhost:8000/health/worker  # 200 com o último heartbeat
```

As migrações do banco rodam sozinhas no startup da api e do worker.

## 🔔 Webhook no SigNoz

1. Em **Settings → Alert Channels → New channel**, escolha o tipo **Webhook**.
2. URL: `http://<host-da-instância>:8000/webhooks/alerts`.
3. Usuário e senha: os valores de `GROLLA_ALERTS_WEBHOOK_USER` e `GROLLA_ALERTS_WEBHOOK_PASSWORD`.
4. Ligue o canal nas regras de alerta do time. Mande um alerta de teste e confira o card no
   Teams e o incidente em `http://<host>:8000/incidents`.

## 🏷️ Labels dos alertas

Os nomes dos labels são todos configuráveis em `credenciadora.yaml`:

- `alert_filter`: só entram alertas que têm **todos** esses labels. Os outros são ignorados.
- `service_labels`: o serviço do alerta é o primeiro desses labels que estiver presente. Sem
  nenhum deles, o alerta é agrupado sob `desconhecido`.
- `severity_rules[].match`: casam com qualquer label do alerta, incluindo `alertname`.

Para descobrir os labels reais, mande um alerta para a instância e abra a página do incidente,
que mostra os labels de cada alerta. Ajuste o YAML e rode `docker compose up -d` para recriar a
api (a configuração é lida no startup). Se o filtro estiver errado, nada entra. Para ver todos os
alertas, comece com `alert_filter: {}`.

## 🔄 Atualizar

```bash
cd agent-grolla && git pull            # ou git checkout <commit>
cd deploy && docker compose up -d --build
```

Rollback: volte para o commit anterior e rode o mesmo comando. As migrações só avançam, então
volte só para commits que já rodaram com o schema atual. Na dúvida, restaure o backup.

## 💾 Backup do banco

O log de eventos é a memória do agente, então o backup é diário. Exemplo de cron na EC2 (ajuste o
bucket):

```bash
# /etc/cron.d/grolla-backup — 03:00 todo dia
0 3 * * * ec2-user cd /home/ec2-user/agent-grolla/deploy && docker compose exec -T db pg_dump -U grolla -Fc grolla | aws s3 cp - s3://<bucket>/grolla/credenciadora/$(date +\%F).dump
```

Restaurar numa instância vazia:

```bash
aws s3 cp s3://<bucket>/grolla/credenciadora/<data>.dump - \
  | docker compose exec -T db pg_restore -U grolla -d grolla --clean --if-exists
```

## 🩺 Monitoramento

- `GET /health`: a api está de pé e alcança o banco.
- `GET /health/worker`: 503 se o worker não grava um heartbeat há mais de 2 minutos. Aponte uma
  checagem de URL do SigNoz para cá: um worker parado significa cards represados na outbox.
- Logs: `docker compose logs -f api worker signoz-mcp`.

## ⏪ Desligar

Desligue o canal de webhook no SigNoz. O banco guarda tudo o que já entrou. `docker compose down`
para os processos e mantém o volume.

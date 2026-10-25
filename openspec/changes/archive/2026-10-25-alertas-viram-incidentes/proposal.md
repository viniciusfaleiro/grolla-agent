## Why

Hoje o time da Credenciadora recebe cada alerta solto, inclusive repetido, e não tem um lugar com o histórico do problema. O S2 do roadmap transforma alertas em **incidentes**: deduplicados, agrupados, com severidade, um card no Teams por problema e uma página por incidente. Para P2/P3 a funcionalidade da spec de produto termina aqui. É também o primeiro slice em produção e o início da memória do agente: cada incidente fica gravado com detalhe suficiente para comparar casos no futuro.

## What Changes

- **Webhook de alertas** no `grolla-api`, autenticado, recebendo o formato do SigNoz (compatível com Alertmanager), com alertas disparando e resolvidos.
- **Filtro por time**: a instância só aceita alertas com os labels do time configurado (`team: credenciadora`); os demais são ignorados.
- **Deduplicação**: o mesmo alerta reenviado (mesmo fingerprint e mesmo início) não gera nada novo.
- **Agrupamento determinístico**: alerta do mesmo serviço de um incidente com atividade nos últimos 15 minutos entra nele; senão abre um incidente novo.
- **Severidade por regras** no YAML do time (casando labels do alerta), com severidade padrão quando nenhuma regra casa. A severidade do incidente é a pior entre os seus alertas e só sobe automaticamente.
- **Estados**: incidente nasce em **Detectado**; P2/P3 vão para **Informado**. P0/P1 ficam em Detectado até o acionamento chegar (S3).
- **Cards no Teams**: incidente novo (anomalia, evidências, severidade, serviços, link da página), severidade elevada e "normalizou" quando todos os alertas resolvem. Entrega por **outbox** com retentativa: nenhum card se perde se o Teams falhar.
- **Log de eventos imutável** por incidente, com o alerta normalizado e o bruto. Os incidentes são projeções desse log.
- **Páginas web** só leitura: lista de incidentes e página do incidente (estado, severidade, serviços, alertas e timeline), para a rede interna.
- **Saúde do worker**: `GET /health/worker` responde 503 se o último heartbeat tiver mais de 2 minutos, para o monitoramento externo pegar o worker parado.
- **Configuração por time** em YAML (`GROLLA_CONFIG_FILE`), segredos só em variáveis de ambiente.
- **Pacote de deploy manual** em `deploy/`: compose de produção, config da Credenciadora, `.env.example` e passo a passo (incluindo backup do banco).

## Non-goals

- Acionamento do Spike.sh, ações humanas no war room e encerramento de incidente (S3).
- Agrupamento por dependência no grafo de traces: precisa do port `Telemetry`, que nasce no S4. No S2 o agrupamento é só por serviço.
- Poller da API de alertas do SigNoz como rede de segurança: entra quando houver um caso real de webhook perdido; no S2 a entrega do webhook é monitorada pelo próprio SigNoz.
- Severidade sugerida pelo LLM e agrupamento pelo LLM (S8).
- Atualizar um card já postado: o Workflow do Teams só posta mensagens novas.
- Página ao vivo (SSE) e autenticação: a página recarrega manualmente e fica só na rede interna (SSO na Fase 2).
- Deploy automatizado e CI.

## Capabilities

### New Capabilities

- `alert-intake`: recebimento, autenticação, filtro por time e deduplicação de alertas.
- `incident-grouping`: agrupamento de alertas em incidentes e ciclo de estados do S2.
- `severity-rules`: regras de severidade por time e escalonamento.
- `incident-notifications`: cards no Teams e entrega por outbox.
- `incident-pages`: lista e página do incidente.
- `team-config`: configuração da instância por YAML e segredos por ambiente.

### Modified Capabilities

- `service-skeleton`: o `grolla-api` ganha `GET /health/worker`; o `grolla-worker` ganha o job de entrega de notificações.

## Impact

- **Código**: domínio de incidentes e casos de uso no `grolla-core`; adapters `signoz`, `teams` e repositório de incidentes no `postgres`; rotas e templates no `grolla-api`; job de outbox no `grolla-worker`; protocolo do Teams no `grolla-fakes`; cenários e2e.
- **Banco**: migração 0002 (incidentes, eventos, alertas, outbox).
- **Dependências novas**: `jinja2` (páginas), `pyyaml` (config), `httpx2` como cliente HTTP dos adapters.
- **Externo**: webhook configurado no SigNoz apontando para a instância; URL do Workflow do Teams do canal da Credenciadora; alertas com labels `team` e `service_name`.
- **Spec de produto**: seções "Gatilhos e detecção", "Severidade e acionamento" (regras), "Ciclo de vida do incidente" (Detectado, Informado), "Mensagens no Teams" e "Configuração por deployment" de `docs/initial-spec.md`.

> Change multiagente: domínio, persistência, integrações, web e e2e em paralelo a partir do commit de contrato.

## 0. Contratos (agente pai)

- [x] 0.1 Dependências: `jinja2` e `pyyaml` no `grolla-api`/`grolla-core`, `httpx2` no `grolla-core`; `uv lock`.
- [x] 0.2 `grolla.domain.incidents` com os modelos e as assinaturas do design (corpos `raise NotImplementedError`).
- [x] 0.3 Ports, `OutboxItem`, exceções, `IntakePolicy` e assinaturas dos casos de uso em `grolla.application`.
- [x] 0.4 Migração `0002` (incidents, incident_events com trigger de imutabilidade, alerts, outbox).
- [x] 0.5 `TeamConfig` (YAML), settings por processo e `prepare_api()` / `prepare_worker()` no `bootstrap`, ligando os adapters ainda vazios.
- [x] 0.6 Compose do e2e: config YAML de teste, credenciais do webhook na api e `GROLLA_TEAMS_WEBHOOK_URL` do worker apontando para o `grolla-fakes`.
- [x] 0.7 Fixture de payload do webhook do SigNoz (disparando e resolvido) em `packages/grolla-core/tests/fixtures/`.
- [x] 0.8 `make check` verde com os stubs e commit de contrato.

## 1. grolla-dominio

- [ ] 1.1 `worst` e `SeverityPolicy.classify` (primeira regra que casa, padrão, índice da regra).
- [ ] 1.2 `decide_firing`: novo incidente, agrupamento por serviço na janela ou com alerta disparando, candidato mais recente, escalonamento de severidade, estados Detectado/Informado, eventos e notificações.
- [ ] 1.3 `decide_resolved`: registra a resolução e a normalização quando nada mais dispara.
- [ ] 1.4 `receive_alerts`: filtro de labels, deduplicação via `register_alert`, uma transação por alerta.
- [ ] 1.5 `deliver_notifications`: entrega em ordem, retentativa com espera crescente, desistência após o limite, adiamento dos cards seguintes do mesmo incidente.
- [ ] 1.6 Fakes em memória de `IncidentRepository`, `NotificationOutbox`, `ChatNotifier` e `AlertIngress` em `grolla.testing`, com as suítes de contrato de cada port em `tests/contracts/`.
- [ ] 1.7 Pisos de cobertura do domain e da application atingidos; `make check` verde no worktree.

## 2. grolla-persistencia

- [ ] 2.1 `PostgresIncidentRepository` e unidade de trabalho transacional.
- [ ] 2.2 `register_alert` sobre a `UNIQUE` da tabela `alerts`.
- [ ] 2.3 `PostgresNotificationOutbox` com os eventos de entrega e de desistência.
- [ ] 2.4 Integração com Postgres real passando nas suítes de contrato do repositório e da outbox; teste do trigger que recusa `UPDATE`/`DELETE` em `incident_events`.
- [ ] 2.5 README do `grolla-core` (tabelas e repositórios); `make check` verde no worktree.

## 3. grolla-integracoes

- [ ] 3.1 `grolla.adapters.signoz`: autenticação Basic em tempo constante, parse para `Alert` neutro, `InvalidAlertPayloadError`, serviço `desconhecido`; testes com a fixture.
- [ ] 3.2 `grolla.adapters.teams`: Adaptive Card por tipo de notificação, envio via `httpx2`, `NotificationFailedError` em erro HTTP ou de rede; testes com transporte fake.
- [ ] 3.3 Os dois adapters passando nas suítes de contrato dos ports.
- [ ] 3.4 Job periódico `deliver_notifications` (a cada 5 s, `queueing_lock`) no `grolla-worker` e README do worker.
- [ ] 3.5 `make check` verde no worktree.

## 4. grolla-web

- [ ] 4.1 `POST /webhooks/alerts`: 401 sem credencial, 422 em payload inválido, 202 chamando `receive_alerts`.
- [ ] 4.2 `GET /incidents` e `GET /incidents/{id}` com Jinja2 (404 para inexistente).
- [ ] 4.3 `GET /health/worker` com o limite de 2 minutos.
- [ ] 4.4 Testes com `TestClient` e fakes dos ports; README do `grolla-api`; `make check` verde no worktree.

## 5. grolla-e2e

- [ ] 5.1 Ação `expect_fake_requests` no runner.
- [ ] 5.2 Cenários: alerta novo vira incidente e card; reenvio não duplica; dois alertas do mesmo serviço agrupam; serviço diferente abre outro incidente; alerta de outro time é ignorado; alerta mais grave escala e gera card; último alerta resolvido gera card de normalização; webhook sem credencial é 401.
- [ ] 5.3 `make check` verde no worktree.

## 6. Integração (agente pai)

- [ ] 6.1 Juntar os branches dos agentes e resolver conflitos.
- [ ] 6.2 Pacote `deploy/`: `compose.yml` de produção, `credenciadora.yaml`, `.env.example` e `README.md` com o passo a passo do deploy manual, a configuração do webhook no SigNoz e o backup do banco.
- [ ] 6.3 Registrar no `design.md` os desvios de contrato combinados entre os agentes.
- [ ] 6.4 Revisar e atualizar os READMEs do pai e de cada componente (features, roadmap e badge de status).
- [ ] 6.5 Rodar `make e2e` e garantir todos os cenários verdes.
- [ ] 6.6 Rodar `make fmt && make check` e garantir tudo verde.

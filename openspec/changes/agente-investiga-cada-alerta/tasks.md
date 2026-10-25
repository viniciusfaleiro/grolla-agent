> Change multiagente. Domínio, persistência, duas frentes de integrações (LLM e observabilidade), web e e2e trabalham em paralelo a partir do commit de contrato.

## 0. Contratos (agente pai)

- [x] 0.1 Dependências: `anthropic` e `mcp` no `grolla-core`, `mcp` no `grolla-fakes`; `uv lock`.
- [x] 0.2 `grolla.domain.analysis`, as alterações em `grolla.domain.incidents` (eventos, `ANALYSIS_READY`, `Notification.analysis`, `Decision.request_analysis`) e as assinaturas do design.
- [x] 0.3 Ports, `Toolbox`, `TokenBudget`, modelos e exceções em `grolla.application.investigation` e `grolla.application.analysis`; `request_analysis` e `analysis_of` nos ports de incidentes. Corpos com `raise NotImplementedError`.
- [x] 0.4 Stubs dos adapters: `grolla.adapters.anthropic`, `grolla.adapters.signoz` (Telemetry), `grolla.adapters.azure_devops` e `PostgresAnalysisQueue`.
- [x] 0.5 Migração `0003` (`analyses` e `llm_usage`).
- [x] 0.6 `TeamConfig` com `investigation`, `llm`, `signoz` e `azure_devops`; `WorkerSettings` com os segredos novos e `GROLLA_CONFIG_FILE`; `prepare_worker()` ligando os adapters ainda vazios.
- [x] 0.7 Compose do e2e: config do e2e (timeout curto e URLs dos fakes) e variáveis do worker apontando para o `grolla-fakes`.
- [x] 0.8 `make check` verde com os stubs e commit de contrato.

## 1. grolla-dominio

- [ ] 1.1 `complete` e `partial` (hipóteses ordenadas por confiança) e `decide_firing` com `request_analysis` só em incidente novo; `receive_alerts` chamando `request_analysis` e gravando `analysis_requested`.
- [ ] 1.2 `Toolbox`: specs das quatro ferramentas com JSON Schema, `call` que nunca levanta (erro vira "não foi possível consultar") e registro das `Evaluation`s.
- [ ] 1.3 `TokenBudget` e o prompt neutro (`SYSTEM_PROMPT`, `brief_text`) com o roteiro curto do design.
- [ ] 1.4 `run_next_analysis` (timeout, conversão de cada falha em parcial, card `ANALYSIS_READY`) e `reap_stuck_analyses`.
- [ ] 1.5 Fakes em `grolla.testing`: `ScriptedLanguageModel` (chama ferramentas e devolve um rascunho roteirizado, falha ou demora), `FakeTelemetry`, `FakeChangeHistory` e `InMemoryAnalysisQueue`, com as suítes de contrato de cada port em `tests/contracts/`. Commit cedo para os outros agentes.
- [ ] 1.6 Pisos de cobertura; `make check` verde no worktree.

## 2. grolla-persistencia

- [ ] 2.1 `request_analysis` e `analysis_of` no repositório Postgres.
- [ ] 2.2 `PostgresAnalysisQueue`: `claim` com `FOR UPDATE SKIP LOCKED`, `stuck` e `finish` numa transação (análise, eventos, outbox e `llm_usage`).
- [ ] 2.3 Integração com Postgres real passando nas suítes de contrato da fila e do repositório; teste de duas reivindicações simultâneas que não pegam o mesmo pedido.
- [ ] 2.4 README do `grolla-core` (tabelas novas); `make check` verde no worktree.

## 3. grolla-integracoes (LLM)

- [ ] 3.1 `grolla.adapters.anthropic`: laço manual de ferramentas assíncrono, structured outputs para o `AnalysisDraft`, `effort` e modelo do YAML, thinking adaptativo, `cache_control` de 1 h, `budget.spend` a cada resposta e erros traduzidos para `LanguageModelError`/`InvalidAnalysisError`. Seguir a documentação do SDK (skill `claude-api`), sem adivinhar a API.
- [ ] 3.2 Testes do adapter contra a Messages API fake (transporte fake), passando na suíte de contrato de `LanguageModel`.
- [ ] 3.3 Fake da Messages API no `grolla-fakes` (`/anthropic/v1/messages`) com os modos `completa`, `fora_do_ar` e `lenta`.
- [ ] 3.4 Card de análise no `grolla.adapters.teams` (resumo, avaliações, hipóteses, plano, parcial, botões).
- [ ] 3.5 Worker: dois laços do procrastinate (filas `default` e `analysis`), jobs `run_analyses` e `reap_stuck_analyses`; README do worker.
- [ ] 3.6 `evals/`: três casos roteirizados (deploy ruim, dependência lenta, sem causa clara) com a hipótese esperada e `make evals` com o modelo real; README do `evals/`.
- [ ] 3.7 `make check` verde no worktree.

## 4. grolla-integracoes (observabilidade)

- [ ] 4.1 `grolla.adapters.signoz` como `Telemetry`:
  - cliente MCP streamable HTTP com o header `SIGNOZ-API-KEY`;
  - lista fechada de ferramentas de leitura;
  - tradução dos sinais neutros;
  - resumo compacto e link do SigNoz;
  - timeout por chamada.
  Confirmar os nomes e os argumentos das ferramentas no servidor real ou na documentação oficial e versionar as fixtures.
- [ ] 4.2 Teste que prova a recusa de ferramenta de escrita sem chamar o servidor.
- [ ] 4.3 `grolla.adapters.azure_devops` como `ChangeHistory`: repositório pelo mapa ou, sem entrada, pelo nome do serviço nos `projects` (cache em memória); builds desde `since`, PAT, "serviço sem repositório mapeado" e link do pipeline.
- [ ] 4.4 Fakes no `grolla-fakes`: servidor MCP do SigNoz em `/signoz/mcp` (lib `mcp`) e a API de builds do Azure DevOps, com roteiros e padrão plausível.
- [ ] 4.5 Os dois adapters passando nas suítes de contrato; `make check` verde no worktree.

## 5. grolla-web

- [ ] 5.1 Seção de análise na página do incidente (em andamento, concluída, parcial com motivo, avaliações, hipóteses com links, plano).
- [ ] 5.2 Console do `grolla-fakes`: seletor do roteiro do LLM fake (completa, fora do ar, lenta), com o card de análise renderizado no canal.
- [ ] 5.3 Testes; READMEs do `grolla-api` e do `grolla-fakes`; `make check` verde no worktree.

## 6. grolla-e2e

- [ ] 6.1 Cenários:
  - alerta novo gera "investigando" e depois a análise, nessa ordem;
  - LLM fora do ar gera análise parcial;
  - LLM lento gera análise parcial por tempo esgotado;
  - alerta agrupado não reabre a análise;
  - `llm_usage` gravado.
- [ ] 6.2 Ajustar os cenários do S2 ao novo texto do card de incidente novo; `make check` verde no worktree.

## 7. Integração (agente pai)

- [ ] 7.1 Juntar os branches dos agentes e resolver conflitos.
- [ ] 7.2 Code review do todo entregue: consistência entre componentes, duplicação, desvio de contrato, simplicidade, cobertura das specs. Fazer os ajustes que couberem.
- [ ] 7.3 `deploy/`: variáveis novas no `.env.example`, seções novas no `credenciadora.yaml`, container `signoz-mcp` no compose (tag fixa, rede interna, sem porta publicada), e o passo a passo atualizado (API key Viewer, PAT somente leitura).
- [ ] 7.4 Registrar no `design.md` os desvios de contrato combinados entre os agentes.
- [ ] 7.5 Revisar e atualizar os READMEs do pai e de cada componente (features, roadmap e badge de status).
- [ ] 7.6 Rodar `make e2e` e garantir todos os cenários verdes.
- [ ] 7.7 Rodar `make fmt && make check` e garantir tudo verde.

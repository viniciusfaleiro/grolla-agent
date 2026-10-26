> Change multiagente: domínio, persistência, integrações, web e e2e trabalham em paralelo a partir do commit de contrato. Os jobs do worker ficam com o `grolla-dominio`.

## 0. Contratos (agente pai)

- [x] 0.1 `grolla.domain.memory` (assuntos, registros, confiança, fonte, versões, regras com corpos `NotImplementedError`) e `SeenBefore` na análise.
- [x] 0.2 Application, com corpos `NotImplementedError`:
  - modelos e ports: `AlertRule`, `MemoryContext`, `MemoryStore`, `LearningQueue`, `StudyQueue`, briefs e drafts;
  - métodos novos de `Telemetry`, `CloudInventory`, `ChangeHistory` e `LanguageModel`;
  - `request_learning`;
  - memória no `InvestigationBrief` e no `Toolbox`;
  - casos de uso.
- [x] 0.3 Stubs no Postgres, nos adapters Anthropic, SigNoz, AWS e Azure DevOps e nos fakes; migração `0006`.
- [x] 0.4 Config `memory` e etapas `learning`/`study` no `bootstrap`; containers do worker e da api com a memória e as filas.
- [x] 0.5 `make check` verde com os stubs e commit de contrato.

## 1. grolla-dominio

- [x] 1.1 Regras de `domain.memory`: versão guarda o estado anterior; humano confirma; esquecer e restaurar; `relearn_targets`. Validação de `seen_before` contra os episódios entregues.
- [x] 1.2 `run_next_study`:
  - regras do domínio pelo `alert_filter`;
  - serviços das regras e os ativos com repositório;
  - ordem de `service_order`;
  - estudo por serviço com teto próprio e parada no teto da rodada;
  - falha por serviço registrada;
  - síntese com ambiente e integrações;
  - fonte com os links consultados.
  
  `STUDY_PROMPT` (seções do dossiê de serviço, alertas com significado e o que olhar primeiro) e `SYNTHESIS_PROMPT`.
- [x] 1.3 `ensure_initial_study`, `request_study` (completo e reaprender, que esquece com `relearn_targets` antes) e `reap_stuck_studies`.
- [x] 1.4 `run_next_learning` e `reap_stuck_learnings`:
  - episódio por assunto, atualizado ao reencerrar;
  - ruído no descarte;
  - dossiês consolidados e notas marcadas como consolidadas;
  - uma nova tentativa;
  - `LEARNING_PROMPT`.
  
  `request_learning` no encerramento e no descarte.
- [x] 1.5 Memória no brief da análise, da validação e do chat (`memory_text` como dado); "já vi isso" no `SYSTEM_PROMPT`.
- [x] 1.6 Ferramentas `recall`, `read_file`, `service_dependencies`, `cloud_overview` e `remember` (só no chat, com o autor da pergunta); linha "Guardei: …" no `CHAT_PROMPT`.
- [x] 1.7 Casos de uso da página: `add_note`, `edit_memory`, `confirm_memory`, `forget_memory`, `restore_memory`.
- [x] 1.8 Jobs `run_learnings`, `run_studies`, `schedule_daily_study`, `ensure_initial_study` na subida e reapers no `grolla-worker`.
- [x] 1.9 Fakes e suítes de contrato de `MemoryStore`, `LearningQueue`, `StudyQueue` e dos métodos novos dos ports. Commit cedo.
- [x] 1.10 Pisos de cobertura e `make check` verde.

## 2. grolla-persistencia

- [x] 2.1 Migração 0006:
  - `memories`, com `tsvector` em português, índice GIN e os índices únicos;
  - `memory_versions`;
  - `learnings`;
  - `study_runs`;
  - `service_studies`.
- [x] 2.2 `MemoryStore`: `context_for` (ambiente, serviços, integrações citadas, notas e episódios por serviço/regra e depois `ts_rank`), `search`, `save` em transação, listagens da página.
- [x] 2.3 `LearningQueue` e `StudyQueue` com `SKIP LOCKED`; `request_learning` no unit of work.
- [x] 2.4 Integração passando nas suítes de contrato; README do core; `make check` verde.

## 3. grolla-integracoes

- [x] 3.1 Anthropic: `learn` e `synthesize` (saída estruturada, sem ferramentas), `study_service` (laço com ferramentas e fechamento no orçamento), `seen_before` no schema da análise.
- [x] 3.2 SigNoz:
  - `alert_rules` via `signoz_list_alert_rules` e `signoz_get_alert`;
  - `active_services` via `signoz_list_services`;
  - `dependencies` (spans de saída agrupados por `peer.service`, `db.system`/`db.name` e `server.address`);
  - leituras novas no `READ_TOOLS`.
- [x] 3.3 AWS: `overview` pelo Resource Groups Tagging API e os `describe_*` já usados, com o grupo "sem serviço"; política IAM somente leitura atualizada se precisar.
- [x] 3.4 Azure DevOps: `read_file` (arquivo no branch padrão cortado em `READ_LIMIT`, listagem, binário recusado, link).
- [x] 3.5 `grolla-fakes`: os protocolos novos; LLM fake com `learn`/`study_service`/`synthesize` e `seen_before` quando houver episódio; "Já vi isso" no card de análise do Teams.
- [x] 3.6 Suítes de contrato passando; README do `e2e/fakes`; `make check` verde.

## 4. grolla-web

- [x] 4.1 `/memoria`: dossiê do ambiente, assuntos por tipo, estado do último estudo (serviços estudados, pendentes e falhos), aprendizados falhos e os botões "estudar de novo" e "reaprender do zero" (com confirmação).
- [x] 4.2 `/memoria/{subject}`: dossiê, episódios (link do incidente, ruído) e notas, com confiança, fonte e data; histórico de versões com restaurar.
- [x] 4.3 Editar, confirmar, esquecer e adicionar nota, com autor identificado; assunto sem dossiê aceita nota.
- [x] 4.4 "Já vi isso" na análise da página do incidente; link "Memória" na barra.
- [x] 4.5 Testes; README do `grolla-api`; `make check` verde.

## 5. grolla-e2e

- [x] 5.1 Cenário: na subida, o estudo inicial cria o dossiê do serviço (com o significado da regra de alerta), o do ambiente e o de uma integração.
- [x] 5.2 Cenário: encerrar um incidente gera o episódio, e um incidente repetido mostra "Já vi isso" com o link do anterior.
- [x] 5.3 Cenário: nota adicionada na página entra na memória da próxima análise; reaprender esquece o dossiê, mantém a nota confirmada e refaz o estudo; `make check` verde.

## 6. Integração (agente pai)

- [x] 6.1 Juntar os branches e resolver conflitos.
- [x] 6.2 Fazer o code review do todo entregue, com os ajustes que couberem.
- [ ] 6.3 Evals: o mesmo incidente com e sem memória (rodar só com o ok do usuário, por causa do crédito).
- [ ] 6.4 Laboratório, com o ok do usuário para o gasto:
  - estudo inicial do `pedidos-api`, com conferência dos dossiês;
  - encerrar um incidente e conferir o episódio;
  - provocar o mesmo problema e ver o "já vi isso";
  - ensinar algo pelo chat;
  - reaprender do zero.
- [x] 6.5 Roadmap: S3.2 · Memória viva antes do S4; tirar "incidentes parecidos" do S5; o S7 vira "post mortem alimenta a memória e métricas de acerto"; `CLAUDE.md` "Estado atual"; `memory` no `deploy/credenciadora.yaml`.
- [x] 6.6 Registrar no `design.md` os desvios de contrato.
- [x] 6.7 Revisar e atualizar os READMEs do pai e de cada componente.
- [x] 6.8 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 6.9 Rodar `make fmt && make check` e garantir tudo verde.

> Change multiagente: domínio, persistência, integrações, web e e2e trabalham em paralelo a partir do commit de contrato.

## 0. Contratos (agente pai)

- [x] 0.1 `boto3` no `grolla-core`; `uv lock`.
- [x] 0.2 Modelos da validação e das ações em `grolla.domain.analysis`; eventos, `ROOT_CAUSE` e `Notification.verification` em `grolla.domain.incidents`.
- [x] 0.3 Ports e assinaturas na application: `CloudInventory` e `NoCloudInventory`; `Toolbox` com `cloud`; `LanguageModel.verify`; os métodos da validação na `AnalysisQueue` e o `verification_of`; `run_next_verification` e `reap_stuck_verifications`. Corpos com `NotImplementedError`.
- [x] 0.4 Stubs `AwsCloudInventory`, `verify` no adapter da Anthropic e a fila da validação no Postgres; migração `0004`.
- [x] 0.5 `TeamConfig.cloud`, os limites da validação, a etapa `verification` e o `prepare_worker`.
- [x] 0.6 `make check` verde com os stubs; commit de contrato.

## 1. grolla-dominio

- [x] 1.1 `verified`/`verification_partial` e a validação do plano concreto: verbos de investigação recusados e plano vazio sem causa confirmada.
- [x] 1.2 Ferramentas `cloud_resources`, `cloud_changes` e `cloud_alarms` no `Toolbox`, e o `NoCloudInventory`.
- [x] 1.3 `run_next_verification` e `reap_stuck_verifications` (timeout, parcial em toda falha, card `ROOT_CAUSE`).
- [x] 1.4 Prompt da análise inicial (usar a nuvem para dependências; rollback só com o deploy antes do problema) e `VERIFICATION_PROMPT`.
- [x] 1.5 Fakes: `ScriptedLanguageModel.verify`, `FakeCloudInventory` e a fila da validação em memória, com as suítes de contrato. Commit cedo.
- [x] 1.6 Pisos de cobertura e `make check` verde.

## 2. grolla-persistencia

- [x] 2.1 Migração 0004 e a fila da validação (`SKIP LOCKED`, `stuck`, `finish_verification` só em andamento).
- [x] 2.2 `finish` da análise pede a validação quando há hipóteses, na mesma transação; `verification_of`.
- [x] 2.3 Integração passando nas suítes de contrato; README do core; `make check` verde.

## 3. grolla-integracoes

- [x] 3.1 `adapters/aws`: `AwsCloudInventory` (tags, estado de RDS/EC2/ELB, CloudTrail, CloudWatch), boto3 em thread e lista fechada; Stubber e suíte de contrato.
- [x] 3.2 Correções no SigNoz e no Azure DevOps: logs WARN+ com top 5 agrupado, séries de 1 min com o minuto da mudança e deploys com a mensagem do commit.
- [x] 3.3 `verify` no adapter da Anthropic (schema do `VerificationDraft`, etapa `verification`) e modo de validação no LLM fake.
- [x] 3.4 Card "Causa raiz" no Teams, e o card inicial avisando "validando as hipóteses".
- [x] 3.5 Jobs `run_verifications` e `reap_stuck_verifications` no worker; READMEs; `make check` verde.

## 4. grolla-web

- [x] 4.1 Seção "Validação" na página do incidente (vereditos, causa raiz, plano e avaliações) e o card da causa raiz no console; testes; README; `make check` verde.

## 5. grolla-e2e

- [x] 5.1 Cenários:
  - alerta gera investigando, análise inicial e causa raiz, nessa ordem;
  - validação parcial;
  - inicial parcial sem hipóteses não pede validação.

  Ajustar as contagens dos cenários existentes e deixar `make check` verde.

## 6. Integração (agente pai)

- [x] 6.1 Juntar os branches dos agentes e resolver conflitos.
- [x] 6.2 Fazer o code review do conjunto entregue e os ajustes que couberem.
- [x] 6.3 `deploy/`: seções `cloud` e de limites, as variáveis `AWS_*` e a política IAM mínima.
- [x] 6.4 Laboratório:
  - criar o IAM somente leitura;
  - marcar os recursos da pedidos-api com a tag `service=pedidos-api`;
  - repetir os dois cenários.

  A causa raiz precisa sair certa nos dois, com o plano de ação (rollback e religar o RDS).
- [x] 6.5 Registrar no `design.md` os desvios de contrato.
- [x] 6.6 Revisar e atualizar os READMEs do pai e de cada componente.
- [x] 6.7 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 6.8 Rodar `make fmt && make check` e garantir tudo verde.

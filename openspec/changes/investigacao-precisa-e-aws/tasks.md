> Change de agente único: o agente pai implementa (domínio, integrações e configuração), porque o escopo é pequeno.

## 1. Investigação mais precisa

- [ ] 1.1 `search_logs` com WARN e acima, agrupamento por mensagem normalizada e top 5 com nível; fixtures e testes.
- [ ] 1.2 `query_metric` com pontos de 1 min e o minuto da mudança de patamar no resumo; testes.
- [ ] 1.3 `recent_deploys` com a primeira linha da mensagem do commit; testes com fixture do Azure DevOps.
- [ ] 1.4 Prompt: usar a nuvem quando os sinais apontam para uma dependência, e só sugerir rollback quando o problema começa depois do deploy.

## 2. Nuvem

- [ ] 2.1 Port `CloudInventory`, `NoCloudInventory`, as três ferramentas no `Toolbox` e `run_next_analysis` com `cloud`; fake em memória e suíte de contrato.
- [ ] 2.2 `grolla.adapters.aws.AwsCloudInventory` (boto3 em thread, lista fechada de leitura), testado com Stubber e na suíte de contrato.
- [ ] 2.3 `TeamConfig.cloud`, `WorkerContainer.cloud` e `prepare_worker`; `boto3` nas dependências.
- [ ] 2.4 `deploy/`: seção `cloud` no `credenciadora.yaml`, as variáveis `AWS_*` no `.env.example` e a política IAM mínima no README.

## 3. Laboratório

- [ ] 3.1 Usuário IAM somente leitura `grolla-agent-readonly`, tag `service=pedidos-api` nos recursos e credenciais no worker local.
- [ ] 3.2 Repetir o cenário "banco fora" e conferir que a análise diz que o banco caiu e foi parado manualmente, sem sugerir rollback.

## 4. Fechamento

- [ ] 4.1 Revisar e atualizar os READMEs dos componentes e do pai (features, roadmap e badge de status).
- [ ] 4.2 Rodar `make e2e` e garantir todos os cenários verdes.
- [ ] 4.3 Rodar `make fmt && make check` e garantir tudo verde.

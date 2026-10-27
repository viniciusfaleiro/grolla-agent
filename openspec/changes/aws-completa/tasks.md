> Change multiagente: domínio e integrações em paralelo a partir do commit de contrato. Sem web, sem persistência; o e2e com fakes não tem nuvem (`NoCloudInventory`), então o teste ponta a ponta é no laboratório real.

## 0. Contratos (agente pai)

- [x] 0.1 `CloudInventory.health`/`quotas`, `HEALTH_UNAVAILABLE`, `NoCloudInventory`, stubs no AWS e nos fakes; `CloudAccount` e `cloud.accounts` na config e no bootstrap.
- [x] 0.2 `make check` verde e commit de contrato.

## 1. grolla-dominio

- [ ] 1.1 Ferramentas `cloud_health` e `cloud_quotas` no Toolbox e nos prompts; `FakeCloudInventory` com `health`/`quotas`; suíte de contrato; `make check` verde.

## 2. grolla-integracoes

- [ ] 2.1 ECS/EKS no `cloud_resources` e na visão geral.
- [ ] 2.2 `health` (AWS Health, com `SubscriptionRequiredException` virando `HEALTH_UNAVAILABLE`) e `quotas` (Service Quotas e CloudWatch para o uso).
- [ ] 2.3 Várias contas por STS AssumeRole, com o nome da conta em cada item e falha por conta isolada; testes com Stubber; suíte de contrato; README do core; `make check` verde.

## 3. Integração (agente pai)

- [ ] 3.1 Juntar os branches e resolver conflitos.
- [ ] 3.2 Fazer o code review do todo entregue, com os ajustes que couberem.
- [ ] 3.3 Laboratório:
  - Health na conta sem plano;
  - cotas reais;
  - criar uma role somente leitura na conta do laboratório e configurá-la em `cloud.accounts`;
  - um incidente que use as ferramentas.
- [ ] 3.4 `deploy/iam-somente-leitura.json` com as ações novas e a política de confiança da role.
- [ ] 3.5 Registro de decisões, roadmap e `CLAUDE.md`; desvios no `design.md`; READMEs.
- [ ] 3.6 Rodar `make e2e` e garantir todos os cenários verdes.
- [ ] 3.7 Rodar `make fmt && make check` e garantir tudo verde.

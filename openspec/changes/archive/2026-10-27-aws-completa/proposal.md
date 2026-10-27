## Why

A consulta à AWS (S3.1) cobre recursos por tag (RDS, EC2, ELB), CloudTrail e alarmes do CloudWatch, numa conta só. Fica de fora o que mais derruba serviço em nuvem: eventos da própria AWS (AWS Health), cotas estouradas, ECS/EKS e ambientes com mais de uma conta (roadmap S11, "AWS completa").

## What Changes

- **Eventos da AWS** (`cloud_health`): os eventos abertos do AWS Health que afetam as regiões e os recursos do serviço. Sem o plano de suporte que libera a API, a resposta diz isso, sem erro.
- **Cotas** (`cloud_quotas`): uso contra o limite das cotas que costumam derrubar serviço (instâncias e conexões do RDS, vCPUs do EC2, balanceadores), pelo Service Quotas e pelo CloudWatch, destacando as que passam de 80%.
- **ECS e EKS** no `cloud_resources`: serviços do ECS (tarefas desejadas e rodando, último deployment) e clusters e nodegroups do EKS com a tag `service=<nome>`.
- **Várias contas**: a seção `cloud` aceita `accounts` (nome, role somente leitura e regiões). O adapter assume a role de cada conta e junta as respostas, dizendo de que conta veio cada item.
- **Política IAM somente leitura** atualizada no `deploy/`.

## Capabilities

### Modified Capabilities
- `cloud-investigation`: AWS Health, cotas, ECS/EKS e várias contas.
- `team-config`: `cloud.accounts`.

## Non-goals

- **Escrever na AWS.** Continua somente leitura.
- **Organizations ou descoberta automática de contas.** As contas vêm do YAML.
- **Cotas de todos os serviços.** Só as que costumam derrubar serviço, numa lista fechada no adapter.

## Impact

- `grolla-core`:
  - `CloudInventory` ganha `health` e `quotas`;
  - ferramentas neutras `cloud_health` e `cloud_quotas`;
  - adapter AWS com ECS/EKS, Health, Service Quotas, STS para várias contas e a config `cloud.accounts`.
- `deploy/iam-somente-leitura.json` atualizado.
- Laboratório: teste real na conta (Health sem plano, cotas, assumir uma role somente leitura criada para o teste).

## Why

O laboratório real do S3 (pedidos-api na AWS, deploys pelo Azure DevOps, SigNoz local) mostrou dois limites da investigação:

- **No cenário "banco fora", o agente concluiu "pool esgotado ou banco degradado".** A aplicação registrou a causa ("connection to server … failed: server closed the connection unexpectedly", "reconnection attempt failed after 300 s"), mas como WARN. A ferramenta de logs só lê ERROR e devolve as 3 mensagens mais frequentes, e esse ERROR era só o sintoma genérico do pool.
- **O agente ainda deu 30% para "regressão de deploy" e sugeriu rollback.** O último deploy era o próprio revert, e os erros começaram 10 minutos depois. As séries vêm em pontos de 5 minutos, e o resumo do deploy não traz a mensagem do commit.
- **Sem acesso à AWS, ele não confirma a causa.** O RDS parado e o `StopDBInstance` no CloudTrail ficam invisíveis para ele.

O usuário também quer que o agente vá até a **causa raiz**. Hoje ele para nas hipóteses, e o plano ainda é "investigar mais". A investigação passa a ter duas fases:

1. Análise inicial, com hipóteses e um primeiro aviso ao humano.
2. Validação de cada hipótese em detalhe, até confirmar ou rejeitar, e então um plano de ação concreto, como "fazer rollback para o build X" ou "religar o banco Y", para o humano validar e executar.

O usuário pediu para trazer a consulta à AWS para agora. Esta change adianta o S11 com escopo mínimo e corrige os dois limites da investigação. Ver `docs/initial-spec.md`, roteiro de investigação, passo 5 (infraestrutura).

## What Changes

- **Duas fases até a causa raiz**:
  - Ao fim da análise inicial, o worker pede a **validação**.
  - O LLM investiga cada hipótese em detalhe, com as mesmas ferramentas neutras, e dá a cada uma o veredito **confirmada**, **rejeitada** ou **inconclusiva**, com justificativa e evidências.
  - Com uma hipótese confirmada, registra a **causa raiz** e um **plano de ação concreto**: ações diretas (rollback, religar, escalar), nunca "investigar".
  - Sem confirmação, diz que a causa não foi confirmada e o que faltou.
  - Limites próprios de tempo e tokens, a mesma garantia de que a validação sempre termina e o card **"Causa raiz"**.
  - A página do incidente mostra as duas fases.

- **Logs com a causa**:
  - `search_logs` passa a incluir WARN e acima;
  - devolve as 5 mensagens mais frequentes, cada uma com o nível;
  - agrupa variações da mesma mensagem (IPs, números, endereços).
- **Deploys que explicam a mudança**: cada deploy no resumo traz a primeira linha da mensagem do commit. O agente vê, por exemplo, que um build é um "Revert …".
- **Séries com resolução para correlacionar**:
  - `query_metric` usa pontos de 1 minuto;
  - o resumo diz o minuto em que a série mudou de patamar.

  Com isso, o agente compara o início do problema com o horário de cada deploy.
- **Investigação na nuvem**, com o port `CloudInventory` e um adapter AWS (boto3, credenciais somente leitura). Três ferramentas neutras:
  - `cloud_resources(service)`: os recursos do serviço e o estado de cada um. Cobre instâncias de banco gerenciado, máquinas e balanceadores, achados pela tag `service=<nome>` e por um mapa opcional no YAML.
  - `cloud_changes(service)`: eventos recentes de auditoria nesses recursos (o quê, quem, quando), por exemplo parar, reiniciar ou modificar uma instância.
  - `cloud_alarms(service)`: os alarmes de monitoramento da nuvem em estado de alarme ligados a esses recursos.
- **Prompt** atualizado para usar as ferramentas de nuvem quando os sinais apontam para uma dependência de infraestrutura.
- **Configuração**: o YAML ganha a seção `cloud` (regiões e mapa opcional serviço → recursos). As credenciais vêm só do ambiente, pela cadeia padrão do SDK.
- **Laboratório**: os recursos da pedidos-api recebem a tag `service=pedidos-api`, e o worker local passa a usar credenciais de um usuário IAM somente leitura.

## Non-goals

- AWS Health (exige plano de suporte pago), ECS/EKS, quotas e métricas detalhadas do CloudWatch: entram quando houver caso real.
- Multi-conta e assumir role por conta: no piloto, uma conta só.
- Leitura de código (`read_file`): fica no S5.
- Qualquer escrita na AWS: o agente continua somente leitura.

## Capabilities

### New Capabilities

- `root-cause-verification`: a fase de validação, com os vereditos, a causa raiz, o plano de ação concreto, os limites e a garantia de término.
- `cloud-investigation`: ferramentas neutras de nuvem (recursos, mudanças e alarmes), descoberta dos recursos do serviço e a fronteira somente leitura.

### Modified Capabilities

- `investigation-tools`: logs com WARN e mensagens agrupadas, deploys com a mensagem do commit e séries com o minuto da mudança. Entram como requisitos novos, porque a capability nasce no S3, ainda não arquivado.
- `team-config`: seção `cloud` e credenciais da nuvem só no ambiente; limites da validação.
- `incident-notifications`: card da causa raiz; o card inicial avisa que a validação começou.
- `incident-pages`: a página mostra a validação.

## Impact

- **Código**:
  - port `CloudInventory` e três ferramentas no `Toolbox`, com o prompt atualizado;
  - adapter `grolla.adapters.aws`, novo;
  - ajustes em `grolla.adapters.signoz.telemetry` e em `grolla.adapters.azure_devops`;
  - fake da AWS no `grolla-fakes` e cenário e2e.
- **Banco**: migração 0004 com as colunas da validação em `analyses`.
- **Dependências novas**: `boto3`.
- **Externo**: credenciais AWS somente leitura no worker, com as permissões `tag:GetResources`, `rds:Describe*`, `ec2:Describe*`, `elasticloadbalancing:Describe*`, `cloudtrail:LookupEvents` e `cloudwatch:DescribeAlarms`.
- **Custo**: a validação dobra, no máximo, o uso de LLM por incidente, com teto próprio de tokens (no laboratório, cada análise custou cerca de US$ 0,03–0,05). As ferramentas de nuvem somam até 3 chamadas por fase.

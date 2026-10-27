## Context

`AwsCloudInventory` já descobre recursos por tag e estado (RDS, EC2, ELB), mudanças no CloudTrail,
alarmes do CloudWatch e a visão geral, numa conta e nas regiões do YAML, com boto3 somente
leitura.

## Contratos

```python
# CloudInventory ganha:
async def health(self, service: str) -> ToolResult: ...
async def quotas(self, service: str) -> ToolResult: ...


# NoCloudInventory responde NO_CLOUD nos dois. Ferramentas neutras `cloud_health` e
# `cloud_quotas` no Toolbox (uma só por `service`), citadas no SYSTEM_PROMPT/VERIFICATION_PROMPT
# como passo de infraestrutura.
HEALTH_UNAVAILABLE = "eventos da nuvem indisponíveis no plano de suporte desta conta"


# adapter: AwsCloudInventory(regions, resource_map, accounts: Sequence[CloudAccount] = ())
@dataclass(frozen=True)
class CloudAccount:
    name: str
    role_arn: str
    regions: tuple[str, ...]
```

**Config:** `cloud.accounts: [{name, role_arn, regions}]`.

### Ajustes combinados durante a implementação

- Suíte separada `CloudHealthQuotasContract`, no padrão da `CloudOverviewContract`.
- A conta só aparece em cada item quando há mais de uma; a principal se chama "principal" (sem
  `sts:GetCallerIdentity`).
- Uso de vCPU só pelo CloudWatch (`AWS/Usage`); sem ponto, "uso sem dado".
- Cotas do banco: instâncias e armazenamento (conexões não são cota da conta).
- Health filtra pelas regiões configuradas; eventos globais ficam de fora.
- Laboratório (27/10): Health sem plano → `HEALTH_UNAVAILABLE`; cotas reais (vCPUs 2 de 64,
  instâncias de banco 1 de 40); recursos e visão geral; conta com role inexistente → "conta outra:
  AccessDenied" sem esconder a principal. A role real não foi criada: criar identidade IAM fica
  com o dono da conta (a conta é usada como root, e root não assume role).

## Decisions

**1. Lista fechada de cotas.** Poucas cotas derrubam serviço, e listar todas é ruído e custo de
API. A lista fica no adapter: RDS (instâncias, total de armazenamento), EC2 (vCPUs sob demanda
padrão) e ELB (balanceadores de aplicação por região).

**2. Health tolerante ao plano.** A API exige Business ou Enterprise. Sem o plano, a resposta diz
isso, e a investigação segue.

**3. Várias contas por AssumeRole, em sessão por conta e com cache até expirar.** Uma role
somente leitura por conta, criada pelo time; nenhuma credencial no YAML.

## Divisão de trabalho

| Papel | Entrega |
|---|---|
| `grolla-dominio` | ports `health`/`quotas`, ferramentas `cloud_health`/`cloud_quotas` no Toolbox e nos prompts, fakes (`FakeCloudInventory`) e suíte de contrato |
| `grolla-integracoes` | AWS: ECS/EKS, Health, Service Quotas e várias contas (STS), com Stubber; a política IAM em `deploy/` (pedida ao pai) |

O agente pai faz a config, o bootstrap, a política no `deploy/` e o teste no laboratório: Health
sem plano, cotas reais e uma role somente leitura assumida na própria conta.

## Risks / Trade-offs

- [Mais chamadas de API por investigação] → só quando o modelo pede a ferramenta; as respostas
  são resumidas.
- [Role mal configurada] → o erro aparece na resposta, com o nome da conta, e não esconde as
  outras.

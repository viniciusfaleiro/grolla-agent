## Context

O S3 investiga só pelo SigNoz (telemetria) e pelo Azure DevOps (deploys). O laboratório real mostrou três problemas:

- O filtro de logs (`severity_number >= 17`, top 3) escondeu a causa, que estava em WARN.
- Os pontos de 5 minutos e a falta da mensagem do commit impediram separar o deploy de revert da queda do banco.
- Sem nuvem, o agente não confirma uma parada de infraestrutura.

O usuário pediu a AWS agora. A conta do laboratório é a 851725637288 (us-east-1). Change de agente único: o agente pai implementa, porque o escopo é pequeno e as áreas se tocam pouco.

## Goals / Non-Goals

**Goals:**

- Repetido o cenário "banco fora", a análise diz que o banco caiu, mostra o recurso parado e quem o parou, e não sugere rollback de deploy.
- No cenário "bug no deploy", a análise continua apontando o deploy e o rollback.
- O fornecedor de nuvem fica isolado atrás de um port, com ferramentas neutras.

**Non-Goals:** ver a proposta.

## Contratos

```python
# grolla.application.investigation (acréscimos)
NO_CLOUD = "consulta à nuvem não configurada nesta instância"


class CloudInventory(Protocol):  # adapter: grolla.adapters.aws
    async def resources(self, service: str) -> ToolResult: ...
    async def changes(self, service: str, since: datetime) -> ToolResult: ...
    async def alarms(self, service: str) -> ToolResult: ...


class NoCloudInventory:  # instância sem a seção `cloud`: responde NO_CLOUD, sem erro
    ...


class Toolbox:
    def __init__(
        self,
        telemetry: Telemetry,
        changes: ChangeHistory,
        clock: Clock,
        lookback: timedelta,
        cloud: CloudInventory,
    ) -> None: ...


# Ferramentas novas (argumento único `service`; a janela vem do lookback):
#   cloud_resources {service} · cloud_changes {service} · cloud_alarms {service}

# grolla.application.analysis: run_next_analysis ganha `cloud: CloudInventory`.
# grolla.bootstrap: TeamConfig.cloud: CloudConfig | None (regions, resource_map) e
#   WorkerContainer.cloud.


# grolla.adapters.aws
class AwsCloudInventory:
    def __init__(
        self,
        regions: Sequence[str],
        resource_map: Mapping[str, Sequence[str]],  # serviço -> ARNs ou identificadores
        session: boto3.Session | None = None,
    ) -> None: ...
```

## Decisions

1. **Recursos pela tag `service=<nome>`**, pela Resource Groups Tagging API, mais o mapa opcional. É o mesmo princípio do repositório pelo nome do serviço. *Mais simples considerada:* só o mapa no YAML. Descartada porque exige manter uma lista grande; a tag já é prática comum de FinOps.
2. **Estado por tipo de recurso**, com uma chamada de describe por tipo encontrado: `rds:DescribeDBInstances`, `ec2:DescribeInstances` e `elbv2:DescribeLoadBalancers`. Outros tipos aparecem só com o ARN. *Mais simples considerada e escolhida:* só os três tipos mais comuns nos incidentes da Credenciadora.
3. **`cloud_changes` pelo CloudTrail `LookupEvents`**, filtrando por `ResourceName` de cada recurso e por `ReadOnly=false`. As 20 mudanças mais recentes da janela, numa linha cada.
4. **`cloud_alarms` pelo `DescribeAlarms(StateValue=ALARM)`**, filtrado pelas dimensões que citam os recursos do serviço.
5. **boto3 síncrono em `asyncio.to_thread`.** *Mais simples considerada e escolhida:* o aioboto3 seria mais uma dependência para três chamadas.
6. **Testes com `botocore.stub.Stubber`**, sem rede. A suíte de contrato do port roda contra o fake em memória e contra o adapter com Stubber.
7. **Logs WARN+ e agrupamento**:
   - O filtro passa a `severity_number >= 13`.
   - O agrupamento normaliza números longos, IPs, endereços `0x…` e UUIDs antes de contar.
   - O resumo mostra "N× [NÍVEL] mensagem", com as 5 mais frequentes.
8. **Séries de 1 minuto com ponto de mudança**:
   - O `stepInterval` passa a 60 s.
   - O resumo traz o início, o fim e o máximo, mais a primeira mudança de patamar: o primeiro ponto que se afasta mais de 3× (ou mais de 5 pontos percentuais, na taxa de erro) da média dos 5 primeiros pontos. Ele aparece como "mudou às HH:MM, de X para Y".
   - A série completa não vai para o LLM, só os últimos 10 pontos, para economizar tokens.
9. **Mensagem do commit** vem de `triggerInfo["ci.message"]`, ou de `sourceVersionMessage` quando presente no build. Sem nenhum dos dois, o resumo segue sem a mensagem.
10. **Prompt** ganha uma linha: quando logs e traces apontam para uma dependência (banco, fila, rede), consultar `cloud_resources` e `cloud_changes`. E uma regra: só sugerir rollback quando o início do problema vem depois de um deploy, e explicar a relação.
11. **Sem fake da AWS no `grolla-fakes`.** O e2e roda sem a seção `cloud`, e as ferramentas respondem `NO_CLOUD`. *Mais simples considerada e escolhida:* o adapter é coberto pelo Stubber, e o caminho real é validado no laboratório.

## Risks / Trade-offs

- [Credencial com permissão demais] → um usuário IAM dedicado com a política mínima da proposta. No laboratório, uso a conta root só para criar esse usuário.
- [CloudTrail tem atraso de alguns minutos] → o resumo avisa que "eventos de auditoria podem levar até 5 min para aparecer". A análise pode rodar antes do evento chegar.
- [Tag ausente nos recursos reais] → o mapa opcional cobre os casos sem tag. A ferramenta responde "nenhum recurso encontrado" sem erro.
- [Mais tokens por análise] → os resumos são compactos, e o teto por análise continua o mesmo.

## Migration Plan

1. Merge depois do S3, com `make check` e `make e2e` verdes.
2. **Na Credenciadora**: criar o usuário IAM somente leitura (ou role, em produção na EC2), pôr as credenciais no `.env` do worker, configurar `cloud.regions` e marcar os recursos com `service=<nome>`.
3. **No laboratório**: repetir os dois cenários e comparar as análises.

## Open Questions

- Nenhuma para o laboratório. Em produção, falta definir se a credencial será role da EC2 ou chave de usuário IAM.

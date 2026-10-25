## Context

O S3 investiga só pelo SigNoz (telemetria) e pelo Azure DevOps (deploys). O laboratório real mostrou três problemas:

- O filtro de logs (`severity_number >= 17`, top 3) escondeu a causa, que estava em WARN.
- Os pontos de 5 minutos e a falta da mensagem do commit impediram separar o deploy de revert da queda do banco.
- Sem nuvem, o agente não confirma uma parada de infraestrutura.

O usuário pediu a AWS agora e quer que o agente chegue à causa raiz: análise inicial com hipóteses e primeiro aviso, depois a validação de cada hipótese em detalhe e por fim um plano de ação concreto. A conta do laboratório é a 851725637288 (us-east-1). Change multiagente: a validação toca domínio, persistência, integrações (card), web (página) e e2e.

## Goals / Non-Goals

**Goals:**

- Toda análise inicial com hipóteses vira uma validação, que termina com causa raiz confirmada e plano de ação concreto, ou com "causa não confirmada" e o que faltou.

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

### Validação — domínio, application e schema

```python
# grolla.domain.analysis (acréscimos)
class Verdict(StrEnum):
    CONFIRMED = "confirmada"
    REJECTED = "rejeitada"
    INCONCLUSIVE = "inconclusiva"


@dataclass(frozen=True)
class HypothesisVerdict:
    hypothesis: str  # título da hipótese da análise inicial
    verdict: Verdict
    reasoning: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Action:
    action: str  # imperativo e concreto: "Fazer rollback do pedidos-api para o build 20261025.1"
    target: str  # componente ou recurso: "pedidos-api", "RDS grolla-lab-db"
    expected: str  # efeito esperado
    link: str | None  # pipeline, console etc., só se veio de uma avaliação


@dataclass(frozen=True)
class VerificationDraft:  # o que o modelo devolve, validado
    verdicts: tuple[HypothesisVerdict, ...]
    root_cause: str | None  # None: causa não confirmada
    actions: tuple[Action, ...]  # vazio quando root_cause é None
    missing: str  # o que faltou para confirmar ("" quando confirmada)


@dataclass(frozen=True)
class Verification:
    incident_id: UUID
    status: AnalysisStatus  # mesmos estados da análise
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    verdicts: tuple[HypothesisVerdict, ...]
    root_cause: str | None
    actions: tuple[Action, ...]
    missing: str
    evaluations: tuple[Evaluation, ...]
    partial_reason: PartialReason | None


def verified(pending, draft, evaluations, now) -> Verification: ...
def verification_partial(pending, reason, evaluations, now) -> Verification: ...


# grolla.domain.incidents: EventType ganha VERIFICATION_REQUESTED/COMPLETED/PARTIAL e
#   NotificationKind ganha ROOT_CAUSE; Notification ganha `verification: Verification | None`.


# grolla.application.investigation
class LanguageModel(Protocol):
    async def investigate(self, brief, toolbox, budget) -> AnalysisDraft: ...
    async def verify(
        self, brief: InvestigationBrief, analysis: Analysis, toolbox: Toolbox, budget: TokenBudget
    ) -> VerificationDraft: ...


VERIFICATION_PROMPT: str  # neutro; regras de veredito e de plano concreto
STAGE_VERIFICATION = "verification"

# grolla.application.analysis
#   AnalysisQueue.finish(...) passa a pedir a validação na mesma transação quando a análise
#   tem hipóteses; ganha claim_verification, stuck_verifications, finish_verification.
#   run_next_verification(...) e reap_stuck_verifications(...), como os da análise inicial.
#   AnalysisPolicy ganha verification_timeout e verification_max_tokens.
#   IncidentRepository.verification_of(incident_id) -> Verification | None
```

- **Schema, migração `0004`:** tabela `verifications`, com a mesma forma da `analyses`:
  - `incident_id pk`;
  - estado e horários: `status`, `requested_at`, `started_at`, `finished_at`;
  - resultado: `verdicts jsonb`, `root_cause`, `actions jsonb`, `missing`, `evaluations jsonb`, `partial_reason`.
- **Worker:** jobs `run_verifications` e `reap_stuck_verifications` na fila `analysis`.

## Decisions

0. **A validação é uma segunda passada do LLM com estado próprio, e não um laço único mais longo.**
   - *Mais simples considerada:* pedir as duas coisas na mesma análise. Descartada porque o humano precisa da primeira resposta rápido, em segundos ou poucos minutos. A validação aprofunda depois, com outro orçamento, e um timeout na validação não pode apagar a análise inicial.
   - Tabela própria em vez de colunas em `analyses`, para a fila de cada fase ficar simples e independente.
0.1 **Plano concreto imposto pelo schema e pela validação.**
   - Cada ação tem `action`, `target` e `expected`.
   - Uma ação cujo verbo é de investigação (investigar, verificar, checar, analisar, consultar, confirmar, avaliar) é recusada como resultado inválido, o que força uma segunda tentativa dentro do orçamento. Sem causa confirmada, `actions` precisa vir vazio.
   - *Mais simples considerada:* só instruir no prompt. Descartada porque o laboratório mostrou o modelo devolvendo "verificar a saúde do banco" como plano.
   - Como no S3, o link de uma ação só fica se veio de uma avaliação real.

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

## Desvios de contrato combinados na implementação

Registrados pelo agente pai na integração. O código é a referência.

1. **Constantes novas.** `NO_CLOUD_RESOURCES` ("nenhum recurso de nuvem encontrado para o serviço") em `grolla.application.investigation` e `NO_ROOT_CAUSE` ("causa raiz não confirmada") em `grolla.application.analysis`.
2. **Falha da nuvem.** O `CloudInventory` levanta `ToolUnavailableError` em falha de credencial, permissão ou rede, como os outros ports. O `Toolbox` transforma isso em "não foi possível consultar".
3. **Suíte da fila da validação.** É a classe própria `VerificationQueueContract`, separada da `AnalysisQueueContract`.
4. **Inconclusivo termina como `concluida`.** A validação com todas as hipóteses inconclusivas fica `concluida`, com `root_cause=None`, sem ações e com `missing` preenchido. O status `parcial` fica só para falha, tempo esgotado, orçamento esgotado, resultado inválido (inclusive `NotConcreteError`) ou interrupção. Nesse caso, `missing` = "Validação parcial: <motivo>.".
5. **`check_plan` também pega o imperativo.** O code review do agente pai mudou a regra para recusar ainda as formas imperativas dos verbos de investigação ("Verifique", "Cheque", "Avalie"), comparando pelo radical.
6. **Pedido da validação no `finish`.** O `finish` da análise cria a validação pendente só quando a análise tem hipóteses e só se de fato gravou. O pedido repetido mantém o primeiro.
7. **LLM fake.** O controle é `POST /_control/llm {"mode"?, "verification_mode"?}`. O fake reconhece a fase de validação pelo schema com `verdicts`.
8. **Uma correção no adapter do LLM.** Quando o plano recebe `NotConcreteError`, o adapter da Anthropic manda uma mensagem de correção e tenta de novo. Se falhar outra vez, levanta `InvalidAnalysisError`.
9. **Logs no formato `31x [ERROR] …`.** O separador é o "x" ASCII, porque o ruff (RUF001) recusa "×" no código.
10. **`LanguageModelContract` ganhou os testes de `verify`,** com as fixtures `verifying_model`, `failing_verifier` e `invalid_verifier`.
11. **`resource_map` aceita ARNs.** Um identificador solto fica na primeira região: `i-…` vira EC2, e os demais aparecem sem estado.
12. **CloudTrail consultado pelo nome curto do recurso.** O `lookup_events` usa `ResourceName` com o identificador (`grolla-lab-db`), não o ARN. Foi conferido no laboratório real.
13. **Mensagem do commit no Azure DevOps.** Vem de `triggerInfo["ci.message"]`, que só existe em build de CI. Em build manual, o adapter busca o `comment` na API de commits.

## Risks / Trade-offs

- [Credencial com permissão demais] → um usuário IAM dedicado com a política mínima da proposta. No laboratório, uso a conta root só para criar esse usuário.
- [CloudTrail tem atraso de alguns minutos] → o resumo avisa que "eventos de auditoria podem levar até 5 min para aparecer". A análise pode rodar antes do evento chegar.
- [Tag ausente nos recursos reais] → o mapa opcional cobre os casos sem tag. A ferramenta responde "nenhum recurso encontrado" sem erro.
- [Mais tokens por análise] → os resumos são compactos, e o teto por análise continua o mesmo.

## Divisão de trabalho

| Papel | Entrega |
|---|---|
| agente pai | Commit de contrato, `bootstrap` (cloud e limites da validação), `boto3`, `deploy/` e laboratório (IAM e tags) |
| `grolla-dominio` | Vereditos, `verified`/`verification_partial`, validação do plano concreto, os dois casos de uso, o prompt da validação, as ferramentas de nuvem no `Toolbox`, `NoCloudInventory`, fakes e suítes de contrato |
| `grolla-persistencia` | Migração 0004, fila da validação, pedido da validação no `finish` da análise e `verification_of` |
| `grolla-integracoes` | `adapters/aws`, as correções de logs, séries e deploys, `verify` no adapter da Anthropic, card da causa raiz, jobs do worker e modo de validação no LLM fake |
| `grolla-web` | Seção da validação na página e no console |
| `grolla-e2e` | Cenários: validação confirma e gera o card da causa raiz; validação parcial; inicial sem hipóteses não pede validação |

## Migration Plan

1. Merge depois do S3, com `make check` e `make e2e` verdes.
2. **Na Credenciadora**: criar o usuário IAM somente leitura (ou role, em produção na EC2), pôr as credenciais no `.env` do worker, configurar `cloud.regions` e marcar os recursos com `service=<nome>`.
3. **No laboratório**: repetir os dois cenários e comparar as análises.

## Open Questions

- Nenhuma para o laboratório. Em produção, falta definir se a credencial será role da EC2 ou chave de usuário IAM.

# Roadmap de slices — agent-grolla

Sequência de slices para construir o agente sobre o harness. Cada slice vira uma change do
openspec (`/opsx:propose → apply → archive`) e **entrega valor para o time de plantão** ao
fechar. A spec de produto é [`docs/initial-spec.md`](../docs/initial-spec.md).

**Decisões de fatiamento (combinadas):**

- **Valor só existe em produção.** O primeiro slice de funcionalidade (S2) já faz o deploy na
  EC2 com integrações reais. Todo slice seguinte fecha com aceite no ambiente real, não só com
  e2e verde.
- **Funcionalidade completa por slice.** Cada slice fecha uma capacidade da spec de ponta a
  ponta (entrada, núcleo, persistência, saída e tela), em vez de uma camada por vez.
- **Port novo só quando o slice precisa dele**, com suíte de contrato, fake em memória, adapter
  real e o protocolo do fornecedor no `grolla-fakes`.
- **O e2e de cada slice reproduz o caminho de valor** com os fakes (ex.: alerta repetido gera um
  card só).
- **Acionamento com modo sombra.** O S3 nasce com `pager.mode: shadow`: o agente só posta no
  Teams "eu teria acionado o on-call". O modo real é ligado por config depois de 1–2 semanas de
  calibração das regras de severidade.
- S7–S10 são independentes entre si e podem ser reordenados pela dor do plantão.

## S1 · Fundação ✅

Workspace, harness completo (`make check`, `make e2e`), esqueleto do `grolla-api` e do
`grolla-worker`, `grolla-fakes` e cenário de fumaça. Change arquivada:
`changes/archive/2026-10-25-fundacao-harness`.

## S2 · Alertas viram incidentes

- **Valor:** um card no Teams por problema, não por alerta — deduplicado, agrupado e com
  severidade — e uma página por incidente com a timeline. Para P2/P3 a funcionalidade da spec
  termina aqui.
- **Escopo:** webhook do SigNoz com token (+ poller de 60 s como rede de segurança);
  deduplicação por fingerprint; agrupamento determinístico (mesmo `service.name` ou dependência
  direta, janela de 15 min); regras de severidade no YAML do time; log de eventos imutável e
  projeções; outbox para o Teams; página do incidente só leitura (estado, severidade, timeline),
  atrás da rede interna; YAML de configuração por time.
- **Ports:** `AlertIngress`, `AlertSource`, `ChatNotifier`, `EventLog` / `IncidentRepository`,
  `Scheduler`.
- **Infra:** primeiro deploy na EC2 (compose, segredos via Secrets Manager/SSM, backup diário do
  banco no S3); heartbeat do worker monitorado pelo SigNoz.
- **Dependências externas:** URL do Workflow do Teams; webhook configurado no SigNoz; alertas com
  labels de time e serviço.
- **Aceite:** alertas reais do time piloto viram incidentes no Teams, sem duplicados.

## S3 · On-call acionado sozinho

- **Valor:** P0/P1 acionam o Spike.sh em minutos, com o link da página do incidente; o time
  assume, reclassifica e encerra pelo war room.
- **Escopo:** acionamento com chave de deduplicação e limite por 10 min; estados Detectado →
  Acionado → Em tratamento; ack do Spike move o estado; ações rápidas no war room (assumir IC,
  mudar severidade, separar/juntar, encerrar — encerrar resolve no Spike); autor identificado por
  nome (fase 1); timestamps de TTD/TTA/TTM/TTR; **modo sombra**.
- **Ports:** `Pager`.
- **Aceite:** em modo sombra, o canal mostra os acionamentos que teriam acontecido; após a
  calibração, um P0/P1 real aciona o on-call.

## S4 · Investigação pronta quando o on-call chega

- **Valor:** o on-call recebe hipóteses ranqueadas com evidência e links para o SigNoz e o Azure
  DevOps, em vez de começar do zero.
- **Escopo:** roteiro de investigação inicial (impacto, mudanças recentes, traces, logs, código);
  ferramentas neutras para o LLM (`query_metric`, `search_logs`, `find_traces`,
  `recent_deploys`, `read_file`); saída JSON validada; card "hipóteses prontas" e seção de
  hipóteses no war room; mapa serviço → repositório; limite de tokens por incidente e
  `llm_usage`; núcleo segue funcionando se o LLM cair.
- **Ports:** `LanguageModel`, `Telemetry`, `ChangeHistory`.
- **Evals:** `evals/` começa aqui, com replay dos incidentes reais gravados desde o S2.

## S5 · Agente acompanha a crise

- **Valor:** o time foca em resolver; o canal fica informado e o registro em dia.
- **Escopo:** ciclos de 5 min, update só quando muda (e curto a cada 15 min sem mudança);
  impacto financeiro estimado; chat com o agente no war room; gatilho de ciclo por mensagem ou
  alerta agrupado; proposta de Mitigado e de encerramento após 15 min estáveis, com confirmação
  humana; Monitorando volta para Em tratamento se piorar.

## S6 · Post mortem em minutos

- **Valor:** post mortem blameless no mesmo dia, com a timeline real.
- **Escopo:** rascunho gerado do log de eventos com o template da spec; edição por seção com
  versões e autor; "regenerar seção"; ciclo Rascunho → Em revisão → Aprovado → Encerrado; PDF;
  lista com filtros e action items vencidos.
- **Ports:** `DocumentRenderer`.

## S7 · Menos ruído

- **Valor:** menos incidentes duplicados e menos alertas sem severidade.
- **Escopo:** agrupamento pelo LLM quando a regra determinística não decide; sugestão de
  severidade quando nenhuma regra casa (na dúvida, o nível mais alto); motivo registrado na
  timeline.

## S8 · Console de operação

- **Valor:** respostas sobre a saúde do ambiente sem incidente aberto.
- **Escopo:** chat geral com sessões salvas; respostas com evidência e gráficos; "abrir incidente
  a partir desta conversa"; prioridade menor que incidentes e limite de tokens próprio.

## S9 · Transcrição de call

- **Valor:** o que foi decidido na call entra no registro sem esforço manual.
- **Escopo:** upload da transcrição; extração de decisões, ações, horários e participantes como
  eventos "origem: transcrição"; reavaliação das hipóteses.

## S10 · Investigação na AWS

- **Valor:** hipóteses que também olham a infraestrutura AWS.
- **Escopo:** role somente leitura por conta; AWS Health, alarmes do CloudWatch, RDS, ELB,
  EKS/ECS, quotas e CloudTrail como ferramentas neutras da investigação.
- **Ports:** `CloudInventory`.

## Fase 2 (depois do S10)

SSO no war room e no post mortem (pré-requisito para um segundo time); thresholds calibrados com
dados; action items como work items no Azure DevOps; painel de custo e orçamento de LLM; vigias
criados pelo chat.

## Em aberto

- Time piloto da primeira instância (define as regras de severidade, o canal do Teams e o mapa
  serviço → repositório).

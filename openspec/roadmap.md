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
- **O agente precisa valer mais que o alerta.** O SigNoz já manda o próprio webhook para o
  Teams do time, então um card que só repete o alerta tem pouco valor. Por isso a investigação
  vem logo depois do S2 (S3), antes do acionamento: todo alerta chega ao canal com "estou
  investigando" e, minutos depois, a análise (o que avaliei, hipóteses, plano proposto e o
  botão para a página do incidente). **O S2 não vai sozinho para o canal do time**: o aceite em
  produção do S2 e do S3 é feito junto (ou o S2 roda num canal de teste).
- **Acionamento com modo sombra.** O S4 nasce com `pager.mode: shadow`: o agente só posta no
  Teams "eu teria acionado o on-call". O modo real é ligado por config depois de 1–2 semanas de
  calibração das regras de severidade.
- **A memória começa no S2.** O aprendizado depende de histórico: desde o primeiro incidente, o
  log de eventos guarda sintomas, decisões e desfechos com detalhe suficiente para comparar casos.
- S8–S11 são independentes entre si e podem ser reordenados pela dor do plantão.

## S1 · Fundação ✅

Workspace, harness completo (`make check`, `make e2e`), esqueleto do `grolla-api` e do
`grolla-worker`, `grolla-fakes` e cenário de fumaça. Change arquivada:
`changes/archive/2026-10-25-fundacao-harness`.

## S2 · Alertas viram incidentes ✅

Entregue na change `alertas-viram-incidentes`, com o console de testes no `grolla-fakes`. O aceite
com alertas reais é feito junto com o S3 (sozinho, o S2 repete o que o SigNoz já manda). Ficaram
para depois: poller de 60 s, agrupamento por dependência (precisa do `Telemetry`, S3), Secrets
Manager e backup automatizado (o deploy é manual; o `deploy/README.md` traz o backup por cron).

- **Valor:** um card no Teams por problema, não por alerta — deduplicado, agrupado e com
  severidade — e uma página por incidente com a timeline.
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

## S3 · O agente investiga cada alerta

- **Valor:** o canal recebe mais do que o alerta do SigNoz. Em segundos, "estou investigando";
  em até 5 minutos, a análise: **o que o agente avaliou**, **hipóteses** ranqueadas com
  evidência e links, **plano proposto** para o time e o botão **Abrir incidente**. Vale para
  toda severidade; P0/P1 seguem investigando mais fundo nos slices seguintes.
- **Escopo:**
  - Card **"investigando"** no lugar do card "incidente novo" do S2, postado antes de qualquer
    chamada ao LLM.
  - **Análise inicial** assíncrona no worker (job por incidente), com limite de tempo (5 min) e de
    tokens por config, roteiro curto: impacto (métricas do serviço), mudanças recentes (deploys),
    traces com erro, padrões de log novos.
  - Ferramentas neutras para o LLM: `query_metric`, `search_logs`, `find_traces`,
    `recent_deploys`.
  - Saída JSON validada (avaliações feitas, hipóteses com confiança e evidência, plano proposto)
    gravada como evento.
  - Card **"análise"** com o resultado e seção de análise na página do incidente.
  - **Análise parcial garantida**: se o LLM falhar ou estourar tempo ou orçamento, o card sai
    mesmo assim, dizendo o que avaliou e o que faltou.
  - Alertas agrupados num incidente já analisado não abrem análise nova.
  - Modelos por etapa no YAML e `llm_usage`.
  - O console de testes mostra os dois cards em sequência; o `grolla-fakes` ganha os protocolos
    do LLM, do SigNoz e do Azure DevOps.
- **Ports:** `LanguageModel`, `Telemetry`, `ChangeHistory`.
- **Evals:** `evals/` começa aqui. Cenários roteirizados com telemetria fake, que viram replay
  dos incidentes reais gravados desde o S2.
- **Aceite:** num alerta real da Credenciadora, o canal recebe "investigando" e, em até 5
  minutos, a análise, que o time considera útil (o que avaliou, ao menos uma hipótese com
  evidência e um plano).

## S4 · On-call acionado sozinho

- **Valor:** P0/P1 acionam o Spike.sh em minutos, com o link da página do incidente e a análise
  inicial já anexada; o time assume, reclassifica e encerra pelo war room.
- **Escopo:** acionamento com chave de deduplicação e limite por 10 min; estados Detectado →
  Acionado → Em tratamento; ack do Spike move o estado; ações rápidas no war room (assumir IC,
  mudar severidade, separar/juntar, encerrar — encerrar resolve no Spike); autor identificado por
  nome (fase 1); timestamps de TTD/TTA/TTM/TTR; **modo sombra**.
- **Ports:** `Pager`.
- **Aceite:** em modo sombra, o canal mostra os acionamentos que teriam acontecido; após a
  calibração, um P0/P1 real aciona o on-call.

## S5 · Agente acompanha a crise

- **Valor:** o time foca em resolver; o canal fica informado e o registro em dia.
- **Investigação profunda (P0/P1):** depois da análise inicial, ciclos até 10 min ou confiança
  mínima; leitura de código pelo mapa serviço → repositório (`read_file`); hipóteses no war room.
- **Incidentes parecidos (v1):** antes de investigar, busca determinística e textual nos
  incidentes encerrados (mesmos serviços, regra de alerta, assinatura de erro); o caso parecido
  vira a primeira hipótese e aparece na análise como "já vi isso no incidente #N".
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

## S7 · O agente aprende

- **Valor:** a cada incidente o agente conhece melhor o ambiente e investiga melhor; o time
  ouve "já vimos isso" com a causa e a correção do caso passado.
- **Escopo:** extração de fatos sobre o ambiente ao encerrar incidentes, ao aprovar post mortems
  e das correções do time no chat; página de conhecimento (propor → confirmar → aposentar, com
  fonte e data); fatos confirmados entram no contexto da investigação; incidentes parecidos
  passam a usar também os post mortems aprovados e a confirmação do time ("era o mesmo
  problema"); revisão de fatos antigos ou contrariados; métricas de acerto do agente por período.
- **Evals:** investigação com e sem conhecimento acumulado nos mesmos incidentes reais.

## S8 · Menos ruído

- **Valor:** menos incidentes duplicados e menos alertas sem severidade.
- **Escopo:** agrupamento pelo LLM quando a regra determinística não decide; sugestão de
  severidade quando nenhuma regra casa (na dúvida, o nível mais alto); motivo registrado na
  timeline.

## S9 · Console de operação

- **Valor:** respostas sobre a saúde do ambiente sem incidente aberto.
- **Escopo:** chat geral com sessões salvas; respostas com evidência e gráficos; "abrir incidente
  a partir desta conversa"; prioridade menor que incidentes e limite de tokens próprio.

## S10 · Transcrição de call

- **Valor:** o que foi decidido na call entra no registro sem esforço manual.
- **Escopo:** upload da transcrição; extração de decisões, ações, horários e participantes como
  eventos "origem: transcrição"; reavaliação das hipóteses.

## S11 · Investigação na AWS

- **Valor:** hipóteses que também olham a infraestrutura AWS.
- **Escopo:** role somente leitura por conta; AWS Health, alarmes do CloudWatch, RDS, ELB,
  EKS/ECS, quotas e CloudTrail como ferramentas neutras da investigação.
- **Ports:** `CloudInventory`.

## Fase 2 (depois do S11)

SSO no war room e no post mortem (pré-requisito para um segundo time); thresholds calibrados com
dados; action items como work items no Azure DevOps; painel de custo e orçamento de LLM; vigias
criados pelo chat.

## Em aberto

- Labels reais dos alertas da Credenciadora (filtro e serviço são configuráveis no YAML).

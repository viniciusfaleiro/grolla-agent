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

Entregue nas changes `changes/archive/2026-10-25-alertas-viram-incidentes` e `changes/archive/2026-10-25-console-de-testes`. O aceite
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

## S3 · O agente investiga cada alerta ✅

Entregue na change `agente-investiga-cada-alerta`. O aceite com alertas reais da Credenciadora é
feito junto com o S2, no primeiro deploy.

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

## S3.1 · Até a causa raiz ✅

Entregue na change `investigacao-ate-a-causa-raiz`, depois do laboratório real com a pedidos-api
na AWS. O agente valida cada hipótese, chega à causa raiz e propõe um plano de ação concreto
(rollback, religar o recurso), além de consultar a AWS só para leitura: estado dos recursos por
tag, CloudTrail e CloudWatch. É o S11 adiantado com escopo mínimo. Também corrigiu a leitura de
logs (WARN e acima), as séries (1 min, com o minuto da mudança) e os deploys (com a mensagem do
commit).

## S3.2 · Memória viva ✅

Change `memoria-viva`, adiantada antes do S4 e no lugar do antigo S7. O agente monta a própria
base de conhecimento estudando o ambiente na primeira subida (alertas do domínio → serviços →
repositórios → pipelines → AWS → integrações nos traces), mantém em dia com um estudo diário,
aprende com cada incidente encerrado (episódio + dossiês) e com o que o time ensina no chat,
lembra na análise ("já vi isso"), na validação e no chat, e pode reaprender do zero pela página
`/memoria`. Aprende sozinho; o time corrige.

## S3.3 · Memória com RAG ✅

Change `memoria-com-rag`: embeddings locais (fastembed/ONNX, `memory.rag.size: small | large`,
modelos embutidos na imagem), pgvector no mesmo Postgres, trechos por seção de dossiês, episódios,
notas e dos `.md` dos repositórios, e busca híbrida (determinístico → RRF de vetor e texto) no
contexto da investigação e no `recall`. Medido por eval de recuperação e no laboratório real.

## S4 · On-call acionado sozinho (adiado)

> Depende de conta no Spike.sh (API key, política de escalonamento e webhook de ack) para
> testar ponta a ponta. Fica para quando a conta existir.


- **Valor:** P0/P1 acionam o Spike.sh em minutos, com o link da página do incidente e a análise
  inicial já anexada; o time assume, reclassifica e encerra pelo war room.
- **Escopo:** acionamento com chave de deduplicação e limite por 10 min; estados Detectado →
  Acionado → Em tratamento; ack do Spike move o estado; ações rápidas no war room (assumir IC,
  mudar severidade, separar/juntar, encerrar — encerrar resolve no Spike); autor identificado por
  nome (fase 1); timestamps de TTD/TTA/TTM/TTR; **modo sombra**.
- **Ports:** `Pager`.
- **Aceite:** em modo sombra, o canal mostra os acionamentos que teriam acontecido; após a
  calibração, um P0/P1 real aciona o on-call.

## S5 · Agente acompanha a crise ✅

Change `acompanha-a-crise`: ciclos de acompanhamento em P0/P1 (tendência, taxa de erro, resumo), card
só quando muda, ciclo antecipado por alerta ou mensagem, proposta de mitigar ou encerrar após
15 min normalizado, volta determinística de mitigado para em tratamento e impacto financeiro
estimado. Ficaram de fora a investigação profunda separada (os ciclos cumprem esse papel).


- **Valor:** o time foca em resolver; o canal fica informado e o registro em dia.
- **Investigação profunda (P0/P1):** depois da análise inicial, ciclos até 10 min ou confiança
  mínima; leitura de código pelo mapa serviço → repositório (`read_file`); hipóteses no war room.
- **Escopo:** ciclos de 5 min, update só quando muda (e curto a cada 15 min sem mudança);
  impacto financeiro estimado; chat com o agente no war room; gatilho de ciclo por mensagem ou
  alerta agrupado; proposta de Mitigado e de encerramento após 15 min estáveis, com confirmação
  humana; Monitorando volta para Em tratamento se piorar.

## S6 · Post mortem em minutos ✅

Change `post-mortem`: rascunho ao resolver (métricas e timeline do log, demais seções pelo modelo,
blameless), edição por seção com versões, regenerar seção, plano de ação estruturado, ciclo de
vida rascunho → em revisão → aprovado → encerrado, PDF e lista com filtros e itens vencidos.


- **Valor:** post mortem blameless no mesmo dia, com a timeline real.
- **Escopo:** rascunho gerado do log de eventos com o template da spec; edição por seção com
  versões e autor; "regenerar seção"; ciclo Rascunho → Em revisão → Aprovado → Encerrado; PDF;
  lista com filtros e action items vencidos.
- **Ports:** `DocumentRenderer`.

## S7 · Post mortem alimenta a memória ✅

Change `post-mortem-alimenta-a-memoria`: post mortem aprovado reescreve o episódio como
confirmado e atualiza os dossiês, "era o mesmo problema?" no "já vi isso", avaliação
estruturada do agente no post mortem e a página `/metricas` por semana ou mês.


- **Valor:** o que o time escreve no post mortem vira conhecimento, e dá para medir se o agente
  está acertando mais.
- **Escopo:** post mortem aprovado alimenta a memória viva (S3.2) pelo mesmo caminho do
  encerramento; confirmação do time "era o mesmo problema" nos casos de "já vi isso"; métricas de
  acerto do agente por período (hipótese principal certa, tempo até a hipótese certa, quantas
  vezes a memória ajudou).
- **Evals:** investigação com e sem memória nos mesmos incidentes reais.

## S8 · Menos ruído ✅

Change `menos-ruido`: triagem de todo incidente novo no worker, sugestão de junção com outro
aberto na janela (juntar é humano, com "Juntar com…" no war room) e severidade sugerida pelo
agente quando nenhuma regra casa (só sobe).


- **Valor:** menos incidentes duplicados e menos alertas sem severidade.
- **Escopo:** agrupamento pelo LLM quando a regra determinística não decide; sugestão de
  severidade quando nenhuma regra casa (na dúvida, o nível mais alto); motivo registrado na
  timeline.

## S9 · Console de operação ✅

- **Valor:** respostas sobre a saúde do ambiente sem incidente aberto.
- **Escopo:** chat geral com sessões salvas; respostas com evidência e gráficos; "abrir incidente
  a partir desta conversa"; prioridade menor que incidentes e limite de tokens próprio.

## S10 · Transcrição de call ✅

- **Valor:** o que foi decidido na call entra no registro sem esforço manual.
- **Escopo:** upload da transcrição; extração de decisões, ações, horários e participantes como
  eventos "origem: transcrição"; reavaliação das hipóteses.

## S11 · AWS completa ✅

O básico (recursos por tag, CloudTrail e CloudWatch) entrou no S3.1. Fica para cá o restante.


- **Valor:** hipóteses que também olham a infraestrutura AWS.
- **Escopo:** role somente leitura por conta; AWS Health, alarmes do CloudWatch, RDS, ELB,
  EKS/ECS, quotas e CloudTrail como ferramentas neutras da investigação.
- **Ports:** `CloudInventory`.

## S11.1 · Cluster somente leitura ✅

- **Valor:** a causa raiz que está dentro do Kubernetes: pods quebrando e mudanças feitas fora do
  pipeline (`kubectl edit`, `set env`, `scale`).
- **Escopo:** estado e mudanças do serviço em vários clusters (contas PCI e não PCI), `enabled`
  por cluster no YAML, valores ocultos nos clusters PCI, RBAC só leitura.
- **Ports:** `ClusterInventory`.

## S11.2 · Alarme normalizado na tela ✅

- **Valor:** quem conduz o incidente percebe na hora que o alarme parou (hora de mitigar ou
  encerrar) ou voltou a disparar (a mitigação não segurou).
- **Escopo:** faixa no topo do war room com a ação em destaque, aviso na tela e título piscando na
  transição, alertas e estado ao vivo, selo "alarme normalizado" na lista.
- **Ports:** nenhum.

## S11.3 · War room redesenhado ✅

- **Valor:** quem está no plantão lê, de cima para baixo e num relance, o que acontece, quanto
  dói, o que fazer agora e como o Grolla chegou lá.
- **Escopo:** barra fixa com a pílula do alarme e um botão, cartão "Agora", hipóteses com o
  veredito num lugar só, linha do tempo filtrável, celular com o próximo passo embaixo.
- **Ports:** nenhum.

## S11.4 · Todas as telas na mesma linguagem ✅

- **Valor:** lista, post mortem, chat, memória e métricas dizem num relance o que precisa de
  atenção, com filtros onde o time procura.
- **Escopo:** lista com o que precisa de você agora e filtros; post mortem com totais, filtros,
  próxima ação e índice com o que falta; chat ao lado das conversas e em primeiro no menu; memória
  com os assuntos ao lado; métricas com o período atual em destaque e o gráfico.
- **Ports:** nenhum.

## S11.5 · Agente cercado ✅

- **Valor:** o Grolla só investiga e só fala do domínio, mesmo diante de pedidos ou dados mal-intencionados.
- **Escopo:** escopo na resposta com recusa fixa, dados externos marcados, nota do chat `aprendido` e nota-ordem
  recusada, selo em comando destrutivo, limite por pessoa, teste de capacidade e evals de ataque.
- **Ports:** `MessageCounter`.

## S11.6 · Modelo no Bedrock

- **Valor:** times que só podem usar LLM pela própria conta AWS rodam o Grolla com o mesmo Claude.
- **Escopo:** `llm.provider: bedrock` e `llm.region` no YAML, credencial padrão da AWS, IDs do Bedrock
  em `llm.stages`, suítes de contrato no modo Bedrock e a política `deploy/iam-bedrock.json`.
- **Ports:** nenhum; é um modo do adapter da Anthropic.

## S12 · Times, login e administração

- **Valor:** vários times usam o mesmo Grolla, cada um vendo e trabalhando só no que é seu.
- **Escopo:**
  - **Times:** criação de times e associação de cada time a um domínio de alarmes (labels ou
    regras dos alertas). Cada time vê só os seus alarmes e incidentes e tem as suas métricas,
    post mortems, memória e chat: tudo separado.
  - **Login com Entra ID:** autenticação pelo Entra ID (OIDC) no lugar do nome digitado; o autor
    das ações e do chat vem do login.
  - **Painel admin:** times, membros e perfis (por exemplo admin, membro, leitor).
  - **Troca de time:** quem participa de mais de um time escolhe o time ativo no topo da página
    e passa a ver e trabalhar nele.
- **Ports:** `Identity` (o login; Entra ID por trás).

## Fase 2 (depois do S12)

Thresholds calibrados com
dados; action items como work items no Azure DevOps; painel de custo e orçamento de LLM; vigias
criados pelo chat.

## Em aberto

- Labels reais dos alertas da Credenciadora (filtro e serviço são configuráveis no YAML).

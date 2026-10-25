## Why

O SigNoz já manda o próprio webhook de alerta para o Teams da Credenciadora. Por isso, um card que só repete o alerta, como o do S2, tem pouco valor. O S3 do roadmap faz o agente valer mais que o alerta: todo incidente, de qualquer severidade, recebe em segundos um card "estou investigando". Em até 5 minutos chega a análise, com o que o agente avaliou, hipóteses com evidência e um plano proposto. Ver `docs/initial-spec.md`, seções "Escopo da v1" (tratamento por severidade), "Investigação" e "Mensagens no Teams". É também o slice que leva o S2 para produção: o aceite dos dois é feito junto.

## What Changes

- **Card "investigando"** no lugar do card "incidente novo" do S2. É postado sem esperar o LLM e traz a anomalia, o serviço, a evidência do alerta, a severidade, o que o agente vai checar e o botão **Abrir incidente**.
- **Análise inicial de todo incidente novo**:
  - O pedido de análise é gravado na mesma transação que abre o incidente. Um job do worker executa a análise com limite de tempo (padrão 5 min) e de tokens, ambos configuráveis.
  - Um alerta agrupado num incidente que já tem análise não abre outra.
- **Ferramentas neutras da investigação**: `query_metric`, `search_logs`, `find_traces` e `recent_deploys`, implementadas sobre os ports `Telemetry` e `ChangeHistory`. Cada chamada é registrada; é desse registro que sai a lista "o que avaliei", e não do que o LLM diz ter feito.
- **Resultado estruturado e validado**:
  - Avaliações feitas, cada uma com o achado.
  - Hipóteses ranqueadas, com título, confiança de 0 a 1 e evidências com link.
  - Plano proposto: passos sugeridos ao time; o agente não executa nada.
  - O resultado é gravado como evento no incidente.
- **Card "análise"** com as avaliações, as hipóteses, o plano e os botões **Abrir incidente** e **Ver no SigNoz**.
- **Análise parcial garantida.** Se o LLM falhar, se a análise estourar o tempo ou o orçamento, ou se o worker cair no meio, o card de análise sai mesmo assim, marcado como parcial, com o que foi avaliado e o motivo.
- **Página do incidente** ganha a seção de análise: estado, avaliações, hipóteses e plano.
- **Fornecedores**:
  - **SigNoz via MCP.** O adapter usa só uma lista fechada de ferramentas de leitura. O MCP foi escolhido porque, no futuro, o agente vai criar dashboards.
  - **Azure DevOps** pela API REST, com token somente leitura, e um mapa serviço → repositório no YAML.
  - **Anthropic** (Claude), com o modelo da etapa `investigation` no YAML.
- **Configuração**:
  - O YAML ganha as seções `investigation` (tempo e tokens da análise inicial), `llm` (modelos por etapa), `signoz` (URL do MCP e URL base para links) e `azure_devops` (organização e mapa serviço → repositório).
  - Os segredos ficam no ambiente: `GROLLA_ANTHROPIC_API_KEY`, `GROLLA_SIGNOZ_API_KEY` e `GROLLA_AZURE_DEVOPS_TOKEN`.
  - O worker passa a ler o YAML.
- **Uso do LLM registrado** por chamada (etapa, modelo, tokens) em `llm_usage`.
- **Fakes e console**:
  - O `grolla-fakes` ganha a API da Anthropic roteirizada, um servidor MCP fake do SigNoz e a API do Azure DevOps.
  - O console de testes mostra o "investigando" seguido da análise.
  - O console escolhe o roteiro do LLM fake (análise completa, LLM fora do ar, análise lenta).
  - Para testar com o Claude de verdade sobre a telemetria fake, o README documenta como subir o worker com a chave real.
- **Evals**: `evals/` ganha os primeiros casos (telemetria roteirizada e hipótese esperada), rodados com o modelo real por `make evals`, fora do `make check`.

## Non-goals

- **Investigação profunda de P0/P1**, ciclos de acompanhamento e updates periódicos: ficam no S5.
- **Incidentes parecidos** ("já vi isso no #N"): ficam no S5, quando houver histórico.
- **Leitura de código** (`read_file`) e **ferramentas AWS**: ficam no S5 e no S11.
- **Criar dashboards ou qualquer escrita no SigNoz.** O MCP fica preparado para isso, mas o S3 só lê, e as ferramentas de escrita ficam fora da lista do adapter.
- **Acionamento do Spike.sh** e ações no war room: ficam no S4.
- **Nova análise quando a severidade sobe**: fica no S5, com a investigação profunda.
- **Validação dos modelos pela Models API no startup**: o modelo inválido aparece como análise parcial com o erro. Entra quando houver mais de uma etapa com LLM.
- **Atualizar o card "investigando"**: o Workflow do Teams só posta mensagens novas.
- **MCP do Azure DevOps**: no S3 basta uma consulta (deploys recentes), feita pela API REST.

## Capabilities

### New Capabilities

- `incident-analysis`: ciclo da análise inicial (pedido, execução, limites, resultado, análise parcial garantida, uma análise por incidente) e o registro de uso do LLM.
- `investigation-tools`: ferramentas neutras da investigação sobre telemetria e histórico de mudanças, o registro das avaliações e a fronteira somente leitura com os fornecedores.

### Modified Capabilities

- `incident-notifications`: o card de incidente novo vira "investigando", e entra o card de análise.
- `incident-pages`: a página do incidente mostra a análise.
- `team-config`: novas seções do YAML e novos segredos de ambiente.
- `service-skeleton`: o worker roda o job de análise e lê a configuração do time.
- `system-tests`: os fakes ganham os protocolos da Anthropic, do MCP do SigNoz e do Azure DevOps, e o console mostra a análise.

## Impact

- **Código**:
  - Domínio da análise e casos de uso no `grolla-core`.
  - Ports `LanguageModel`, `Telemetry` e `ChangeHistory`.
  - Adapters `anthropic`, `signoz` (agora também o cliente MCP) e `azure_devops`.
  - Fila de análise e `llm_usage` no `postgres`.
  - Card de análise no `teams`.
  - Job no `grolla-worker` e seção nova na página do `grolla-api`.
  - Protocolos novos no `grolla-fakes`, cenários e2e e `evals/`.
- **Banco**: migração 0003 (fila de análises e `llm_usage`).
- **Dependências novas**: `anthropic` e `mcp`, no `grolla-core` e no `grolla-fakes`.
- **Externo**:
  - Chave da API da Anthropic na EC2.
  - Servidor MCP do SigNoz alcançável pela EC2, com uma API key de papel **Viewer**.
  - Token somente leitura do Azure DevOps.
  - Mapa serviço → repositório da Credenciadora.
- **Custo**: uma análise por incidente novo, com limite de tokens configurável, medido em `llm_usage`.

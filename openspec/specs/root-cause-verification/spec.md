# root-cause-verification Specification

## Purpose

Levar a investigação até a causa raiz: validar cada hipótese da análise inicial e, com uma confirmada, propor um plano de ação concreto para o humano executar.
## Requirements
### Requirement: Duas fases até a causa raiz
Todo incidente analisado SHALL passar por duas fases.

1. **Análise inicial** (a do S3): avaliações e hipóteses ranqueadas, com o card ao humano e a página atualizada.
2. **Validação**: começa assim que a análise inicial termina e investiga cada hipótese em detalhe, até confirmá-la ou rejeitá-la. O objetivo é a causa raiz.

A validação SHALL ser pedida na mesma transação que encerra a análise inicial. Se a análise inicial terminar parcial e sem hipóteses, a validação SHALL NOT ser pedida.

#### Scenario: Validação começa sozinha
- **WHEN** a análise inicial de um incidente termina com duas hipóteses
- **THEN** a validação é pedida e o worker a executa sem ação humana

#### Scenario: Inicial sem hipóteses
- **WHEN** a análise inicial termina parcial por LLM indisponível, sem hipóteses
- **THEN** nenhuma validação é pedida

### Requirement: Veredito por hipótese
A validação SHALL dar um veredito a cada hipótese da análise inicial: confirmada, rejeitada ou inconclusiva. Cada veredito SHALL trazer a justificativa e ao menos uma evidência obtida nas ferramentas da investigação. As avaliações da validação SHALL ser registradas como na análise inicial, a partir das chamadas de ferramenta realmente feitas.

#### Scenario: Banco parado confirmado
- **WHEN** a hipótese é "banco indisponível" e as ferramentas mostram o banco `stopped` e uma parada manual às 21:42
- **THEN** a hipótese fica confirmada, com essas evidências, e a hipótese "regressão de deploy" fica rejeitada porque o problema começou 10 minutos depois do último deploy

### Requirement: Causa raiz e plano de ação concreto
Com uma hipótese confirmada, a validação SHALL registrar a causa raiz: uma frase, o que aconteceu, quando e em qual componente. SHALL registrar também um plano de ação concreto para o humano validar e executar.

Cada ação do plano:
- SHALL dizer o que fazer, onde e o efeito esperado, por exemplo "fazer rollback do pedidos-api para o build 20261025.1" ou "religar o RDS grolla-lab-db";
- SHALL NOT ser um passo de investigação;
- pode trazer um link de apoio;
- SHALL trazer, quando der para montá-los, os comandos que fazem a ação, como um passo a passo em ordem de até 5 comandos, de qualquer ferramenta do time (`kubectl`, `aws`, `helm`, `az`, a CLI de um banco); cada comando SHALL dizer onde executá-lo (o cluster e o namespace, a conta e a região, o pipeline ou o sistema), e comandos do mesmo passo podem rodar em lugares diferentes;
- um comando pode encadear outros (`&&`, `|`, `;`) quando eles rodam no mesmo lugar e fazem sentido juntos; lugares diferentes ou passos independentes SHALL virar comandos separados;
- cada comando SHALL usar só nomes e valores que as ferramentas mostraram (namespace, workload, réplicas ou revisão anteriores, build, recurso), sem `<placeholder>` nem valor inventado; sem um dado necessário, o comando que dependeria dele SHALL ficar de fora, e o efeito esperado diz o que falta;
- é só uma sugestão: o agente não executa nada.

Sem hipótese confirmada, a validação SHALL dizer que a causa não foi confirmada e o que faltou para confirmá-la. Nesse caso SHALL NOT apresentar ações como se a causa fosse conhecida.

#### Scenario: Plano direto
- **WHEN** a causa raiz confirmada é "deploy 20261025.2 introduziu KeyError em GET /pedidos"
- **THEN** o plano tem como primeira ação "fazer rollback do pedidos-api para o build 20261025.1", com o link do pipeline

#### Scenario: Nada confirmado
- **WHEN** todas as hipóteses ficam inconclusivas
- **THEN** a validação diz "causa raiz não confirmada", lista o que faltou e não traz plano de ação

#### Scenario: Comando e onde rodar
- **WHEN** a causa confirmada é "Deployment antifraude-api com 0 réplicas desde 15:16" e `cluster_state` mostrou o deployment no namespace `loja` do cluster `loja-prod`, com 2 réplicas antes da mudança
- **THEN** a primeira ação traz o comando `kubectl scale deploy/antifraude-api --replicas=2 -n loja` para rodar no "cluster loja-prod, namespace loja"

#### Scenario: Comando na AWS
- **WHEN** a causa confirmada é "RDS grolla-lab-db parado manualmente às 21:42" e `cloud_resources` mostrou o banco na região `us-east-1` da conta `producao`
- **THEN** a ação traz o comando `aws rds start-db-instance --db-instance-identifier grolla-lab-db --region us-east-1` para rodar na "conta AWS producao, região us-east-1"

#### Scenario: Passo a passo em dois lugares
- **WHEN** a causa confirmada é "deploy 20261025.2 introduziu KeyError em GET /pedidos", o pipeline `pedidos-api-cd` fez o deploy e o `pedidos-api` roda no namespace `loja` do cluster `loja-prod`
- **THEN** a ação de rollback traz, em ordem, o comando de reimplantar o build 20261025.1 para rodar no "Azure DevOps, pipeline pedidos-api-cd" e `kubectl rollout status deploy/pedidos-api -n loja` para rodar no "cluster loja-prod, namespace loja"

#### Scenario: Falta um dado para o comando
- **WHEN** a causa confirmada pede reverter uma configuração, mas nenhuma ferramenta mostrou o valor anterior
- **THEN** a ação vem sem o comando de reverter, e o efeito esperado diz que falta o valor anterior

### Requirement: Limites e garantia da validação
A validação SHALL ter limites próprios de tempo e de tokens, configuráveis. Ela SHALL sempre terminar, concluída ou parcial, pelas mesmas regras de garantia da análise inicial: falha do LLM, tempo esgotado, orçamento esgotado, resultado inválido ou interrupção. O uso do LLM SHALL ser registrado com a etapa `verification`.

#### Scenario: Validação estoura o tempo
- **WHEN** a validação passa do limite de tempo
- **THEN** ela termina parcial, com os vereditos já obtidos e o motivo, e o card da causa raiz diz que é parcial

### Requirement: Comando inválido descartado
O núcleo SHALL descartar, um a um, os comandos de uma ação que tiverem mais de uma linha, passarem de 300 caracteres ou vierem sem onde executar, e SHALL manter só os 5 primeiros. A ação SHALL continuar no plano com os comandos que sobrarem, na mesma ordem. O agente nunca executa os comandos.

#### Scenario: Comando encadeado curto
- **WHEN** o modelo devolve o comando `kubectl rollout undo deploy/pedidos-api -n loja && kubectl rollout status deploy/pedidos-api -n loja`
- **THEN** o comando fica, inteiro, com o seu lugar de execução

#### Scenario: Comando em várias linhas
- **WHEN** o modelo devolve um comando com uma quebra de linha no meio
- **THEN** esse comando sai, e a ação fica no plano com os outros comandos dela

#### Scenario: Comando sem onde rodar
- **WHEN** o modelo devolve um comando com o lugar de execução vazio
- **THEN** esse comando sai, e a ação fica no plano


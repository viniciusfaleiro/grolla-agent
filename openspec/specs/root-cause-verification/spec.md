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
- pode trazer um comando ou um link de apoio;
- é só uma sugestão: o agente não executa nada.

Sem hipótese confirmada, a validação SHALL dizer que a causa não foi confirmada e o que faltou para confirmá-la. Nesse caso SHALL NOT apresentar ações como se a causa fosse conhecida.

#### Scenario: Plano direto
- **WHEN** a causa raiz confirmada é "deploy 20261025.2 introduziu KeyError em GET /pedidos"
- **THEN** o plano tem como primeira ação "fazer rollback do pedidos-api para o build 20261025.1", com o link do pipeline

#### Scenario: Nada confirmado
- **WHEN** todas as hipóteses ficam inconclusivas
- **THEN** a validação diz "causa raiz não confirmada", lista o que faltou e não traz plano de ação

### Requirement: Limites e garantia da validação
A validação SHALL ter limites próprios de tempo e de tokens, configuráveis. Ela SHALL sempre terminar, concluída ou parcial, pelas mesmas regras de garantia da análise inicial: falha do LLM, tempo esgotado, orçamento esgotado, resultado inválido ou interrupção. O uso do LLM SHALL ser registrado com a etapa `verification`.

#### Scenario: Validação estoura o tempo
- **WHEN** a validação passa do limite de tempo
- **THEN** ela termina parcial, com os vereditos já obtidos e o motivo, e o card da causa raiz diz que é parcial


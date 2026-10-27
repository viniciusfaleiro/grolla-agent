# incident-analysis Specification

## Purpose

Garantir que todo incidente novo receba uma análise inicial do agente (avaliações reais, hipóteses e plano), com limites de tempo e tokens, e que ela sempre chegue, concluída ou parcial.
## Requirements
### Requirement: Todo incidente novo recebe uma análise inicial
Ao abrir um incidente, de qualquer severidade, o núcleo SHALL gravar um pedido de análise na mesma transação que cria o incidente. O `grolla-worker` SHALL executar a análise de forma assíncrona: a api SHALL NOT esperar o LLM para responder o webhook. Um alerta agrupado num incidente que já tem análise pedida SHALL NOT gerar outra análise.

#### Scenario: Incidente P3 é analisado
- **WHEN** um alerta abre um incidente P3
- **THEN** o incidente ganha um pedido de análise e o worker o executa

#### Scenario: Alerta agrupado não reabre análise
- **WHEN** um segundo alerta do mesmo serviço entra num incidente que já tem análise
- **THEN** nenhuma análise nova é pedida

### Requirement: Resultado estruturado da análise
A análise SHALL produzir um resultado validado com:
- as avaliações feitas, cada uma com a ferramenta, o objeto avaliado e o achado;
- hipóteses ranqueadas, cada uma com título, confiança de 0 a 1 e ao menos uma evidência (texto e, quando houver, link);
- um plano proposto: passos sugeridos ao time, sem execução pelo agente;
- um resumo de uma frase.

O resultado SHALL ser gravado como evento `analysis_completed` no log do incidente. A lista de avaliações SHALL ser construída a partir das chamadas de ferramenta realmente executadas, e não do texto do LLM.

#### Scenario: Análise concluída
- **WHEN** o LLM chama `query_metric` e `recent_deploys` e devolve duas hipóteses e um plano
- **THEN** o evento `analysis_completed` tem duas avaliações (métrica e deploys), as duas hipóteses em ordem de confiança e o plano

#### Scenario: Resposta fora do formato
- **WHEN** o LLM devolve um resultado que não passa na validação
- **THEN** a análise termina como parcial, com o motivo "resultado inválido", e as avaliações já feitas são mantidas

### Requirement: Limites de tempo e de tokens
A análise inicial SHALL respeitar um limite de tempo (padrão 5 minutos) e um limite de tokens, os dois configuráveis por time. Ao atingir qualquer um dos limites, a análise SHALL terminar como parcial.

#### Scenario: Tempo esgotado
- **WHEN** a análise passa do limite de tempo configurado
- **THEN** ela é interrompida e termina como parcial com o motivo "tempo esgotado"

#### Scenario: Orçamento esgotado
- **WHEN** os tokens usados atingem o limite configurado
- **THEN** a análise termina como parcial com o motivo "orçamento esgotado"

### Requirement: A análise sempre chega
Todo pedido de análise SHALL terminar, concluído ou parcial, e SHALL gerar o card de análise.

Uma análise parcial SHALL:
- registrar o evento `analysis_partial` com o motivo (falha do LLM, tempo, orçamento, resultado inválido ou interrupção do worker);
- trazer as avaliações feitas até ali.

Um pedido que ficou em execução por mais que o limite de tempo com folga, por queda do worker, SHALL ser encerrado como parcial na rodada seguinte.

#### Scenario: LLM fora do ar
- **WHEN** a API do LLM responde erro em todas as tentativas
- **THEN** o card de análise é postado como parcial, dizendo que a investigação não rodou e por quê

#### Scenario: Worker reiniciou no meio
- **WHEN** o worker cai durante uma análise e volta depois do limite
- **THEN** a análise é encerrada como parcial com o motivo "interrompida" e o card é postado

### Requirement: Uso do LLM registrado
Cada chamada ao LLM SHALL gravar a etapa, o modelo efetivamente usado, os tokens de entrada, os tokens de saída, os tokens de cache e o incidente.

#### Scenario: Custo por incidente
- **WHEN** uma análise faz três chamadas ao LLM
- **THEN** existem três registros de uso ligados ao incidente, com a etapa `investigation`

### Requirement: Já vi isso antes
O resultado estruturado da análise SHALL aceitar a referência opcional a um incidente passado, com o motivo da semelhança. Quando a análise traz a referência, o card de análise e a página do incidente SHALL mostrar "Já vi isso", com o título e a data do incidente passado, o link e o motivo, e a causa passada SHALL aparecer como hipótese com "como confirmar". Uma referência a incidente que não está entre os episódios entregues SHALL ser descartada.

#### Scenario: Mesmo problema de novo
- **WHEN** a análise de um incidente de `pedidos-api` aponta o episódio do incidente "pedidos-api com 5xx" de 20/10 com o motivo "mesmo erro de pool após deploy com migration"
- **THEN** o card mostra "Já vi isso: pedidos-api com 5xx (20/10)" com o link e o motivo, e uma hipótese repete a causa daquele incidente com como confirmar

#### Scenario: Referência inventada
- **WHEN** a análise aponta um incidente que não estava entre os episódios entregues
- **THEN** a referência é descartada e o card sai sem "Já vi isso"


## Why

Console, memória e métricas tinham o visual antigo: no console, a conversa caía para baixo de um
card solitário e deixava um vazio grande; a memória era um texto longo sem navegação por assunto; as
métricas eram só uma tabela com traços. A proposta visual aprovada está no canvas "War room do
Grolla — proposta de redesenho".

## What Changes

- **No menu, o console vira "Chat" e passa a ser o primeiro item**, a pedido do usuário. A URL
  continua `/console`.
- **Console:** duas colunas, com as conversas à esquerda (nova conversa, busca, "virou incidente") e
  a conversa ocupando o resto; o gráfico dentro da resposta; "Abrir incidente com esta conversa" no
  topo; sugestões com os serviços reais. Sem o vazio da tela antiga.
- **Memória:** título "O que o Grolla sabe", estado do estudo numa pílula, busca, assuntos à esquerda
  com o frescor de cada um (atualizado quando, novo sem estudo), e o assunto aberto à direita com
  cada registro num cartão (confiança, fonte, data, confirmar, editar, esquecer, versões) e os
  episódios.
- **Métricas:** título "Como o Grolla está indo", quatro números com contexto (o "—" diz quantas
  avaliações faltam e leva aos post mortems), gráfico de incidentes resolvidos por período e a tabela
  de detalhe, com o período vazio apagado (os traços continuam).

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `operations-console`: a sessão aparece ao lado da lista de conversas.
- `agent-memory`: a página da memória navega por assunto, com o frescor de cada um.
- `agent-metrics`: as métricas ganham os números do período atual e o gráfico.

## Non-goals

- Mudar o que o console responde, como a memória aprende ou como as métricas são calculadas.

## Impact

- `grolla-api`: `console.html`, `console_session.html`, `memory.html`, `memory_subject.html`,
  `metrics.html`, os módulos de página e CSS; testes das páginas.
- `e2e/scenarios`: trechos de texto que mudaram.

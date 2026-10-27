## Why

A lista de post mortems tinha um formulário de filtros pesado e linhas sem a causa nem a situação do
plano; o documento era uma pilha de seções iguais, sem dizer o que falta para aprovar. O usuário pediu
filtros nos post mortems, e a proposta visual aprovada está no canvas "War room do Grolla — proposta
de redesenho" (`docs/initial-spec.md`, post mortem).

## What Changes

- **Lista:** quatro números no topo (rascunhos para revisar, em revisão, itens do plano vencidos,
  encerrados no mês); filtros em linha (status em abas; severidade; serviço; "só com itens vencidos";
  "sem avaliação do agente"; período) com contagem e "Limpar filtros"; cada linha com a causa numa
  frase e a situação do plano ("3 itens sem dono", "4 de 4 concluídos").
- **Documento:** barra com a próxima ação do ciclo (começar revisão, aprovar, encerrar) e os números
  do incidente (duração, TTD, TTA, TTM, TTR); índice lateral com o que falta em cada seção ("3 sem
  dono", "falta você"); texto em fonte de leitura; plano de ação em tabela com dono, prazo e
  situação; a avaliação do agente num cartão com perguntas diretas.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `post-mortem`: a lista ganha os números, os filtros novos e a situação do plano; o documento ganha
  a barra, o índice e o plano em tabela.

## Non-goals

- Mudar o ciclo de vida, as seções, a geração ou o PDF.

## Impact

- `grolla-api`: `postmortems.html`, `postmortem.html`, `post_mortem_pages.py`, rota da lista
  (filtros novos) e CSS; testes das páginas. Se a lista precisar de um campo a mais (causa,
  avaliação), ele sai do que o caso de uso já devolve ou de uma leitura por linha mostrada.
- `e2e/scenarios`: trechos de texto e um filtro novo.

## Context

`/postmortems` lista com filtros de status, severidade, serviço e período (`list_post_mortems`),
mostrando título, severidade, serviços, status, data e itens vencidos. `/postmortems/{id}` mostra o
cabeçalho, o ciclo, as seções numeradas com editar, regenerar e versões, o plano de ação e a
avaliação.

## Goals / Non-Goals

**Goals:** saber o que falta (revisar, dar dono, avaliar) sem abrir cada documento, filtrar melhor e
ler o documento com conforto.

**Non-Goals:** ciclo de vida, seções, geração, PDF.

## Decisions

### 1. Filtros novos em cima do que a lista já lê

"Só com itens vencidos" e "sem avaliação do agente" filtram a lista já carregada (o resumo já traz
os vencidos; a avaliação sai do post mortem). Os números do topo contam a mesma lista, sem filtro.

- *Alternativa:* filtros no SQL. Descartada: a lista é pequena e o caso de uso já devolve o resumo.

### 2. Causa e plano na linha

A causa é a primeira frase da seção "Resumo executivo" do post mortem, ou a causa raiz do incidente
quando a lista já a tiver; a situação do plano conta os itens sem dono e os concluídos.

### 3. Índice lateral calculado

`post_mortem_pages.outline_of(pm)` devolve, por seção, o autor da última versão ("Grolla", "do log"
ou o nome) e o que falta: itens sem dono ou prazo no plano, avaliação pendente.

### 4. Barra com a próxima ação

A próxima ação é a transição permitida no status atual (começar revisão, aprovar, encerrar), a mesma
que a página já oferece, só em destaque; o PDF aparece desabilitado com "PDF depois de aprovar"
enquanto não houver.

## Contratos

Sem port, evento ou migração. Query de `/postmortems` ganha `vencidos=1` e `sem_avaliacao=1`.

## Risks / Trade-offs

- [Primeira frase do resumo pode ser longa] → Cortada em uma linha com reticências.

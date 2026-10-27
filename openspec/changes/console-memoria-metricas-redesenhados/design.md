## Context

`/console` lista as sessões e tem a nova conversa; `/console/{id}` mostra a conversa (polling) e
"Abrir incidente". `/memoria` mostra o dossiê do ambiente, os assuntos, o estudo e os aprendizados
falhos; `/memoria/{assunto}`, o dossiê, os episódios e as notas. `/metricas` mostra a tabela por
período.

## Goals / Non-Goals

**Goals:** a mesma linguagem do war room nas três telas, menos rolagem e mais contexto.

**Non-Goals:** comportamento do console, da memória e das métricas.

## Decisions

### 1. Console em duas colunas na própria sessão

`/console/{id}` passa a renderizar também a lista de sessões à esquerda (as mesmas que `/console`
lê). `/console` mostra a lista à esquerda e a nova conversa à direita, com as sugestões. "Abrir
incidente" vira um botão no topo da conversa, com o mesmo popover.

### 2. Sugestões com os serviços reais

As sugestões do console usam os serviços que a memória conhece (os assuntos do tipo serviço); sem
nenhum, as sugestões genéricas de hoje.

### 3. Memória: assuntos à esquerda em todas as páginas da memória

`/memoria` e `/memoria/{assunto}` mostram a mesma barra de assuntos, com a data do último registro
de cada um e "sem estudo" quando o assunto não tem dossiê. A busca filtra a barra no navegador
(sem JS, a lista aparece inteira). Os registros viram cartões com as ações de hoje.

### 4. Métricas: o período atual em destaque

Os quatro números são o período mais recente da tabela. O gráfico é um SVG desenhado no servidor,
como os do console, com os incidentes resolvidos dos 6 períodos. Período sem nada diz "sem
incidentes" na tabela.

## Contratos

Sem port, evento ou migração.

## Risks / Trade-offs

- [A sessão do console carrega a lista de sessões em toda abertura] → É a mesma leitura curta de
  `/console`; o polling da conversa não muda.

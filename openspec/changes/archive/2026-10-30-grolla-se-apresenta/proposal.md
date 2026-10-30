## Why

Quem abre o Grolla pela primeira vez não sabe o que ele faz quando toca um alarme nem onde estão
as funcionalidades. Hoje a primeira tela já é a lista de incidentes, sem nenhuma explicação. O
Grolla nasceu para ser o colega de SRE do time (`docs/initial-spec.md`, "Visão geral"), então a
melhor apresentação é ele mesmo falando, de um jeito leve e rápido. O design foi aprovado num
protótipo clicável.

## What Changes

- Na primeira visita de um navegador, um modal mostra o mascote do Grolla se apresentando:
  - a boca mexe enquanto ele fala;
  - a fala aparece digitada num balão de quadrinho.
- São 6 falas curtas, uma frase cada, digitadas em cerca de 2 segundos:
  1. quem ele é;
  2. o alarme: ele abre o incidente, dá a severidade e avisa o Teams;
  3. a investigação: métricas, logs, traces, deploys, AWS e cluster;
  4. a causa raiz, com o comando e onde rodar, e quem aplica é o time;
  5. o chat do war room;
  6. normalizado, post mortem e memória, terminando em "Bora?".
- Embaixo de cada fala aparece uma cena com um pedaço da tela real: o incidente, as checagens,
  o comando com "Onde rodar", o chat e o normalizado.
- Navegação:
  - O clique no meio da fala mostra a fala inteira. O clique seguinte passa para a próxima.
  - Funciona também com o botão Próximo, as setas, Enter e Esc.
  - **Pular** fecha o modal em qualquer etapa. **Começar** fecha na última.
- Depois de fechar, o modal não abre sozinho de novo naquele navegador. O link "Conhecer o
  Grolla" na barra do topo reabre o modal.
- O modal não abre sozinho no war room de um incidente aberto, para não atrapalhar quem chega
  pelo card do Teams no meio de uma crise. Ele abre na próxima página que a pessoa visitar.
- Com "reduzir movimento" ligado no sistema, a fala aparece inteira e o mascote fica parado.

## Non-goals

- Guardar no servidor quem já viu a apresentação. O "já vi" fica no navegador. Quem limpa os
  dados do navegador vê a apresentação de novo.
- Personalizar as falas pelo nome do autor ou pelo time, ou gerar as falas com o LLM.
- Tour guiado que aponta os botões reais de cada tela.
- Traduzir para outros idiomas.

## Capabilities

### New Capabilities
- `first-visit-welcome`: o modal de boas-vindas em que o Grolla se apresenta na primeira visita,
  com as falas, a navegação, a lembrança no navegador e o link para rever.

### Modified Capabilities
<!-- nenhuma: as páginas existentes só ganham o modal pelo layout comum -->

## Impact

- `packages/grolla-api`:
  - `templates/base.html` ganha o modal e o link "Conhecer o Grolla";
  - o war room desliga a abertura automática;
  - entram um `static/welcome.js` e os estilos em `war-room.css`;
  - o SVG do mascote passa a ter os grupos `boca`, `olhos`, `cabeca` e `braco` para a animação.
- `e2e/scenarios`: um cenário confere que as páginas trazem a apresentação e que o war room de um
  incidente aberto não a abre sozinho.
- Sem mudança no core, no banco, no worker nem nos ports. Não há dependência nova.

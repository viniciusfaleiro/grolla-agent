## Context

O layout comum (`templates/base.html`) serve todas as páginas: lista de incidentes, war room,
post mortems, console, memória e métricas. O mascote existe em `static/grolla.svg` e
`static/grolla-avatar.svg`. As páginas usam HTMX, `static/war-room.js` e os tokens de
`static/war-room.css`, com tema claro e escuro.

O design foi aprovado no protótipo clicável https://claude.ai/artifact/9PGTASE1wkRcQ7GKHa3fnZ,
que é a referência visual e de comportamento desta change.

## Goals / Non-Goals

**Goals:**
- Apresentar o Grolla em menos de 30 segundos, com 6 falas e um clique por fala.
- Não pedir nada ao servidor nem ao LLM.
- Não atrapalhar quem chega no war room no meio de um incidente.

**Non-Goals:** os da proposta: lembrança no servidor, falas personalizadas ou geradas, tour que
aponta botões reais e outros idiomas.

## Decisions

1. **Tudo no navegador: HTML no layout e um script estático.**
   - O modal é um trecho de `base.html`, `_welcome.html` incluído no fim do `<body>`.
   - O comportamento fica em `static/welcome.js`, carregado com `defer`, e os estilos ficam em
     `war-room.css`, com os tokens que já existem.
   - As falas e as cenas ficam no HTML (`<template>` por fala), não no JavaScript. Assim o texto
     é revisável no template e testável no HTML servido.
   - *Mais simples considerada:* o modal todo dentro de `war-room.js`. Descartada porque mistura a
     apresentação com o comportamento do war room e deixa as falas fora do HTML, onde os testes
     não as veem.

2. **"Já vi" no `localStorage`.**
   - A chave `grolla.boas-vindas` recebe `"vista"` quando o modal fecha.
   - Leitura e escrita ficam dentro de `try/catch`. Com o armazenamento bloqueado, o modal abre
     em toda visita, o que é aceitável.
   - *Mais simples considerada:* um cookie lido pelo servidor. Descartada porque obriga o
     servidor a decidir e muda todas as rotas. O `localStorage` resolve sem tocar no Python.
   - *Também considerada:* gravar por autor no banco. Descartada porque o autor só existe depois
     de a pessoa informar o nome, e a apresentação é para a primeira visita.

3. **O war room de um incidente aberto não abre sozinho.**
   - A página do incidente marca o `<body>` com `data-welcome="manual"` quando o estado não é
     resolvido nem descartado.
   - O script respeita a marca, não grava "vista" e deixa a abertura para a próxima página.
   - *Mais simples considerada:* abrir em toda página. Descartada porque quem chega pelo card do
     Teams num P1 precisa ver o incidente, não uma apresentação.

4. **O mascote animado é o SVG inline do mascote atual, com quatro grupos nomeados.**
   - Os grupos `boca`, `olhos`, `cabeca` e `braco` são animados por CSS. A boca muda a abertura
     (`scaleY`) a cada letra digitada: abre nas vogais e fecha nos espaços e na pontuação.
   - *Mais simples considerada:* um GIF ou dois quadros alternados. Descartada porque o GIF não
     segue o tema nem o "reduzir movimento", e os quadros alternados não acompanham a fala.

5. **Digitação rápida e clique que completa.**
   - O texto entra duas letras a cada 32 ms, cerca de 2 segundos por fala.
   - O clique durante a digitação completa a fala, e o clique seguinte avança.
   - *Mais simples considerada:* mostrar a fala inteira de uma vez. Descartada porque a fala
     digitada é o que dá a sensação de o Grolla estar falando, como pedido.

6. **"Conhecer o Grolla" na barra do topo.**
   - O layout não tem rodapé. O link entra na barra do topo, depois da navegação, como um botão
     discreto.
   - *Mais simples considerada:* criar um rodapé, como sugeriam as notas do protótipo.
     Descartada para não acrescentar uma área nova só por um link.

## Contratos

- **Ports, modelos, eventos, banco:** nenhum.
- **Endpoints:** nenhum novo.
- **Templates:**
  - `base.html`: inclui `_welcome.html` e o botão `#conhecer-grolla` na barra do topo;
  - `_welcome.html`: o modal (`#boas-vindas`, `role="dialog"`), o mascote com os grupos
    `#boca`, `#olhos`, `#cabeca` e `#braco`, e 6 `<template data-fala>`, cada um com a fala e a
    cena;
  - `incident.html` (war room): define `welcome_mode = "manual"` quando o incidente está aberto,
    e `base.html` escreve `data-welcome="{{ welcome_mode or 'auto' }}"` no `<body>`.
- **Estáticos:** `static/welcome.js` e os estilos `.welcome-*` em `static/war-room.css`.
- **Armazenamento no navegador:** a chave `grolla.boas-vindas` com o valor `"vista"`.

## Risks / Trade-offs

- [O "já vi" se perde ao limpar o navegador ou ao usar outro navegador] → A pessoa vê a
  apresentação de novo e pula com um clique. Está nos Non-goals.
- [O modal cobre a página na primeira visita] → Pular e Esc fecham na hora, e o war room de um
  incidente aberto nunca abre sozinho.
- [Peso a mais em toda página] → O SVG inline e as 6 falas somam alguns KB, e o script carrega
  com `defer`.

## Migration Plan

Nada a migrar. O deploy é o de sempre, e quem já usa o Grolla vê a apresentação uma vez. Para
voltar atrás, basta reverter o commit.

## Open Questions

Nenhuma.

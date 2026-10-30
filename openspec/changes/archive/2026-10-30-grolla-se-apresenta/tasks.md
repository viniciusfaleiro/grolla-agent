**Modo:** change de um agente só (área web). O cenário e2e é pequeno e fica com o mesmo agente.

## 1. grolla-web

- [x] 1.1 `_welcome.html`: o modal (`#boas-vindas`, `role="dialog"`, rotulado pela fala) com o mascote inline (grupos `#boca`, `#olhos`, `#cabeca`, `#braco`), o balão, a área da cena, os pontos das etapas, **Pular** e **Próximo**, e as 6 falas com as cenas em `<template data-fala>`, com o texto e as cenas do protótipo aprovado.
- [x] 1.2 `base.html`: inclui `_welcome.html`, o botão "Conhecer o Grolla" (`#conhecer-grolla`) na barra do topo, `data-welcome="{{ welcome_mode or 'auto' }}"` no `<body>` e `static/welcome.js` com `defer`.
- [x] 1.3 War room: `welcome_mode = "manual"` quando o incidente não está resolvido nem descartado.
- [x] 1.4 `static/welcome.js`:
  - abre sozinho só com `data-welcome="auto"` e sem `grolla.boas-vindas` no `localStorage`, com `try/catch`;
  - digita duas letras a cada 32 ms, com a boca acompanhando as vogais;
  - o clique durante a digitação completa a fala, e o clique seguinte avança;
  - setas, Enter e Esc funcionam, e Enter e espaço num botão não avançam duas vezes;
  - **Pular**, **Começar** e Esc gravam "vista" e fecham;
  - "Conhecer o Grolla" abre na primeira fala;
  - o foco vai para **Próximo** ao abrir e volta para onde estava ao fechar;
  - com `prefers-reduced-motion`, a fala aparece inteira e não há animação.
- [x] 1.5 Estilos `.welcome-*` em `war-room.css` com os tokens atuais: tema claro e escuro, celular (mascote em cima, balão sem a ponta), cenas e animações (boca, piscar, aceno, balanço) desligadas em `prefers-reduced-motion`.
- [x] 1.6 Testes do `grolla-api`:
  - a lista, o console, os post mortems, a memória e as métricas trazem o modal com as 6 falas, "Conhecer o Grolla" e `data-welcome="auto"`;
  - o war room de um incidente aberto traz `data-welcome="manual"`;
  - o war room de um incidente resolvido traz `data-welcome="auto"`.
- [x] 1.7 Conferir no navegador headless as falas 1 e 4, no tema claro, no escuro e no celular, o clique que completa e avança, o Pular e a segunda visita sem o modal.
- [x] 1.8 `make check` verde.

## 2. grolla-e2e

- [x] 2.1 Cenário `boas_vindas.yaml`:
  - `/incidents` traz "Opa! Eu sou o Grolla", "Conhecer o Grolla" e `data-welcome="auto"`;
  - o war room de um incidente recém-aberto traz `data-welcome="manual"`.

## 3. Fechamento

- [x] 3.1 `docs/decisoes.md` e `openspec/roadmap.md`.
- [x] 3.2 Revisar e atualizar os READMEs do `grolla-api` e do pai (Features, Roadmap e badge de status), no padrão do README do eng-performance.
- [x] 3.3 `make e2e` com todos os cenários verdes.
- [x] 3.4 `make fmt && make check` com tudo verde.

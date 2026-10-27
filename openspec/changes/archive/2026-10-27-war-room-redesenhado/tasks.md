> Change de um agente só: toca só o `grolla-api` (papel `grolla-web`) e trechos dos cenários e2e.

## 1. grolla-web

- [x] 1.1 `Situation`, `Step`, `situation_of`, `hypothesis_rows` e `event_kind` em `grolla_api.war_room`; testes.
- [x] 1.2 Barra do incidente (`#incident-status`) com a pílula do alarme, duração, IC e ciclo; `#next-step-main` com o próximo passo e o "⋯" com as ações raras.
- [x] 1.3 Cartão "Agora": frase, números, plano com o primeiro passo em destaque e o comando copiável, sugestões (juntar, já vi isso), pedido de nome.
- [x] 1.4 "Como o Grolla chegou lá": hipóteses com veredito, confiança, motivo, evidências recolhidas e descarte; consultas juntas.
- [x] 1.5 Linha do tempo com o registro de ação no topo, `data-kind` e filtros, eventos de sistema recolhidos; alertas compactos.
- [x] 1.6 CSS e JS: barra sticky, pílula, cartões, filtros, copiar, celular (atalhos e botão fixo embaixo), tema escuro.
- [x] 1.7 Testes da página revistos e novos.

## 2. e2e

- [x] 2.1 Trechos dos cenários que mudaram de texto.

## 3. Fechamento

- [x] 3.1 Verificação no laboratório com o navegador headless (desktop, celular, escuro, um ciclo do alarme).
- [x] 3.2 Registro de decisões e README da api e do pai.
- [x] 3.3 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 3.4 Rodar `make fmt && make check` e garantir tudo verde.

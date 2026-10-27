> Change de um agente só: toca só o `grolla-api` e trechos dos cenários e2e.

## 1. grolla-web

- [ ] 1.1 Console: lista de conversas à esquerda em `/console` e `/console/{id}`, conversa ao lado, "Abrir incidente" no topo, sugestões com os serviços conhecidos; testes.
- [ ] 1.2 Memória: barra de assuntos com o frescor, busca, pílula do estudo, registros em cartões com as ações; testes.
- [ ] 1.3 Métricas: quatro números do período atual com contexto, gráfico SVG dos resolvidos, tabela com "sem incidentes"; testes.
- [ ] 1.4 CSS (as três telas, celular, escuro).

## 2. e2e

- [ ] 2.1 Trechos dos cenários que mudaram de texto.

## 3. Fechamento

- [ ] 3.1 Verificação no laboratório com o navegador headless.
- [ ] 3.2 Registro de decisões e README da api e do pai.
- [ ] 3.3 Rodar `make e2e` e garantir todos os cenários verdes.
- [ ] 3.4 Rodar `make fmt && make check` e garantir tudo verde.

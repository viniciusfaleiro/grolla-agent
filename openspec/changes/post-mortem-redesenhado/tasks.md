> Change de um agente só: toca só o `grolla-api` e trechos dos cenários e2e.

## 1. grolla-web

- [x] 1.1 Lista: números do topo, filtros em linha (status em abas, severidade, serviço, vencidos, sem avaliação, período), contagem, limpar; causa e situação do plano por linha; testes.
- [x] 1.2 Documento: barra com a próxima ação e os números, índice lateral com o que falta, texto em fonte de leitura, plano em tabela, avaliação em cartão; testes.
- [x] 1.3 CSS (lista, documento, celular, escuro).

## 2. e2e

- [ ] 2.1 Trechos dos cenários que mudaram e o filtro de vencidos ou sem avaliação.

## 3. Fechamento

- [ ] 3.1 Verificação no laboratório com o navegador headless.
- [x] 3.2 Registro de decisões e README da api e do pai.
- [ ] 3.3 Rodar `make e2e` e garantir todos os cenários verdes.
- [ ] 3.4 Rodar `make fmt && make check` e garantir tudo verde.

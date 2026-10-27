> Change de um agente só: toca só o `grolla-api` e um cenário e2e.

## 1. grolla-web

- [x] 1.1 `IncidentFilter` (query, validação e `matches`) e o cartão de cada aberto (alarme, causa, próximo passo, esquecido); testes.
- [x] 1.2 `GET /incidents` com os filtros, os cartões dos abertos, a tabela dos encerrados (20 e "mostrar mais"), a contagem e "Limpar filtros".
- [x] 1.3 Contador de alarmes disparando na navegação.
- [x] 1.4 CSS (cartões, tabela, filtros, celular, escuro) e testes da página.

## 2. e2e

- [x] 2.1 Cenário: filtrar por serviço e severidade mostra só os incidentes certos.

## 3. Fechamento

- [x] 3.1 Verificação no laboratório com o navegador headless.
- [x] 3.2 Registro de decisões e README da api e do pai.
- [x] 3.3 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 3.4 Rodar `make fmt && make check` e garantir tudo verde.

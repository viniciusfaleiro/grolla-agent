> Change de agente único: só `e2e/fakes/` e o compose.

## 1. Console

- [x] 1.1 `httpx2` no `grolla-fakes`; `uv lock`.
- [x] 1.2 Rotas `GET /_console`, `POST /_console/alerts` (monta o payload do SigNoz e chama a api), `GET /_console/state` (alertas disparados, mensagens do canal e modo do Teams) e `POST /_console/teams` (202/500).
- [x] 1.3 Página `console.html`: formulário, lista com "resolver", canal com os cards renderizados e chave do Teams.
- [x] 1.4 Status gravado em cada requisição, para o canal distinguir entregue de recusada.
- [x] 1.5 Testes do console com `TestClient` e a api substituída por transporte fake.
- [x] 1.6 Variáveis do console no serviço `fakes` do `docker-compose.yml`.

## 2. Fechamento

- [x] 2.1 Revisar e atualizar os READMEs (fakes e pai).
- [x] 2.2 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 2.3 Rodar `make fmt && make check` e garantir tudo verde.

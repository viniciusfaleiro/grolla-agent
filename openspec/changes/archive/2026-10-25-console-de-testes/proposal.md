## Why

Para ver o S2 funcionando hoje, é preciso montar payloads com `curl` e ler JSON cru das requisições gravadas no fake. O usuário pediu uma tela de testes: disparar um alarme como se fosse o SigNoz e ver as mensagens do agente como se fosse o canal do Teams. Com ela, qualquer pessoa do time testa o fluxo inteiro na própria máquina, sem SigNoz e sem Teams reais.

## What Changes

- **Console de testes** no `grolla-fakes` (`GET /_console`), uma página só:
  - **Disparar alerta**: formulário com regra, serviço, severidade, time, resumo e descrição. O console monta o payload no formato do SigNoz e chama `POST /webhooks/alerts` da api com as credenciais do ambiente.
  - **Alertas disparados**: cada alerta enviado aparece numa lista com o botão "resolver", que manda o mesmo alerta como resolvido.
  - **Canal do Teams**: os cards que o worker postou no Teams fake aparecem como mensagens de canal (título, severidade, fatos, texto e botões com link), atualizados sozinhos.
  - **Teams aceitando ou falhando**: chave que faz o Teams fake responder 202 ou 500, para ver a retentativa da outbox.
- O serviço `fakes` do compose ganha a URL e as credenciais da api.

## Non-goals

- Não entra em imagem de produção nem no pacote `deploy/`: o console vive no `grolla-fakes`.
- Sem autenticação, sem persistência: a lista de alertas e as mensagens somem ao reiniciar o fake.
- Não é um renderizador completo de Adaptive Cards: só os elementos que o adapter do Teams usa hoje.
- Não substitui os cenários do `make e2e`.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `system-tests`: os fakes ganham um console manual de testes.

## Impact

- **Código**: `e2e/fakes/` (rotas do console e página HTML), `docker-compose.yml` (variáveis do serviço `fakes`).
- **Dependências**: `httpx2` no `grolla-fakes`, para chamar a api.

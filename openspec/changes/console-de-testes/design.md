## Context

O `grolla-fakes` já faz o papel do Teams no e2e: grava as requisições e responde pelo roteiro do cenário. O webhook da api espera o payload do SigNoz com HTTP Basic. A página do incidente já existe na api.

## Goals / Non-Goals

**Goals:** testar à mão o caminho de valor do S2 numa tela só.

**Non-Goals:** ver a proposta.

## Decisions

1. **No `grolla-fakes`, não na api.**
   - *Mais simples considerada:* uma página na própria api. Descartada porque poria código de teste numa imagem de produção.
   - O fake já recebe os cards do Teams, então mostrar o canal é ler o que ele já grava.
2. **O fake chama a api pelo servidor, não pelo navegador.**
   - O navegador chamar a api direto exigiria CORS e poria as credenciais no JavaScript.
   - O fake usa `GROLLA_FAKES_API_URL`, `GROLLA_FAKES_API_USER` e `GROLLA_FAKES_API_PASSWORD`. Os padrões são os do e2e.
3. **HTML num arquivo estático com JavaScript simples** (`fetch` e polling de 2 s), sem framework nem build.
   - *Mais simples considerada:* meta refresh. Descartada porque perderia o formulário a cada recarga.
4. **"Teams aceitando/falhando" reaproveita o roteiro.** A chave grava a rota `POST /teams/webhook` com 202 ou 500 no mesmo roteiro do e2e. Ao abrir o console sem rota, o Teams começa aceitando.
5. **Canal = requisições gravadas em `/teams/webhook`.**
   - Cada uma mostra o status que o fake respondeu.
   - Na retentativa, o canal mostra as tentativas recusadas apagadas e a entregue normal.

## Risks / Trade-offs

- [Rodar o `make e2e` com o console aberto] → os dois usam as mesmas portas; o console é para o stack `grolla-demo`, derrubado antes do e2e.

> Change de um agente só: toca só o `grolla-api` (papel `grolla-web`) e um cenário e2e. O agente
> pai faz tudo, sem commit de contrato separado.

## 1. grolla-web

- [x] 1.1 `Alarm`, `AlarmKind`, `alarm_of` e `alarm_normalized` em `grolla_api.war_room`; testes de cada caso (nunca teve alerta, disparando, normalizado, voltou, encerrado, próximo passo por estado).
- [x] 1.2 Faixa `#alarm` no topo do war room, com o horário, o "há X min" e o botão da ação em destaque ("Assumir como IC", "Marcar mitigado" ou "Encerrar", que abre o pedido de motivo); estilos em claro, escuro e celular.
- [x] 1.3 Bloco `#incident-status` no cabeçalho e tabela `#alerts`, trocados fora do lugar junto com a timeline quando chega evento; os formulários ficam fora da troca; testes das rotas.
- [x] 1.4 `war-room.js`: aviso na tela e título piscando só na transição, e o "há X min" a cada 30 s.
- [x] 1.5 Selo "alarme normalizado" na lista de incidentes; teste da página.

## 2. e2e

- [x] 2.1 `alarme_normalizado.yaml` confere a faixa "Alertas normalizados" na página e na timeline, e o selo na lista; um alerta que volta mostra "Voltou a disparar".

## 3. Fechamento

- [x] 3.1 Verificação no laboratório com o navegador headless: o alarme normaliza com a página aberta e aparecem a faixa, o aviso e o título piscando.
- [x] 3.2 Registro de decisões, roadmap e `CLAUDE.md`.
- [x] 3.3 Revisar e atualizar os READMEs da api e do pai (Features, Roadmap e badge de status).
- [x] 3.4 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 3.5 Rodar `make fmt && make check` e garantir tudo verde.

> Change de um agente só: toca só o `grolla-api` e um cenário e2e.

## 1. grolla-web

- [x] 1.1 `AlarmKind.FIRING` em `alarm_of`, com o horário do primeiro alerta; testes.
- [x] 1.2 Faixa "Alarme disparando desde HH:MM" em vermelho, com até 3 alertas disparando (regra e "abrir o alerta") e "e mais N"; os alertas também na faixa "voltou"; testes da página.

- [x] 1.3 Faixa à direita do título, dentro do bloco de estado, sem botão; pisca levemente enquanto dispara.
- [x] 1.4 Botões do próximo passo no polling da timeline; cada alerta uma vez na faixa; testes.

## 2. e2e

- [x] 2.1 `alarme_normalizado.yaml` confere "Alarme disparando desde" e o link antes de resolver.

## 3. Fechamento

- [x] 3.1 Verificação no laboratório com o navegador headless.
- [x] 3.2 Registro de decisões e README da api.
- [x] 3.3 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 3.4 Rodar `make fmt && make check` e garantir tudo verde.

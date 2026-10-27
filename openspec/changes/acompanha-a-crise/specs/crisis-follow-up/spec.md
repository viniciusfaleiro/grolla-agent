## ADDED Requirements

### Requirement: Ciclos de acompanhamento
Com `follow_up` no YAML, cada incidente aberto cuja severidade está em `follow_up.severities` (padrão P0 e P1) SHALL receber um ciclo de acompanhamento a cada `follow_up.cycle_minutes` (padrão 5), a partir do fim da análise inicial, até ser resolvido ou descartado. Em cada ciclo, o modelo SHALL receber o contexto do incidente (estado, análise, validação, timeline, chat, ciclo anterior e memória) e as ferramentas da investigação, e SHALL devolver:
- a tendência: `piorando`, `estavel`, `melhorando` ou `normalizado`;
- a taxa de erro atual do serviço, quando medida;
- um resumo de uma frase;
- se algo mudou desde o ciclo anterior;
- até 3 evidências com link.

Todo ciclo SHALL virar evento na timeline. Falha, tempo ou orçamento esgotado SHALL virar ciclo parcial, sem afetar o incidente. O incidente SHALL ter no máximo `follow_up.max_cycles` ciclos e `follow_up.max_tokens_per_incident` tokens de acompanhamento.

#### Scenario: P1 em tratamento
- **WHEN** um P1 está em tratamento há 12 min e a análise inicial terminou
- **THEN** ele já teve 2 ciclos, cada um na timeline com a tendência e o resumo

#### Scenario: P3 não é acompanhado
- **WHEN** um P3 está aberto
- **THEN** nenhum ciclo roda

#### Scenario: Teto atingido
- **WHEN** o incidente chega a `max_cycles`
- **THEN** não roda mais ciclo e a timeline registra que o acompanhamento parou pelo limite

### Requirement: Update só quando muda
Ao fim de cada ciclo, o card "Atualização" SHALL ir para o canal quando a tendência é diferente da do último card enviado ou quando o modelo diz que algo mudou. Sem mudança, SHALL sair um card curto ("sem mudança: <tendência>, <taxa de erro>") quando já se passaram 15 min do último card de atualização. O card SHALL trazer a tendência, a taxa de erro, o resumo, o impacto estimado e o link da página.

#### Scenario: Piorou
- **WHEN** o ciclo anterior disse `estavel` e o atual diz `piorando`
- **THEN** sai o card "Atualização" com a nova tendência

#### Scenario: Nada mudou
- **WHEN** três ciclos seguidos dizem `estavel` sem mudança
- **THEN** não sai card nenhum até completar 15 min do último, e então sai o card curto

### Requirement: Ciclo antecipado por alerta ou mensagem
Um alerta agrupado num incidente acompanhado, ou uma mensagem de uma pessoa no chat dele, SHALL antecipar o próximo ciclo para agora, desde que o último ciclo tenha começado há mais de 1 min.

#### Scenario: Alerta novo agrupado
- **WHEN** chega um alerta do mesmo serviço num P1 acompanhado e o último ciclo foi há 3 min
- **THEN** um ciclo roda em seguida, sem esperar os 5 min

### Requirement: Proposta de mitigar e de encerrar
Quando os ciclos dizem `normalizado` sem interrupção por 15 min, o agente SHALL propor:
- **marcar como mitigado**, se o incidente está detectado, informado ou em tratamento;
- **encerrar**, se o incidente está mitigado.

A proposta SHALL virar evento, card e aviso no war room, com o motivo. O agente SHALL NOT mudar o estado: a pessoa confirma pelos botões do war room. A mesma proposta SHALL NOT se repetir no mesmo estado do incidente.

#### Scenario: Normalizado há 15 min
- **WHEN** um P1 em tratamento tem ciclos `normalizado` há 15 min
- **THEN** o war room mostra "Grolla sugere marcar como mitigado" com o motivo, e o card vai para o canal; o estado continua em tratamento

#### Scenario: Volta a piorar antes dos 15 min
- **WHEN** depois de 10 min de `normalizado` um ciclo diz `piorando`
- **THEN** não há proposta e a contagem recomeça

### Requirement: Impacto financeiro estimado
Com `impact.services.<serviço>.per_minute` no YAML, o impacto estimado de um incidente SHALL ser a soma, por ciclo, de `per_minute × taxa de erro do ciclo × minutos desde o ciclo anterior` (o primeiro conta desde a detecção). O valor SHALL aparecer na página do incidente e no card de atualização, com a moeda do YAML e a palavra "estimado". Sem a configuração do serviço, o impacto SHALL NOT aparecer.

#### Scenario: Impacto do pedidos-api
- **WHEN** `pedidos-api` tem `per_minute: 1000` e dois ciclos de 5 min com 50% e 10% de erro
- **THEN** o impacto estimado é R$ 3.000,00 (2.500 + 500)

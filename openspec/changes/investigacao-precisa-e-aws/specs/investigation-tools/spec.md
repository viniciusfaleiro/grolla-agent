## ADDED Requirements

### Requirement: Logs mostram a causa, não só o sintoma
A ferramenta `search_logs` SHALL considerar logs de nível WARN e acima. A resposta SHALL trazer as 5 mensagens mais frequentes, cada uma com a quantidade e o nível. Mensagens que só diferem em números, endereços IP, endereços de memória ou identificadores SHALL ser agrupadas como uma só.

#### Scenario: Causa registrada como WARN
- **WHEN** a aplicação registra 112 ERROR "couldn't get a connection after 5.00 sec" e 22 WARN "connection to server … failed: server closed the connection unexpectedly"
- **THEN** a resposta traz as duas mensagens, com quantidade e nível

### Requirement: Deploys trazem a mensagem do commit
Cada deploy na resposta de `recent_deploys` SHALL trazer a primeira linha da mensagem do commit, além de horário, versão, branch, commit, autor e resultado.

#### Scenario: Deploy de revert
- **WHEN** o último deploy foi o commit "Revert \"Mostrar o valor com desconto na listagem de pedidos\""
- **THEN** a resposta mostra essa mensagem ao lado do build

### Requirement: Séries dizem quando a mudança começou
A ferramenta `query_metric` SHALL usar pontos de 1 minuto. O resumo SHALL dizer o primeiro minuto em que o sinal saiu do patamar do início da janela, com o valor antes e depois.

#### Scenario: Erros começam depois do último deploy
- **WHEN** a taxa de erro fica em 0% até 21:41 e sobe para 70% a partir de 21:42
- **THEN** o resumo diz que a mudança começou às 21:42, de 0% para 70%

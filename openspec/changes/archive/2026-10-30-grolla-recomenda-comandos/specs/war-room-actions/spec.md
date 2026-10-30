## ADDED Requirements

### Requirement: Confirmar que aplicou um comando
No war room, cada comando sugerido no plano SHALL ter o botão "Apliquei" para uma pessoa identificada. O clique SHALL registrar uma ação feita, com o autor e o horário do `Clock`, levando o passo, o comando e onde ele rodou. Não SHALL abrir formulário nem pedir texto. O registro SHALL ser o mesmo evento de "registrar uma ação feita", com o comando e o lugar como dados a mais, e SHALL aparecer na timeline como "Aplicou: <passo> — <comando> (<onde rodar>)". Confirmar o mesmo comando de novo SHALL gravar um novo registro, porque às vezes o comando roda mais de uma vez. O Grolla nunca executa o comando: o botão só registra o que a pessoa disse que fez.

#### Scenario: Ana aplica o primeiro comando
- **WHEN** "Ana" clica em "Apliquei" no comando `kubectl scale deploy/antifraude-api --replicas=2 -n loja` do passo "Restaurar as réplicas do antifraude-api", que roda no "cluster loja-prod, namespace loja"
- **THEN** a timeline mostra "Aplicou: Restaurar as réplicas do antifraude-api — kubectl scale deploy/antifraude-api --replicas=2 -n loja (cluster loja-prod, namespace loja)" com o autor "Ana" e o horário

#### Scenario: Sem nome
- **WHEN** alguém sem nome informado tenta confirmar um comando
- **THEN** a ação é recusada e nada é registrado

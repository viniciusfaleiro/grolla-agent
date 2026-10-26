## ADDED Requirements

### Requirement: Página do incidente é o war room
A página do incidente SHALL mostrar um cabeçalho com estado, severidade, duração, IC e TTA, TTM e TTR. Abaixo do cabeçalho, SHALL mostrar as ações permitidas no estado atual, o chat com o agente, a análise, a validação, os alertas e a timeline com autor.

O war room SHALL pedir um nome na primeira visita e guardá-lo num cookie, que identifica o autor das ações e mensagens.

O chat e a timeline SHALL se atualizar sozinhos a cada poucos segundos, sem recarregar a página.

#### Scenario: Primeira visita
- **WHEN** alguém abre o war room sem ter informado o nome
- **THEN** a página pede o nome antes de permitir ações e mensagens

#### Scenario: Resposta do agente aparece sozinha
- **WHEN** o agente responde uma pergunta enquanto a página está aberta
- **THEN** a resposta aparece no chat em poucos segundos, sem recarregar a página

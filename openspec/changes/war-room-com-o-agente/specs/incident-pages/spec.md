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

### Requirement: War room fácil de conduzir
O war room SHALL deixar claro, num relance, o estado do incidente e o próximo passo:
- a etapa atual dentro do ciclo Detectado → Em tratamento → Mitigado → Resolvido;
- a ação principal do estado em destaque (assumir, marcar mitigado ou encerrar);
- as outras ações a um clique, com o motivo pedido no próprio lugar.

O descarte de uma hipótese SHALL ficar junto da hipótese na análise, e a hipótese descartada SHALL aparecer como descartada, com autor e motivo.

A página SHALL funcionar no celular e em tema escuro, e SHALL ter a identidade do Grolla, o mascote do agente.

#### Scenario: Próximo passo em destaque
- **WHEN** alguém abre um incidente em tratamento
- **THEN** a etapa "Em tratamento" aparece marcada no ciclo e "Marcar mitigado" é a ação em destaque

#### Scenario: Hipótese descartada
- **WHEN** "Ana" descarta a hipótese "Adquirente lenta" com o motivo "latência normal"
- **THEN** a hipótese aparece na análise como descartada por Ana, com o motivo

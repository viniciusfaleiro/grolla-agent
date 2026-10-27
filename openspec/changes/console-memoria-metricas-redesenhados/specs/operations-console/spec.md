## MODIFIED Requirements

### Requirement: Sessões do console
A api SHALL servir `/console` com as sessões do time, as mais recentes primeiro, e o botão "Nova conversa". Uma sessão SHALL ter título (a primeira pergunta, cortada), autor, data e o histórico de mensagens. Qualquer pessoa identificada SHALL poder abrir uma sessão e continuar a conversa.

A lista de sessões SHALL ficar ao lado da conversa, tanto em `/console` quanto numa sessão aberta, com a sessão aberta marcada e as que viraram incidente indicadas. As sugestões de pergunta SHALL citar os serviços que o Grolla conhece, quando houver.

#### Scenario: Nova conversa
- **WHEN** Ana pergunta no console "o autorizador está saudável?"
- **THEN** nasce uma sessão com esse título, autora Ana, e a pergunta fica pendente de resposta

#### Scenario: Conversa ao lado da lista
- **WHEN** alguém abre uma sessão do console
- **THEN** a página mostra a lista de sessões com essa marcada e a conversa ao lado

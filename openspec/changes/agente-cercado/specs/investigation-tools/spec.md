## MODIFIED Requirements

### Requirement: Somente leitura nos fornecedores
O adapter de telemetria SHALL chamar no servidor MCP só as ferramentas de leitura de uma lista fechada, mantida no código. O adapter de histórico de mudanças SHALL usar um token somente leitura. O adapter da nuvem SHALL chamar só métodos `describe_*`, `list_*`, `get_*` e `lookup_*`, e o adapter do cluster SHALL fazer só requisições GET. Nenhuma ferramenta de escrita (criar, alterar ou apagar alerta, dashboard, view, canal, recurso de nuvem ou objeto do cluster) SHALL ser chamada. Um teste no `make check` SHALL conferir essas listas.

#### Scenario: Ferramenta de escrita fora da lista
- **WHEN** o adapter de telemetria recebe um pedido de ferramenta fora da lista de leitura
- **THEN** ele recusa sem chamar o servidor MCP

#### Scenario: Chamada de escrita na nuvem
- **WHEN** alguém acrescenta ao adapter da nuvem uma chamada `update_service`
- **THEN** o teste de capacidade falha

## ADDED Requirements

### Requirement: Worker executa as análises pedidas
O `grolla-worker` SHALL rodar um job periódico que pega os pedidos de análise pendentes, executa cada um com os limites configurados e encerra como parciais os que ficaram presos em execução. Duas rodadas SHALL NOT executar o mesmo pedido.

#### Scenario: Pedido pendente
- **WHEN** existe um pedido de análise pendente e o worker está rodando
- **THEN** a análise começa em até 10 segundos

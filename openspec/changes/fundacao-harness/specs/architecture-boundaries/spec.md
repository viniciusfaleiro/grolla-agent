## ADDED Requirements

### Requirement: Camadas hexagonais impostas no build
O `grolla-core` SHALL se organizar em `grolla.domain`, `grolla.application`, `grolla.adapters` e `grolla.bootstrap`. Os contratos do import-linter SHALL garantir que: `domain` não importa nenhuma outra camada; `application` importa só `domain`; `adapters` importam só `application` e `domain`; nada importa `bootstrap`, exceto os pacotes deployables; e não há ciclos de importação.

#### Scenario: Domínio importa um adapter
- **WHEN** um módulo de `grolla.domain` importa algo de `grolla.adapters`
- **THEN** o gate de arquitetura do `make check` falha

### Requirement: Núcleo livre de infraestrutura
`grolla.domain` e `grolla.application` SHALL NOT importar bibliotecas de infraestrutura, incluindo `fastapi`, `starlette`, `sqlalchemy`, `psycopg`, `alembic`, `procrastinate`, `httpx`, `boto3`, `mcp` e `anthropic`.

#### Scenario: Application importa SQLAlchemy
- **WHEN** um módulo de `grolla.application` importa `sqlalchemy`
- **THEN** o gate de arquitetura do `make check` falha

### Requirement: Fornecedores isolados em adapters independentes
Cada fornecedor SHALL viver num subpacote próprio de `grolla.adapters`. Subpacotes de adapters SHALL NOT importar uns aos outros. `grolla.bootstrap` SHALL ser o único módulo que liga um provider configurado ao adapter que o implementa.

#### Scenario: Um adapter importa outro
- **WHEN** um módulo de um subpacote de adapter importa um módulo de outro subpacote de adapter
- **THEN** o gate de arquitetura do `make check` falha

### Requirement: Vocabulário neutro no núcleo
O `make check` SHALL falhar se nomes de fornecedor (signoz, teams, spike, azure, ado, aws, boto, anthropic, claude, postgres) aparecerem em identificadores, strings ou comentários de `grolla.domain` ou `grolla.application`, sem diferenciar maiúsculas e minúsculas e considerando apenas palavras inteiras ou partes de identificadores separadas por `_`.

#### Scenario: Campo com nome de fornecedor no domínio
- **WHEN** uma entidade do domínio declara o campo `spike_incident_id`
- **THEN** o gate de vocabulário falha apontando arquivo e linha

### Requirement: Ports têm suíte de contrato
Todo port SHALL ter uma suíte de testes de contrato no `grolla-core`. Todo adapter real e todo fake em memória desse port SHALL rodar a mesma suíte. Neste slice, o port `Clock` SHALL ter a suíte, um fake controlável e um adapter de relógio do sistema passando nela.

#### Scenario: Fake e adapter cumprem o mesmo contrato
- **WHEN** a suíte de contrato do `Clock` roda
- **THEN** ela executa contra o fake e contra o adapter do sistema, e ambos passam

### Requirement: Deployables não dependem entre si
O `grolla-api` e o `grolla-worker` SHALL depender do `grolla-core` e SHALL NOT depender um do outro. O `grolla-core` SHALL NOT depender de nenhum deployable.

#### Scenario: Worker importa a api
- **WHEN** um módulo do `grolla-worker` importa algo do `grolla-api`
- **THEN** o gate de arquitetura do `make check` falha

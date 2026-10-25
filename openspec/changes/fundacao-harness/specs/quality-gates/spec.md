## ADDED Requirements

### Requirement: Um comando único roda todos os gates rápidos
O projeto pai SHALL expor `make check`, que roda, em todos os pacotes do workspace e também em `e2e/` (runner e `grolla-fakes`) e `scripts/`: verificação de formatação (ruff format), lint com zero warnings (ruff check, incluindo as regras de segurança do bandit), checagem de tipos em modo strict (pyright), contratos de arquitetura (import-linter), checagem de vocabulário de fornecedor, testes unitários, testes de integração, verificação dos pisos de cobertura, auditoria de dependências (pip-audit), busca de segredos (gitleaks) e validação estrita do openspec. O comando SHALL terminar com código diferente de zero se qualquer gate falhar.

#### Scenario: Repositório limpo
- **WHEN** `make check` roda num repositório sem violações
- **THEN** todos os gates passam e o comando termina com código zero

#### Scenario: Código fora do workspace também é verificado
- **WHEN** um arquivo em `e2e/` ou `scripts/` tem erro de tipo ou warning de lint
- **THEN** `make check` falha

#### Scenario: Um gate falha
- **WHEN** existe um arquivo fora da formatação, um erro de tipo ou um warning de lint
- **THEN** `make check` termina com código diferente de zero e indica qual gate falhou

### Requirement: Formatação tem um comando de correção
O projeto pai SHALL expor `make fmt`, que aplica ruff format e as correções automáticas do ruff em todos os pacotes. A formatação gerada SHALL ser a fonte de verdade.

#### Scenario: Corrigir formatação
- **WHEN** um arquivo está fora do padrão e `make fmt` roda
- **THEN** o arquivo é reescrito e o gate de formatação do `make check` passa

### Requirement: Pisos de cobertura onde a lógica vive
A cobertura SHALL ser medida com branch. O `make check` SHALL falhar se `grolla.domain` ficar abaixo de 90% de linha ou 80% de branch, ou se `grolla.application` ficar abaixo de 80% de linha ou 70% de branch. Os pisos SHALL NOT ser reduzidos sem pedido explícito do usuário.

#### Scenario: Cobertura abaixo do piso
- **WHEN** código novo em `grolla.domain` deixa a cobertura de linha em 85%
- **THEN** `make check` falha apontando o pacote e o piso violado

### Requirement: Testes unitários não acessam a rede
Os testes unitários SHALL rodar com acesso à rede bloqueado (pytest-socket). Testes que precisam de rede SHALL ser marcados como integração e rodar contra containers locais.

#### Scenario: Unitário tenta abrir conexão
- **WHEN** um teste unitário tenta abrir um socket
- **THEN** o teste falha com erro de rede bloqueada

### Requirement: Supply-chain verificada
O `make check` SHALL falhar se pip-audit encontrar vulnerabilidade conhecida numa dependência travada no lockfile do workspace (que inclui o `grolla-fakes` e as dependências de dev), ou se gitleaks encontrar um segredo no repositório.

#### Scenario: Segredo commitado
- **WHEN** um arquivo do repositório contém uma chave de API com formato reconhecido
- **THEN** `make check` falha no gate de segredos

### Requirement: Hooks de git reforçam os gates
O repositório SHALL versionar hooks em `hooks/`, ativados com `git config core.hooksPath hooks`. O pre-commit SHALL rodar formatação e lint; o pre-push SHALL rodar `make check` e abortar o push se falhar.

#### Scenario: Push com build vermelho
- **WHEN** um push é tentado com um teste falhando
- **THEN** o hook de pre-push aborta o push

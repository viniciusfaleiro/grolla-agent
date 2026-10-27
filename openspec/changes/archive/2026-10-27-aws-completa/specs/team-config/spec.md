## ADDED Requirements

### Requirement: Contas da nuvem
A seção `cloud` SHALL aceitar `accounts`, uma lista de `{name, role_arn, regions}`. Com `accounts`, SHALL valer a lista; sem ela, a conta das credenciais do ambiente, como hoje. `role_arn` SHALL ser um ARN de role do IAM, e o processo SHALL NOT subir com um ARN inválido.

#### Scenario: ARN inválido
- **WHEN** o YAML tem `cloud.accounts[0].role_arn: "admin"`
- **THEN** o processo não sobe e a mensagem aponta o campo

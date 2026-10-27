## ADDED Requirements

### Requirement: Configuração dos clusters
A seção opcional `clusters` SHALL ser uma lista de clusters consultados só para leitura, cada um com:
- `name`: o nome que aparece nas respostas (único);
- `enabled`: liga ou desliga o cluster na investigação (padrão `true`);
- `access`: `kubeconfig` (com `context` opcional), `in_cluster` ou `eks` (com `eks_name` e `region`, e o token gerado pela credencial da nuvem do ambiente);
- `role_arn`: opcional, só no `eks`: a role somente leitura da conta do cluster, assumida antes de gerar o token;
- `namespaces`: os namespaces consultados (ao menos um);
- `service_label`: o rótulo que liga um workload ao serviço (padrão `app.kubernetes.io/name`, depois `app`);
- `workload_map`: mapa opcional serviço → `[namespace/nome]` para as exceções;
- `show_values`: se valores de ConfigMap e de variáveis de ambiente podem aparecer (padrão `true`; `false` nos clusters PCI).

Sem a seção, ou com todos desligados, as ferramentas do cluster SHALL responder que a consulta não está configurada. Nenhuma credencial SHALL ir no YAML.

#### Scenario: EKS sem região
- **WHEN** o YAML tem um cluster com `access: eks` sem `region`
- **THEN** o processo não sobe e a mensagem aponta o campo

#### Scenario: Nomes repetidos
- **WHEN** dois clusters têm o mesmo `name`
- **THEN** o processo não sobe e a mensagem aponta o nome repetido

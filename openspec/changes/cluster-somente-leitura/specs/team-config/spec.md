## ADDED Requirements

### Requirement: Configuração do cluster
A seção opcional `cluster` SHALL configurar o acesso somente leitura ao cluster:
- `access`: `kubeconfig` (com `context` opcional), `in_cluster` ou `eks` (com `name` e `region`, e o token gerado pela credencial da nuvem do ambiente);
- `namespaces`: a lista de namespaces consultados (obrigatória, ao menos um);
- `service_label`: o rótulo que liga um workload ao serviço (padrão `app.kubernetes.io/name`, depois `app`);
- `workload_map`: mapa opcional serviço → `[namespace/nome]` para as exceções.

Sem a seção, as ferramentas do cluster SHALL responder que a consulta não está configurada. Nenhuma credencial SHALL ir no YAML.

#### Scenario: EKS sem região
- **WHEN** o YAML tem `cluster.access: eks` sem `region`
- **THEN** o processo não sobe e a mensagem aponta o campo

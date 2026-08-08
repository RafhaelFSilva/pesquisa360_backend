# Pesquisa360 — Regras de Multitenancy

**Status:** padrão obrigatório para Backend, Web e Mobile  
**Objetivo:** impedir vazamento de dados entre empresas/tenants.

## 1. Princípio central

Todo dado operacional pertence a uma empresa.

```text
Empresa / Company
  └── Usuários
  └── Projetos
        └── Pesquisas
              └── Perguntas
              └── Setores
              └── Coletas
                    └── Respostas
```

A empresa do usuário autenticado deve ser obtida exclusivamente por:

```text
JWT -> get_current_user -> current_user.company_id
```

## 2. Regra de ouro

**Nenhum cliente deve conseguir escolher o tenant no payload.**

Proibido em payloads operacionais:

```json
{
  "company_id": 1
}
```

Permitido apenas em rotas administrativas específicas de superadmin, quando existirem.

## 3. Backend

### Onde aplicar

| Recurso | Validação |
|---|---|
| Projeto | `Projeto.company_id == current_user.company_id` |
| Pesquisa | `Pesquisa -> Projeto.company_id == current_user.company_id` |
| Pergunta | `Pergunta -> Pesquisa -> Projeto.company_id == current_user.company_id` |
| Setor | `Setor -> Pesquisa -> Projeto.company_id == current_user.company_id` |
| Coleta | `Coleta -> Pesquisa -> Projeto.company_id == current_user.company_id` |
| Relatório | `Pesquisa -> Projeto.company_id == current_user.company_id` |

### Padrão de função

```python
def get_projeto(db, projeto_id, current_user):
    return (
        db.query(models.Projeto)
        .filter(
            models.Projeto.id == projeto_id,
            models.Projeto.company_id == current_user.company_id,
        )
        .first()
    )
```

Para pesquisa:

```python
def get_pesquisa(db, pesquisa_id, current_user):
    return (
        db.query(models.Pesquisa)
        .join(models.Projeto)
        .filter(
            models.Pesquisa.id == pesquisa_id,
            models.Projeto.company_id == current_user.company_id,
        )
        .first()
    )
```

### Resposta em acesso inválido

Preferir:

```http
404 Not Found
```

em vez de:

```http
403 Forbidden
```

Motivo: não revelar que o recurso existe em outro tenant.

## 4. Web

### Login

Fluxo obrigatório:

```text
POST /login/token
GET /usuarios/me/
authStore.setAuth(token, user)
```

Proibido:

```text
fallback user fake
company_id hardcoded
company_id: 1
```

### Criação de projeto

O Web não deve enviar `company_id`.

Correto:

```json
{
  "nome": "Projeto",
  "descricao": "Descrição"
}
```

Incorreto:

```json
{
  "nome": "Projeto",
  "company_id": 1
}
```

## 5. Mobile

### Login

Após login, o mobile deve chamar:

```http
GET /usuarios/me/
```

e armazenar localmente:

```json
{
  "id": 5,
  "email": "agente@pesquisa360.com",
  "nome": "Agente",
  "perfil_id": 3,
  "company_id": 1
}
```

### Coletas

- Não enviar `company_id`.
- Não confiar em `agente_id` local para autorização.
- Backend deve atribuir `agente_id=current_user.id`.

### Troca de usuário

Ao fazer logout ou trocar usuário:

1. remover token;
2. remover perfil local;
3. impedir upload de coletas do usuário anterior;
4. limpar banco local ou particionar por usuário/empresa.

## 6. Relatórios e monitoramento

Todo relatório e monitoramento deve validar:

```text
pesquisa_id -> projeto_id -> company_id
```

A finalidade territorial futura (`OPERACAO`, `RELATORIO`, `AMBOS`) nao altera a
origem do tenant: o backend continua derivando `company_id` de
`current_user.company_id`. Web e Mobile nao escolhem `company_id` ao criar,
editar, importar ou consultar setores.

Setores exclusivamente `RELATORIO` devem respeitar o mesmo isolamento por
tenant, mas nao devem aparecer em fluxos operacionais, Mobile ou monitoramento
operacional.

## 7. Testes obrigatórios Empresa A x Empresa B

| Cenário | Resultado esperado |
|---|---|
| Usuário A lista projetos | vê somente projetos da Empresa A |
| Usuário B lista projetos | vê somente projetos da Empresa B |
| Usuário B acessa projeto A por URL | 404 |
| Usuário B acessa pesquisa A por URL | 404 |
| Usuário B acessa relatório A por URL | 404 |
| Usuário B acessa monitoramento A | 404 |
| Agente A baixa missões | somente pesquisas da Empresa A |
| Agente A sincroniza coleta | coleta fica em Empresa A |
| Mobile troca de usuário | dados locais antigos não vazam |

## 8. Anti-patterns proibidos

```text
company_id vindo do frontend
agente_id hardcoded
usuário fake em authStore
query sem filtro por company_id
retornar 403 revelando recurso de outro tenant
usar dados locais do mobile para determinar tenant
```

## 9. Checklist para nova funcionalidade

- Qual entidade raiz determina o tenant?
- A rota valida `current_user.company_id`?
- O cliente envia `company_id`? Se sim, por quê?
- O endpoint retorna dados de outro tenant por acidente?
- Existe teste manual A vs B?
- A resposta em acesso inválido é 404?

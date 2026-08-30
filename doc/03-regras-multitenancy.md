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

## 1.1 ACL multiempresa (ADR-034)

Desde a ADR-034 a autorização deixou de ser `usuario.company_id`:

```
ANTES:   Usuario → Company → dados
DEPOIS:  Usuario → Acessos → Projeto → Company → dados
```

`usuarios.company_id` continua no banco e no contrato, mas significa apenas
**empresa principal/default**. Quem responde "pode ver este projeto?" é
`services/acessos.py`:

```python
Superadmin
OU (vínculo ATIVO com a Company do Projeto
    E (acesso_todos_projetos OU projeto explicitamente autorizado))
```

Em código, o que substituiu os filtros antigos:

| Antes | Agora |
|---|---|
| `Projeto.company_id == current_user.company_id` | `acessos.filtro_projeto_acessivel(current_user)` |
| `Coleta.company_id == current_user.company_id` | `acessos.filtro_company_acessivel(models.Coleta.company_id, current_user)` |
| `Usuario.company_id == current_user.company_id` | `acessos.filtro_usuario_visivel(current_user)` |
| `coleta.company_id = current_user.company_id` | `acessos.company_id_da_pesquisa(db, pesquisa_id)` |

Administração da ACL é exclusiva do Superadmin
(`GET/PUT /admin/usuarios/{id}/acessos`): um Gerente da Empresa A conceder
acesso à Empresa B seria escalação de privilégio.

## 1.2 Capacidade x escopo (ADR-037)

ACL responde *onde*; perfil responde *o quê*. As duas regras são independentes e
ambas precisam passar:

```python
acessos.filtro_projeto_acessivel(current_user)   # escopo  → 404 quando falha
rbac.require_permissao(Permissao.X)              # capacidade → 403 quando falha
```

Matriz em `core/rbac.py`. Papel derivado do NOME do perfil (nunca do id); perfil
desconhecido não recebe capacidade nenhuma.

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

## 6.1 Base Eleitoral (dado de referência)

A Base Eleitoral não segue a cadeia `Projeto -> company_id`: ela carrega o tenant
diretamente, e `NULL` tem significado próprio.

| Valor | Significado | Quem enxerga |
|---|---|---|
| `base_eleitoral.company_id IS NULL` | base oficial/global | todos os tenants |
| `base_eleitoral.company_id = N` | base privada do tenant N | somente o tenant N |

Expressão canônica de visibilidade:

```python
BaseEleitoral.company_id.is_(None) | (BaseEleitoral.company_id == current_user.company_id)
```

Proibido:

```text
SELECT * FROM base_eleitoral sem filtro de visibilidade
company_id vindo do payload do cliente
projeto de um tenant vinculado a base privada de outro tenant
```

O Projeto fixa a versão usada via `projeto_base_eleitoral`. A Pesquisa não se
vincula à base eleitoral. Acesso inválido retorna 404, como no restante do projeto.

**Contexto de Projeto (ADR-034).** Em `GET /projetos/{id}/base-eleitoral`,
`GET /projetos/{id}/bases-eleitorais-disponiveis` e
`POST /projetos/{id}/base-eleitoral/{base}/vincular`, a elegibilidade da Base é
comparada com **`Projeto.company_id`**, nunca com a empresa principal do
usuário: primeiro a ACL autoriza o Projeto, depois o tenant do Projeto define
as Bases (oficiais + privadas dele). Superadmin de outra empresa e Gerente com
ACL cruzada veem a mesma Base principal e as mesmas candidatas que o dono do
Projeto; Base privada de outro tenant continua 404 para todos.

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

## 6.2 Setor → Município de referência (ADR-035-B)

`setores.municipio_territorio_id` aponta para um MUNICIPIO da **Base principal
do Projeto**. Manual só é aceito se pertencer a essa Base (senão 404) e não
contradisser a geometria; Gerente principal da Empresa A com ACL no Projeto B
resolve pela Base B. `current_user.company_id` nunca participa.

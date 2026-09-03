# Pesquisa360 — Contratos de API

**Status:** contratos operacionais pós-validação  
**Objetivo:** documentar as rotas usadas pelo Web e Mobile para evitar regressões.

## 1. Convenções gerais

### Autenticação

Rotas privadas devem receber:

```http
Authorization: Bearer <access_token>
```

Token:

```http
POST /login/token
```

Renovacao da sessao (access expirado, refresh ainda valido):

```http
POST /login/refresh
```

O cliente NAO deve deslogar o usuario ao receber `401` numa rota comum: deve
renovar por `/login/refresh` e repetir a requisicao. Apenas o `401` do proprio
`/login/refresh` significa sessao encerrada.

### Tenant

O cliente **não deve** enviar `company_id` em fluxos operacionais. O backend deve derivar o tenant por:

```text
JWT -> current_user -> current_user.company_id
```

### Coordenadas

| Contexto | Padrão atual |
|---|---|
| Criação de coleta | `{ lat, lon }` em alguns schemas legados |
| Monitoramento/leitura | `{ lat, lng }` |
| Geofence/setores Web | `{ lat, lng }` |
| GeoJSON PostGIS | `[lng, lat]` |

Recomendação para novas rotas:

```json
{ "lat": 0.0349, "lng": -51.0694 }
```

Não quebrar rotas legadas sem migração.

## 1.1 Documentação automática (ADR-038)

| Ambiente (`APP_ENV`) | `/docs` | `/redoc` | `/openapi.json` |
|---|:--:|:--:|:--:|
| `development` (default, ou ausente) | 200 | 200 | 200 |
| `production` | **404** | **404** | **404** |

Em produção as rotas **não são registradas** — por isso 404, e não 401/403 nem
redirecionamento para login. A API operacional não depende da documentação
automática: as 125 rotas continuam registradas e respondendo.

Este documento (`02-contratos-api.md`) é a referência de contrato quando o
Swagger está desligado.

## 2. Autenticação

### POST `/login/token`

**Content-Type:** `application/x-www-form-urlencoded`

```text
username=gerente@pesquisa360.com
password=...
```

**Resposta:**

```json
{
  "access_token": "jwt",
  "refresh_token": "jwt",
  "token_type": "bearer",
  "expires_in": 28800
}
```

`refresh_token` e `expires_in` sao ADITIVOS: cliente anterior a FASE E.3 que le
apenas `access_token` continua funcionando.

### POST `/login/refresh`

**Content-Type:** `application/json`

```json
{
  "refresh_token": "jwt"
}
```

**Resposta:** identica a de `/login/token` (novo access e novo refresh).

**`401`** quando o refresh esta expirado, malformado, e na verdade um access
token, ou quando o usuario/empresa deixou de estar ativo. Nao existe `403`
neste fluxo -- `403` e autorizacao, e nunca deve fazer o cliente renovar.

### Tipos de token

| Claim `type` | Autentica rotas privadas | Renova sessao |
| ------------ | ------------------------ | ------------- |
| `access`     | sim                      | nao           |
| `refresh`    | nao                      | sim           |
| ausente (legado, emitido antes da E.3) | sim | nao |

TTLs configuraveis por `ACCESS_TOKEN_EXPIRE_MINUTES` e
`REFRESH_TOKEN_EXPIRE_DAYS`.

**Limitacao conhecida:** a estrategia e stateless. Renovar emite um refresh
novo, mas NAO revoga o anterior, que segue valido ate o proprio `exp`.

### GET `/usuarios/me/`

**Resposta esperada:**

```json
{
  "id": 2,
  "email": "gerente@pesquisa360.com",
  "nome": "Rafhael Silva",
  "perfil_id": 2,
  "ativo": true,
  "company_id": 1
}
```

## 3. Usuários e agentes

### GET `/usuarios/`

Lista usuários do tenant do usuário autenticado.

### GET `/usuarios/agentes/`

Lista usuários ativos com perfil de agente no tenant atual. Usado para seleção de responsável de setor.

### GET `/usuarios/me/modulos/`

Capacidades comerciais (módulos e funcionalidades) da empresa principal do usuário autenticado.

**Resposta esperada:**

```json
{
  "modulos": [
    {
      "chave": "inteligencia_eleitoral",
      "nome": "Inteligência Eleitoral",
      "funcionalidades": [
        {
          "chave": "potencial_crescimento",
          "nome": "Potencial de Crescimento"
        }
      ]
    }
  ]
}
```

**Características:**

- Consulta o catálogo `modulos`, resolutor aditivo de `modulo_entitlements` por Empresa/Projeto/Pesquisa
- Retorna somente módulos e features **ativas** (ativo=true no catálogo)
- Retorna somente capacidades **efetivamente licenciadas** (concessão explícita valida, data dentro de janela, empresa+tenant válida)
- Nunca aceita `company_id` do cliente; deriva do JWT
- Falha em error não impede `/usuarios/me/` — são endpoints independentes
- **Potencial de Crescimento** (`potencial_crescimento`) aparece apenas se **ativo=true** no catálogo. Versão inicial a traz **inativa** (`ativo=false`)

## 3.1 ACL multiempresa/multiprojeto (ADR-034)

Somente **Superadmin**. Concessão cross-tenant por um Gerente seria escalação de
privilégio.

### GET `/admin/usuarios/{usuario_id}/acessos`

```json
{
  "usuario_id": 12,
  "empresa_principal_id": 7,
  "empresas": [
    { "company_id": 7, "company_nome": "Empresa QA A", "acesso_todos_projetos": true,  "ativo": true,  "principal": true,  "projeto_ids": [] },
    { "company_id": 8, "company_nome": "Empresa QA B", "acesso_todos_projetos": false, "ativo": true,  "principal": false, "projeto_ids": [22] }
  ]
}
```

### PUT `/admin/usuarios/{usuario_id}/acessos`

Substitui a ACL inteira, em **uma transação** (`extra="forbid"`):

```json
{
  "empresa_principal_id": 7,
  "empresas": [
    { "company_id": 7, "acesso_todos_projetos": false, "projeto_ids": [14, 15] },
    { "company_id": 8, "acesso_todos_projetos": false, "projeto_ids": [22] }
  ]
}
```

Validado **antes** de qualquer escrita: empresa existente e ativa, projeto
existente e pertencente à empresa informada, sem duplicatas, empresa principal
entre as autorizadas. Qualquer item inválido → `422` e **nenhuma** alteração
parcial. Usuário inexistente → `404`.

`acesso_todos_projetos: true` ignora `projeto_ids` e alcança projetos futuros
daquela empresa, sem nova linha em `usuario_projeto_acessos`.

### GET `/usuarios/me/` (aditivo)

```json
{ "id": 12, "email": "...", "perfil_id": 1, "company_id": 7,
  "company_ids": [7, 8], "multiempresa": true }
```

`company_id` continua sendo a **empresa principal/default** — contrato antigo
intacto. `company_ids`/`multiempresa` são aditivos. A árvore de projetos **não**
vem aqui: para isso existe `GET /projetos/`.

### GET `/projetos/`

Passou de "projetos da empresa do usuário" para **projetos acessíveis**: pode
devolver projetos de empresas diferentes na mesma resposta. Projeto sem
autorização → ausente da lista e `404` no acesso direto.

## 3.2 Cadastro por convite e ativação de conta

Ciclo: **usuário autenticado cria o cadastro → conta nasce inativa → convite por
e-mail → o convidado define a primeira senha → conta ativa → login normal.**

### POST `/usuarios/` (autenticado — regra de perfil preservada)

```json
{ "nome": "João Silva", "email": "joao@cliente.com", "perfil_id": 4 }
```

- **Não é rota anônima** (já exigia `require_manager_or_superadmin`).
- `senha` é **opcional**: ausente → convite (conta `ativo=false` + e-mail);
  presente → comportamento anterior (conta criada já ativa).
- Tenant: para Gerente, sempre `current_user.company_id` — `company_id` no
  payload é **ignorado**. Superadmin continua declarando a empresa
  explicitamente (administração, não operação).
- E-mail duplicado → `400`, sem criar usuário e sem disparar convite.

Resposta da **criação** (`UsuarioCriadoResponse`) devolve o link **uma única
vez**, para o administrador repassar caso o e-mail caia no spam:

```json
{ "id": 123, "nome": "João Silva", "email": "joao@cliente.com", "ativo": false,
  "convite": { "activation_url": "https://<WEB_BASE_URL>/ativar-conta?token=…",
               "expira_em": "2026-08-28T19:00:00Z", "email_enviado": true } }
```

`convite` é `null` quando a criação veio com senha administrativa — não se
inventa link para um convite que não existe. `GET /usuarios/` e
`GET /admin/usuarios/` continuam respondendo `schemas.Usuario`, **sem** qualquer
campo de convite.

### GET `/usuarios/ativacao/validar?token=…` (público)

```json
{ "status": "VALIDO", "valido": true, "email": "joao@cliente.com",
  "nome": "João Silva", "expira_em": "2026-08-28T12:00:00Z" }
```

`status` ∈ `VALIDO | INVALIDO | EXPIRADO | UTILIZADO`. Nunca retorna
`senha_hash`, `token_hash`, ids internos ou dado de terceiros; `email`/`nome` só
aparecem com token válido — para quem já tem o link.

### POST `/usuarios/ativacao` (público)

```json
{ "token": "…", "senha": "abc123", "confirmacao_senha": "abc123" }
```

→ `200 {"ativado": true, "email": "…", "mensagem": "Conta ativada com sucesso. Sua senha foi definida."}`

- `confirmacao_senha` é opcional no contrato; quando enviada, o **Backend**
  confere (`422` se divergir).
- Senha fora da política → `422`. Token inválido/expirado/usado → `400`.
- Transação única: senha gravada, `ativo=true` e token consumido no mesmo
  commit. Falha → rollback, e o token **continua utilizável**.

### POST `/admin/usuarios/{usuario_id}/reenviar-ativacao` (Superadmin)

```json
{ "enviado": true, "email": "joao@cliente.com", "expira_em": "…",
  "activation_url": "https://<WEB_BASE_URL>/ativar-conta?token=…" }
```

Invalida o convite anterior ainda aberto. Conta já ativa → `400`; usuário
inexistente → `404`. `enviado` reflete a entrega do e-mail; `activation_url`
vem sempre, porque o convite existe mesmo com SMTP fora do ar.

**O link não é recuperável.** Ele embute o token puro e o banco guarda só o
SHA-256 — não existe (nem deve existir) rota do tipo
`GET /usuarios/{id}/link-ativacao`. Quem perdeu o link gera um convite novo, o
que invalida o anterior.

### Política de senha (`core/password_policy.py`)

Mínimo 6 caracteres, ao menos uma letra e ao menos um número.
`abc123`/`pesquisa9` aceitas; `123456`, `abcdef`, `a12` recusadas. Regra única e
reutilizável — a futura recuperação de senha usa a mesma função. Aplicada hoje
**na ativação**; os fluxos administrativos de senha seguem com a validação
anterior (ver pendências).

### Login

Conta com `ativo=false` **não autentica** (`401`, com a mesma mensagem genérica
já usada — não se revela o motivo).

## 3.3 Autorização por perfil (ADR-037)

Toda rota privada exige, nesta ordem: **autenticação → tenant → ACL do projeto →
capacidade do perfil**.

| Situação | Status |
|---|---|
| Outro tenant, ou projeto sem ACL | **404** (não revela existência) |
| Recurso visível, operação proibida ao perfil | **403** |

### Matriz efetiva

| Operação | Superadmin | Gerente | Coordenador | Supervisor | Cliente | Agente |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Ver projeto / pesquisa / território | ✔ | ✔ | ✔ | ✔ | ✔ | — |
| Criar projeto | ✔ | ✔ | — | — | — | — |
| Editar projeto | ✔ | ✔ | ✔ | — | — | — |
| Excluir projeto | ✔ | ✔ | — | — | — | — |
| Gerenciar pesquisa / perguntas / geofence | ✔ | ✔ | ✔ | — | — | — |
| Gerenciar setores, cotas e composição | ✔ | ✔ | ✔ | ✔ | — | — |
| Monitoramento e controle de campo | ✔ | ✔ | ✔ | ✔ | ✔ | — |
| Ver relatórios / inteligência / mapas | ✔ | ✔ | ✔ | ✔ | ✔ | — |
| Configurar relatórios executivos | ✔ | ✔ | ✔ | — | — | — |
| Gerenciar lideranças | ✔ | ✔ | ✔ | — | — | — |
| Vincular/validar Base Eleitoral | ✔ | ✔ | ✔ | — | — | — |
| Gerenciar usuários | ✔ | ✔ | — | — | — | — |
| Gerenciar empresas | ✔ | — | — | — | — | — |
| Missão e sincronização (app) | ✔ | ✔ | — | — | — | ✔ |
| Enviar coleta / tentativa / upload | ✔ | ✔ | ✔ | ✔ | — | ✔ |

**Cliente é read-only:** nenhuma capacidade de escrita, em nenhum módulo.
**Agente** só tem missão e sincronização — o painel Web inteiro responde 403.

### GET `/usuarios/me/` (aditivo)

```json
{ "id": 12, "perfil_id": 2, "perfil_nome": "Gerente", "company_id": 7,
  "company_ids": [7], "multiempresa": false,
  "papel": "GERENTE", "permissions": ["PROJETO_VER", "PROJETO_CRIAR", "..."] }
```

O Web deriva a interface de `permissions`. Duplicar a matriz no React criaria
duas verdades sobre a mesma regra — a autorização continua no servidor.

## 4. Projetos

### GET `/projetos/`

Lista projetos do tenant atual.

### POST `/projetos/`

Cria projeto. O backend injeta `company_id`.

```json
{
  "nome": "Eleição 2026",
  "descricao": "Pesquisa de intenção de voto"
}
```

### GET `/projetos/{projeto_id}`

Retorna projeto com pesquisas.

### PATCH `/projetos/{projeto_id}`

Atualiza projeto.

```json
{
  "nome": "Novo nome",
  "descricao": "Nova descrição",
  "status": "Em Campo"
}
```

### DELETE `/projetos/{projeto_id}`

Soft delete do projeto.

## 5. Pesquisas

### POST `/projetos/{projeto_id}/pesquisas/`

Cria pesquisa no projeto.

```json
{
  "titulo": "Intenção de Votos, 1º Turno",
  "tipo_pesquisa": "Quantitativa"
}
```

### PATCH `/projetos/{projeto_id}/pesquisas/{pesquisa_id}`

Atualiza pesquisa.

### DELETE `/projetos/{projeto_id}/pesquisas/{pesquisa_id}`

Soft delete da pesquisa.

## 6. Perguntas

### GET `/pesquisas/{pesquisa_id}/perguntas/`

Lista perguntas da pesquisa.

### POST `/projetos/{projeto_id}/pesquisas/{pesquisa_id}/perguntas/`

Cria pergunta.

```json
{
  "texto_pergunta": "Em quem você votaria?",
  "tipo_pergunta": "escolha_simples",
  "ordem": 1,
  "eh_obrigatoria": true,
  "opcoes": [
    { "texto": "Candidato A", "ordem": 1 },
    { "texto": "Candidato B", "ordem": 2 }
  ]
}
```

### DELETE `/pesquisas/{pesquisa_id}/perguntas/{pergunta_id}`

Soft delete da pergunta. Se houver respostas vinculadas, o backend pode negar exclusão.

## 7. Geofence

### GET `/projetos/{projeto_id}/pesquisas/{pesquisa_id}/geofence`

Retorna cerca global.

### PATCH `/projetos/{projeto_id}/pesquisas/{pesquisa_id}/geofence`

Atualiza cerca global.

```json
{
  "cerca_eletronica": [
    { "lat": 0.0349, "lng": -51.0694 },
    { "lat": 0.0349, "lng": -51.0600 },
    { "lat": 0.0300, "lng": -51.0600 }
  ],
  "tolerancia_metros": 50
}
```

## 8. Setores

### GET `/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores`

Lista setores.

**Atenção:** o contrato atual pode retornar `geometria` GeoJSON ou `poligono`, dependendo do endpoint/ajuste. O frontend deve tolerar ambos enquanto o contrato não for consolidado.

**Estado atual:** setores existentes sao tratados como setores operacionais.
Ainda nao existe campo de finalidade no contrato HTTP. A separacao futura entre
`OPERACAO`, `RELATORIO` e `AMBOS` esta aprovada em ADR, mas nao deve ser
assumida por Web/Mobile ate implementacao e versionamento do contrato.

### POST `/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores`

Cria setor.

```json
{
  "nome": "001. Marabaixo",
  "meta": 100,
  "tolerancia_metros": 50,
  "agente_id": 4,
  "poligono": [
    { "lat": 0.0467, "lng": -51.1372 },
    { "lat": 0.0465, "lng": -51.1304 },
    { "lat": 0.0385, "lng": -51.1309 }
  ]
}
```

### DELETE `/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores/{setor_id}`

Remove setor.

**Status atual:** delete físico.  
**Recomendação futura:** avaliar soft delete para preservar histórico operacional.

### PATCH `/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores/{setor_id}`

Atualiza parcialmente um setor existente sem alterar seu ID. Exige perfil
Gerente ou Superadmin e valida projeto, pesquisa, setor e agente no tenant do
usuário autenticado.

Campos aceitos:

```json
{
  "nome": "Setor Centro atualizado",
  "meta": 120,
  "tolerancia_metros": 75,
  "agente_id": 4,
  "poligono": [
    { "lat": 0.0467, "lng": -51.1372 },
    { "lat": 0.0465, "lng": -51.1304 },
    { "lat": 0.0385, "lng": -51.1309 }
  ]
}
```

Todos os campos são opcionais. Quando o polígono não é enviado, a geometria
existente é preservada. Quando enviado, deve ser um `Polygon` válido em
EPSG:4326 com pelo menos três pontos distintos. Recursos fora do tenant são
respondidos como `404`.

### Importacao administrativa por terminal

A importacao de Shapefile por terminal nao e um endpoint novo e nao altera os
contratos HTTP acima. O CLI reutiliza internamente `schemas.SetorCreate` e
`crud.create_setor`. Os nomes persistidos no modelo real sao `meta`,
`tolerancia` e `geometria`; os termos amigaveis da interface sao mapeados para
esses campos. Consulte `11-importacao-setores-shapefile.md`.

Roadmap aprovado: reutilizar a importacao interativa para permitir escolha de
finalidade territorial. Setores `RELATORIO` nao exigirao agente, meta/cota ou
tolerancia operacional. Essa regra ainda nao altera o contrato atual.

## 9. Coletas

### POST `/pesquisas/{pesquisa_id}/coletas/`

Endpoint usado pelo mobile para sincronizar coleta.

```json
{
  "client_uuid": "27f7d4a3-5477-4ac3-8df1-4b73bd702270",
  "data_inicio_coleta": "2026-05-03T09:00:00",
  "data_fim_coleta": "2026-05-03T09:04:00",
  "localizacao_inicio": { "lat": 0.0405, "lon": -51.1352 },
  "localizacao_fim": { "lat": 0.0406, "lon": -51.1351 },
  "foi_offline": true,
  "respostas": [
    { "pergunta_id": 13, "valor_resposta": "Candidato A" }
  ]
}
```

**Regras:**

- `pesquisa_id` vem da URL.
- `client_uuid` é um UUID obrigatório, gerado e persistido uma única vez pelo mobile.
- Retries reutilizam o mesmo `client_uuid`; dentro da empresa, o backend retorna a coleta já criada sem duplicar respostas.
- `agente_id` deve ser `current_user.id`.
- `company_id` não vem do payload.
- `localizacao_inicio` e `localizacao_fim` podem ser nulas.
- Backend calcula `inconformidade_localizacao`.
- Backend pode preencher `endereco_estimado`.

## 8.1 Cota territorial por setor (PROMPT 04)

Cada setor da missão (`GET /agente/missao/{pesquisa_id}`, e o mesmo dicionário
de progresso usado por `GET .../setores`) traz, de forma **aditiva**, o
snapshot oficial da cota territorial:

```json
{
  "id": 10,
  "nome": "Setor 10",
  "meta": 100,
  "realizado": 92,
  "restante": 8,
  "excedente": 0,
  "percentual_atingimento": 92.0,
  "cota_atingida": false,
  "status_cota": "ATENCAO",
  "limite_atencao_realizado": 90,
  "snapshot_ate_coleta_id": 5821,
  "snapshot_em": "2026-08-27T13:30:00+00:00",
  "agentes_atribuidos_total": 2
}
```

| Campo | Semântica |
|---|---|
| `realizado` | coletas do setor contadas pelo servidor (`COUNT`) |
| `snapshot_ate_coleta_id` | `MAX(coletas.id)` do setor na **mesma** consulta do `COUNT`; o aparelho só soma coletas locais pendentes ou com `server_id` maior |
| `snapshot_em` | horário do servidor em que o snapshot foi calculado (auditoria) |
| `status_cota` | `SEM_COTA` (meta ≤ 0) · `ABERTO` · `ATENCAO` (`realizado ≥ limite_atencao_realizado`) · `ENCERRADO` (`realizado ≥ meta`) |
| `limite_atencao_realizado` | `ceil(meta × 0,90)`; `null` sem meta — regra única, calculada só no Backend |
| `agentes_atribuidos_total` | agentes **vinculados** ao setor (N:N ativo ∪ `agente_id` legado); não significa "atuando agora" |

Regras:

- **Cota territorial é bloqueante apenas no início de uma NOVA abordagem** no
  aplicativo. `POST /pesquisas/{id}/coletas/` **não** rejeita uma coleta
  legítima por cota cheia: dois aparelhos offline com snapshot 99/100 podem
  produzir 101/100, e ambos são aceitos (`excedente = 1`). O próximo snapshot
  devolve `ENCERRADO` e o aplicativo passa a bloquear.
- Tentativas de campo nunca entram em `realizado`.
- Cota de perfil (sexo × idade) e cobertura territorial **ainda não existem**.

## 8.2 Cotas de perfil amostral (PROMPT 05)

Domínio **orientativo**: nunca bloqueia abordagem, entrevista ou sincronização
(a cota territorial do setor, §8.1, é a bloqueante). A célula
`município × sexo × faixa etária` é a verdade planejada; marginais são derivadas.

### PUT `/projetos/{projeto_id}/pesquisas/{pesquisa_id}/cotas-perfil` (Gerente/Superadmin)

Substitui o plano inteiro de forma transacional (falha em qualquer célula deixa o
plano anterior intacto). `GET` na mesma rota devolve o plano;
`GET .../cotas-perfil/progresso` devolve o snapshot completo com números
(gestão/auditoria, futuro painel Web).

```json
{
  "pergunta_sexo_id": 11,
  "pergunta_idade_id": 12,
  "modo_idade": "NUMERICA",
  "sexo_valores": { "MASCULINO": ["Masculino"], "FEMININO": ["Feminino"] },
  "territorios": [
    { "territorio_id": 900, "cotas": [
      { "sexo": "FEMININO",  "faixa_etaria": "16-24", "idade_min": 16, "idade_max": 24,   "meta": 73 },
      { "sexo": "MASCULINO", "faixa_etaria": "16-24", "idade_min": 16, "idade_max": 24,   "meta": 67 },
      { "sexo": "FEMININO",  "faixa_etaria": "25-34", "idade_min": 25, "idade_max": 34,   "meta": 101 },
      { "sexo": "MASCULINO", "faixa_etaria": "25-34", "idade_min": 25, "idade_max": 34,   "meta": 93 },
      { "sexo": "FEMININO",  "faixa_etaria": "35-44", "idade_min": 35, "idade_max": 44,   "meta": 93 },
      { "sexo": "MASCULINO", "faixa_etaria": "35-44", "idade_min": 35, "idade_max": 44,   "meta": 85 },
      { "sexo": "FEMININO",  "faixa_etaria": "45-59", "idade_min": 45, "idade_max": 59,   "meta": 85 },
      { "sexo": "MASCULINO", "faixa_etaria": "45-59", "idade_min": 45, "idade_max": 59,   "meta": 78 },
      { "sexo": "FEMININO",  "faixa_etaria": "60+",   "idade_min": 60, "idade_max": null, "meta": 51 },
      { "sexo": "MASCULINO", "faixa_etaria": "60+",   "idade_min": 60, "idade_max": null, "meta": 49 }
    ] }
  ]
}
```

Regras: perguntas precisam pertencer à pesquisa (422); `territorio_id` é um
MUNICÍPIO da Base Eleitoral principal do projeto (404 fora dela / outro
tenant); `meta ≥ 0`; `idade_min ≤ idade_max`; faixas do mesmo sexo/território
não se sobrepõem; `modo_idade` `NUMERICA` (classifica pelo inteiro) ou
`CATEGORICA` (`idade_valores` com os textos reais). Nenhuma pergunta/valor é
fixado em código. Sem meta municipal formal, o `diagnostico` traz apenas
`total_cotas_perfil` por município.

### Classificação de uma coleta

`coleta.setor_id` → município do setor (mesma resolução da FASE F) → resposta
da pergunta de sexo (mapa `sexo_valores`, sem acento/caixa) → resposta da
pergunta de idade → única célula. Faltando qualquer etapa a coleta é
`NAO_CLASSIFICADA` (motivos: `SEM_SETOR`, `SEM_MUNICIPIO`, `SEM_SEXO`,
`SEXO_DESCONHECIDO`, `SEM_IDADE`, `IDADE_INVALIDA`, `SEM_CELULA`) e não conta em
célula alguma. Tentativas de campo nunca contam.

### Motor de prioridade (Backend, único)

```text
percentual_territorio = realizado_total / meta_total × 100   (soma das células do município)
percentual_celula     = realizado / meta × 100
desvio_pp             = percentual_celula − percentual_territorio
EQUILIBRADO: desvio ≥ −5 · BAIXO: −10 ≤ desvio < −5 · MEDIO: −20 ≤ desvio < −10 · ALTO: desvio < −20
```
Prioridades = células com desvio < 0 e nível BAIXO/MEDIO/ALTO, excluindo
células completas (`realizado ≥ meta`), ordenadas pelo desvio mais negativo.
Enquanto o município tem menos de 10% da meta (`FRACAO_MINIMA_PARA_PRIORIDADE`)
o status é `FASE_INICIAL` e a lista é vazia.

### Na missão (`GET /agente/missao/{pesquisa_id}`, aditivo)

```json
{
  "plano_cota_perfil_ativo": true,
  "prioridades_perfil": [
    { "territorio_id": 900, "territorio_nome": "Macapa", "sexo": "MASCULINO", "sexo_rotulo": "Homem",
      "faixa_etaria": "60+", "meta": 49, "realizado": 28, "restante": 21,
      "percentual_atingimento": 57.14, "percentual_territorio": 80.0, "desvio_pp": -22.86, "prioridade": "ALTO" }
  ],
  "perfil_status_territorios": { "900": "PRIORIDADES" },
  "prioridades_perfil_snapshot_em": "2026-08-27T13:30:00+00:00",
  "setores": [ { "id": 500, "municipio": { "id": 900, "nome": "Macapa" }, "...": "..." } ]
}
```
Recortado aos municípios dos setores do agente. Os números existem para
auditoria/Web; **o app do agente não exibe quantidades** — só
"Homem · 60+ / Déficit alto". Atualiza a cada sincronização (sem polling).

## 8.3 Cobertura territorial de campo (PROMPT 06)

**Atividade de campo conhecida** — eventos georreferenciados já registrados.
Não é tracking: o servidor não sabe onde os agentes estão agora nem o trajeto
percorrido. Orientativo; nada aqui bloqueia. Só a cota territorial
`ENCERRADO` (§8.1) bloqueia nova abordagem.

### GET `/agente/pesquisas/{pesquisa_id}/cobertura-campo/`

Eventos dos setores atribuídos ao agente (mesma regra de `/agente/missao`).
Pesquisa de outro tenant → `404`. Pesquisa sem setores → `setor_ids: []`,
`eventos: []`.

```json
{
  "pesquisa_id": 1000,
  "snapshot_em": "2026-08-27T14:00:00+00:00",
  "distancia_recomendada_entre_abordagens_metros": 100,
  "distancia_configurada": false,
  "setor_ids": [20],
  "eventos": [
    { "tipo": "COLETA",    "server_id": 501, "setor_id": 20, "lat": 0.0349, "lng": -51.0694, "accuracy": null, "ocorrido_em": "2026-08-27T12:00:00Z", "resultado": null },
    { "tipo": "TENTATIVA", "server_id": 81,  "setor_id": 20, "lat": 0.0354, "lng": -51.0700, "accuracy": 10.2, "ocorrido_em": "2026-08-27T12:03:00Z", "resultado": "RECUSA" }
  ]
}
```

| Campo | Origem | Semântica |
|---|---|---|
| `tipo` COLETA | `coletas.localizacao_inicio` (ponto da abordagem) com `setor_id` | entrevista concluída |
| `tipo` TENTATIVA | `tentativas_campo` com `resultado ≠ EM_ANDAMENTO` | abordagem registrada (`resultado` diferencia RECUSA, NAO_ELEGIVEL, DESISTENCIA, INCOMPLETA, PROBLEMA_TECNICO, OUTRO, CONCLUIDA sem vínculo) |
| `server_id` | id do registro | chave lógica `tipo + server_id` para deduplicação no app |
| `accuracy`, `ocorrido_em` | GPS/horário do evento | sem relógio do aparelho |

**Deduplicação**: `TentativaCampo CONCLUIDA` com `coleta_id` **não** é emitida
— a `Coleta` já representa a entrevista (1 ponto). Coordenada ausente/inválida
nunca gera evento. `PROBLEMA_TECNICO`/`OUTRO`/`INCOMPLETA` aparecem como
TENTATIVA (presença física comprovada) e nunca contam cota ou perfil.
Sem dados pessoais: nenhum nome, resposta, endereço, `client_uuid` ou
identificação do agente.

### Distância recomendada entre abordagens

Parâmetro operacional da pesquisa (não é a tolerância de geofence), em
`configuracoes_campo_pesquisa` (migration `b9c0d1e2f3a4`). Default único
`DISTANCIA_RECOMENDADA_PADRAO_METROS = 100` no serviço quando não configurado.

- `GET/PUT /projetos/{projeto_id}/pesquisas/{pesquisa_id}/configuracao-campo`
  (Gerente/Superadmin): `{"distancia_recomendada_entre_abordagens_metros": 150}`;
  `null` limpa; `≤ 0` ou `> 5000` → `422`; outro tenant → `404`.

## 8.4 Painel de Controle de Campo — Web (PROMPT 07)

Visão gerencial consolidada da pesquisa (supervisão/coordenação). **Snapshot do
servidor** — a UI diz "Dados conhecidos até" e atualiza sob demanda; nada aqui
é tempo real. O endpoint **não recalcula** nenhum motor: reaproveita
`crud.obter_progressos_setores` (§8.1), `services.cota_perfil` (§8.2) e
`services.cobertura_campo` (§8.3) e apenas agrega/filtra.

### GET `/projetos/{projeto_id}/pesquisas/{pesquisa_id}/controle-campo`

Usuário autenticado do tenant (mesma autorização de monitoramento/relatórios).
Pesquisa de outro tenant ou `projeto_id` divergente → `404`. Os endpoints
`/agente/...` não foram alterados: o agente continua recortado aos setores
dele e nunca recebe identificação de outros agentes (CB-B11 / PW-B09).

Query string (todos opcionais): `municipio_id`, `setor_ids` (csv), `agente_ids`
(csv), `data_inicio`, `data_fim` (ISO), `resultado`. Setor fora da pesquisa ou
agente fora do tenant → `404`; csv não numérico, `resultado` inválido,
`municipio_id ≤ 0` ou `data_fim < data_inicio` → `422`.

| Bloco | município | setor | agente | período | resultado |
|---|---|---|---|---|---|
| `resumo`, `tentativas_por_resultado` | sim | sim | sim | sim | — |
| `atividade_campo.eventos` (mapa) | sim | sim | sim | sim | sim (só TENTATIVA) |
| `setores` / `resumo_territorial` (cota oficial) | sim | sim | **não** | **não** | — |
| `cotas_perfil` (motor oficial) | sim (territórios) | via município | **não** | **não** | — |

`filtros.nota` repete essa regra no payload. `opcoes` (municípios, setores,
agentes vinculados) é sempre completa, independente dos filtros.

```json
{
  "pesquisa_id": 1000, "snapshot_em": "2026-08-27T14:00:00+00:00",
  "filtros": { "municipio_id": null, "setor_ids": [], "agente_ids": [], "data_inicio": null, "data_fim": null, "resultado": null, "nota": "..." },
  "opcoes": { "municipios": [{"id": 900, "nome": "Macapa"}], "setores": [{"id": 500, "nome": "Centro"}], "agentes": [{"id": 2, "nome": "Agente Um"}] },
  "resumo": { "meta_territorial": 20, "realizado_territorial": 3, "entrevistas_concluidas": 3, "coletas_com_tentativa": 2, "coletas_sem_tentativa": 1,
              "tentativas_encerradas": 4, "tentativas_em_andamento": 0, "tentativas_concluidas": 2, "recusas": 2, "nao_elegiveis": 0, "desistencias": 0,
              "incompletas": 0, "problemas_tecnicos": 0, "outros": 0, "taxa_conclusao_tentativas": 50.0, "nota_taxa": "..." },
  "resumo_territorial": { "abertos": 2, "atencao": 0, "encerrados": 0, "sem_cota": 0, "com_excedente": 0 },
  "alertas": [{ "tipo": "SETORES_ATENCAO", "total": 1, "mensagem": "1 setor(es) próximo(s) da meta." }],
  "setores": [{ "setor_id": 500, "setor_nome": "Centro", "finalidade": "OPERACAO", "municipio_id": 900, "municipio_nome": "Macapa", "meta": 10, "realizado": 3,
                "restante": 7, "excedente": 0, "percentual_atingimento": 30.0, "status_cota": "ABERTO", "limite_atencao_realizado": 9, "agentes_atribuidos_total": 1, "snapshot_ate_coleta_id": 77 }],
  "cotas_perfil": { "plano_ativo": true, "territorios": [{ "territorio_id": 900, "territorio_nome": "Macapa", "meta_total": 20, "realizado_total": 1, "percentual_territorio": 5.0,
                    "fase_inicial": true, "status": "FASE_INICIAL", "celulas": [ { "sexo": "FEMININO", "faixa_etaria": "16-24", "meta": 5, "realizado": 1, "restante": 4, "percentual_atingimento": 20.0, "desvio_pp": 15.0, "prioridade": "EQUILIBRADO", "...": "..." } ] }],
                    "nao_classificadas": 2, "motivos_nao_classificadas": { "SEM_IDADE": 1, "SEM_SEXO": 1 }, "snapshot_em": "2026-08-27T14:00:00+00:00" },
  "tentativas_por_resultado": [{ "resultado": "RECUSA", "total": 2 }, { "resultado": "CONCLUIDA", "total": 2 }],
  "atividade_campo": { "distancia_recomendada_entre_abordagens_metros": 100, "distancia_configurada": false,
                       "eventos": [{ "tipo": "COLETA", "server_id": 77, "setor_id": 500, "lat": 0.0349, "lng": -51.0694, "accuracy": null, "ocorrido_em": "2026-08-27T12:00:00Z", "resultado": null, "agente_id": 2, "agente_nome": "Agente Um" }] }
}
```

Semântica:

- `taxa_conclusao_tentativas = tentativas CONCLUIDA / tentativas encerradas`
  (percentual, `null` sem abordagens). Coletas legadas sem `TentativaCampo`
  entram em `entrevistas_concluidas` e em `coletas_sem_tentativa`, nunca na taxa.
- `setores` ordenados ATENCAO > ABERTO > ENCERRADO > SEM_COTA, depois % desc.
  `resumo.meta_territorial`/`realizado_territorial` somam os setores em escopo.
- `cotas_perfil.territorios[].celulas` traz os números completos (meta,
  realizado, restante, %, `desvio_pp`, prioridade), ordenadas ALTO > MEDIO >
  BAIXO > EQUILIBRADO; `status: FASE_INICIAL` quando abaixo de 10% da meta.
  Pesquisa sem plano → `plano_ativo: false`, listas vazias.
- `atividade_campo.eventos`: mesma deduplicação da §8.3 (Coleta + tentativa
  CONCLUIDA vinculada = 1 evento), acrescida de `agente_id`/`agente_nome`
  (coordenador do mesmo tenant). Nunca respostas, endereço, `client_uuid`
  ou dados do entrevistado.
- `alertas` são derivados dos blocos acima (SETORES_ATENCAO,
  SETORES_EXCEDENTE, PERFIL_NAO_CLASSIFICADAS); não há motor próprio.
- Número de consultas constante em relação a setores/coletas (PW-B20).

### GET `/projetos/{projeto_id}/pesquisas/{pesquisa_id}/cobertura-campo`

Só a camada espacial do painel (mesmos filtros): `pesquisa_id`, `snapshot_em`,
`setores`, `distancia_recomendada_entre_abordagens_metros`,
`distancia_configurada`, `eventos` (com agente).

Web: rota `/projetos/:projectId/pesquisas/:surveyId/controle-campo`
(`FieldControlPage`, `fieldControlService.getPainel`), link "Controle de Campo"
no card da pesquisa em `ProjectDetailPage`. Testes: `tests/test_controle_campo.py`
(PW-B01..B20) e `tests/fieldControl.test.mjs` (PW-W01..W20).

## 9.1 Tentativas de campo

### POST `/pesquisas/{pesquisa_id}/tentativas-campo/`

Registra uma **abordagem operacional** do agente (PROMPT 03). Uma tentativa
não é uma coleta: recusa, não elegível, desistência, incompleta, problema
técnico e outros encerramentos vivem aqui e **nunca criam `Coleta`**. Só a
abordagem que vira entrevista concluída aponta para uma coleta.

```json
{
  "client_uuid": "6b3c1e5a-2f7d-4c3b-9a1e-0d2f8a7b6c5d",
  "setor_id": 123,
  "iniciada_em": "2026-08-27T10:00:00-03:00",
  "encerrada_em": "2026-08-27T10:01:00-03:00",
  "localizacao": {
    "lat": 0.0349,
    "lng": -51.0694,
    "accuracy": 8.5,
    "capturada_em": "2026-08-27T10:00:00-03:00"
  },
  "resultado": "RECUSA",
  "motivo": "NAO_QUIS_PARTICIPAR",
  "observacao": null,
  "coleta_client_uuid": null
}
```

Resposta `201`:

```json
{
  "id": 1,
  "client_uuid": "6b3c1e5a-2f7d-4c3b-9a1e-0d2f8a7b6c5d",
  "pesquisa_id": 100,
  "setor_id": 123,
  "agente_id": 7,
  "iniciada_em": "2026-08-27T13:00:00Z",
  "encerrada_em": "2026-08-27T13:01:00Z",
  "resultado": "RECUSA",
  "motivo": "NAO_QUIS_PARTICIPAR",
  "coleta_id": null
}
```

**Semântica de `resultado`** (classificação principal):

| Valor | Significado | Cria coleta? |
|---|---|---|
| `RECUSA` | pessoa abordada não quis participar | não |
| `NAO_ELEGIVEL` | pessoa fora do público (menor, não residente…) | não |
| `DESISTENCIA` | entrevista começou e o entrevistado desistiu | não |
| `INCOMPLETA` | entrevista interrompida por outro motivo operacional | não |
| `PROBLEMA_TECNICO` | aparelho/aplicativo impediu a entrevista | não |
| `OUTRO` | outro encerramento | não |
| `CONCLUIDA` | entrevista concluída; `coleta_client_uuid` aponta para a coleta | vínculo com coleta existente |

`EM_ANDAMENTO` existe apenas no aplicativo e é rejeitado pelo servidor (`422`).
`motivo` é um **código** (`[A-Z0-9_]`, ex.: `NAO_QUIS_PARTICIPAR`, `MENOR_DE_IDADE`,
`APARELHO_SEM_BATERIA`); texto livre vai em `observacao`. Nenhum dado pessoal
da pessoa recusante é aceito ou persistido.

**Regras:**

- `pesquisa_id` vem da URL e é validado no tenant (`404` fora dele).
- `agente_id` e `company_id` **não** vêm do payload: chaves extras são ignoradas
  e os valores são sempre os do token.
- `setor_id` é opcional (pesquisa sem setores); se informado, precisa pertencer
  à pesquisa e ao tenant (`404`) e o agente precisa estar atribuído (`403`).
- `localizacao` é obrigatória; `lat/lng` fora da faixa ou não numéricos → `422`.
  O servidor nunca cria `0,0`.
- `encerrada_em` é obrigatória e não pode ser anterior a `iniciada_em`.
- `coleta_client_uuid` só é aceito em `CONCLUIDA`/`DESISTENCIA`/`INCOMPLETA` e a
  coleta precisa **já estar sincronizada**, na mesma pesquisa e do mesmo agente;
  caso contrário `422` — o aplicativo mantém a tentativa pendente e reenvia
  após a coleta.
- **Idempotência**: `unique(company_id, client_uuid)`. Reenvio do mesmo
  `client_uuid` devolve o registro original (`201`, sem alterar nada); o mesmo
  `client_uuid` por outro agente do tenant → `409`.
- Tentativas **não** alteram `meta`, `realizado` nem `restante` do setor.

## 10. Monitoramento

### GET `/pesquisas/{pesquisa_id}/coletas/monitoramento/`

Retorna coletas para mapa e tabela.

```json
[
  {
    "id": 533,
    "pesquisa_id": 4,
    "agente_id": 5,
    "agente_nome": "Rafhael Ferreira",
    "data_inicio_coleta": "2026-05-03T17:38:01+00:00",
    "data_fim_coleta": "2026-05-03T17:56:22+00:00",
    "localizacao_inicio": { "lat": 0.0385, "lng": -51.1333 },
    "localizacao_fim": { "lat": 0.0384, "lng": -51.1330 },
    "inconformidade_localizacao": false,
    "status_sincronizacao": "sincronizado",
    "foi_offline": false,
    "endereco_estimado": "Avenida 6, Marabaixo II, Macapá..."
  }
]
```

Semântica visual:

- `foi_offline=false`: marcador azul, coleta online.
- `foi_offline=true`: marcador vermelho, coleta offline.
- `inconformidade_localizacao=true`: indicador separado em roadmap futuro.

## 11. Relatórios

### GET `/relatorios/pesquisas/{pesquisa_id}/simples/`

Alias validado:

```http
GET /pesquisas/{pesquisa_id}/simples/
```

### POST `/relatorios/pesquisas/{pesquisa_id}/crosstab/`

Alias validado:

```http
POST /pesquisas/{pesquisa_id}/crosstab/
```

Payload:

```json
{
  "pergunta_linha_id": 13,
  "pergunta_coluna_id": 14
}
```

Resposta:

```json
{
  "pergunta_linha": "Em quem você votaria para Governador?",
  "pergunta_coluna": "Em quem você votaria para Presidente?",
  "dados": [
    {
      "valor_linha": "Candidato A",
      "celulas": [
        {
          "valor_coluna": "Candidato A",
          "contagem": 20,
          "percentual": 46.51
        }
      ]
    }
  ]
}
```

## 12. Regras para novas rotas

1. Preferir rotas aninhadas quando houver validação de cadeia.
2. Validar tenant antes de operar.
3. Não retornar ORM cru.
4. Não retornar `WKBElement`.
5. Não confiar em IDs sensíveis vindos do payload.
6. Manter nomenclatura estável:
   - Web: `projectId`, `surveyId`
   - Backend: `projeto_id`, `pesquisa_id`

## 13. Cruzamentos Estratégicos — contrato multidimensional

### POST `/relatorios/pesquisas/{pesquisa_id}/cruzamentos-multidimensionais/`

Alias curto preservado:

```http
POST /pesquisas/{pesquisa_id}/cruzamentos-multidimensionais/
```

Payload completo:

```json
{
  "pergunta_ids": [46, 42, 43],
  "incluir_sem_resposta": true,
  "profundidade_maxima": 3,
  "filtros_respostas": [
    {
      "pergunta_id": 46,
      "valores": ["Dr. Furlan", "Clécio Luís", "__SEM_RESPOSTA__"]
    },
    {
      "pergunta_id": 42,
      "valores": ["Feminino"]
    }
  ]
}
```

Regras do request:

- `pergunta_ids` exige pelo menos duas perguntas únicas; a ordem define a
  hierarquia `A -> B -> C -> ... -> N`;
- `profundidade_maxima` é opcional, deve ser positiva e não pode superar o
  número de perguntas;
- `filtros_respostas` é opcional; cada pergunta aparece no máximo uma vez,
  deve pertencer a `pergunta_ids` e precisa conter ao menos um valor único e
  não vazio;
- filtros só podem usar dimensões processadas pela profundidade solicitada;
- valores desconhecidos retornam `422`;
- `incluir_sem_resposta` permanece no contrato por compatibilidade e aceita
  `false`; o Web de Cruzamentos Estratégicos sempre envia `true`;
- `company_id` e `projeto_id` não são aceitos no corpo.

A resposta contém `total_entrevistas`, `base_valida`, `dimensoes`, `nodos`,
`metadados_execucao` e `avisos`. Cada nodo informa `caminho`, contagem distinta
de entrevistas, `base_pai`, `percentual_pai`, `percentual_total` e
`tem_filhos`.

### Semântica dos filtros

Os filtros controlam quais categorias e ramificações são retornadas. Eles são
aplicados depois da construção das bases e do cálculo dos percentuais, sem
renormalização:

```text
Base = 100
A = 40%, B = 30%, C = 30%

Filtro = A e B
Resultado = A 40%, B 30%
Não resulta em 57,14% / 42,86%.
```

Nos níveis seguintes, `percentual_pai` continua usando todas as entrevistas do
segmento pai calculado, inclusive categorias que não foram selecionadas para
retorno.

### GET `/relatorios/pesquisas/{pesquisa_id}/cruzamentos-multidimensionais/opcoes/`

Fonte única usada pelo Web para configurar todas as dimensões elegíveis em uma
chamada. Não existe uma requisição por pergunta.

Resposta resumida:

```json
{
  "pesquisa_id": 9,
  "dimensoes": [
    {
      "pergunta_id": 42,
      "ordem": 2,
      "texto_pergunta": "Sexo",
      "tipo_pergunta": "escolha_simples",
      "eh_resposta_espontanea": false,
      "papel_analitico": "PERFIL",
      "metadados_analiticos": { "dimensao": "PERFIL", "subtipo": "SEXO" },
      "cardinalidade_observada": 2,
      "valores": [
        {
          "valor_chave": "Feminino",
          "rotulo": "Feminino",
          "ordem": 1,
          "contagem_entrevistas": 137,
          "origem": "OPCAO"
        },
        {
          "valor_chave": "__SEM_RESPOSTA__",
          "rotulo": "Sem resposta",
          "ordem": null,
          "contagem_entrevistas": 0,
          "origem": "SEM_RESPOSTA"
        }
      ]
    }
  ]
}
```

Perguntas fechadas preservam opções configuradas e sua ordem, inclusive opções
com zero entrevistas. Valores espontâneos usam `origem` igual a
`CATEGORIA_ESPONTANEA`; o valor técnico ausente usa `SEM_RESPOSTA`.

### Respostas espontâneas

O endpoint de opções e o motor usam o mesmo pipeline dos demais relatórios:

```text
Resposta bruta
  -> normalização da Apuração de Respostas Espontâneas
  -> get_active_spontaneous_mapping_for_report
  -> resolve_reportable_response_value
  -> categoria ativa ou “Não categorizada”
  -> motor multidimensional
```

Não existe fuzzy matching ou normalização paralela nos Cruzamentos
Estratégicos. Variantes brutas deixam de ser nodos separados quando o
mapeamento ativo as resolve para a mesma categoria. Na modelagem atual, os
mapeamentos ativos são vinculados à pesquisa.

### Sem resposta, validação e tenant

```text
valor_chave = __SEM_RESPOSTA__
rotulo = Sem resposta
```

O valor técnico pode ser usado em `filtros_respostas`. Pesquisas fora do tenant
autenticado retornam `404`; perguntas inválidas, filtros inválidos e limites
excedidos retornam `422`. O tenant é derivado de
`current_user.company_id -> Projeto -> Pesquisa`.

Limites padrão configuráveis: 8 dimensões, 5.000 nodos e 100.000 combinações.

## Perguntas: aplicabilidade territorial (FASE F)

Uma Pergunta e `GLOBAL` (todos os setores) ou `TERRITORIAL` (somente setores
cujo municipio resolvido esta entre os municipios da pergunta). Perguntas
anteriores a esta fase sao `GLOBAL` por backfill.

### POST/PATCH `/pesquisas/{pesquisa_id}/perguntas/`

```json
{ "texto_pergunta": "...", "tipo_pergunta": "TEXTO",
  "aplicabilidade": "TERRITORIAL", "municipio_ids": [16] }
```

`municipio_ids` referencia `TerritorioEleitoral` de tipo `MUNICIPIO` da Base
Eleitoral principal do projeto. Invariantes (422): `GLOBAL` com municipios;
`TERRITORIAL` sem municipio. No PATCH, campo ausente nao altera a associacao;
`aplicabilidade: GLOBAL` remove as associacoes.

Resposta inclui `aplicabilidade`, `municipio_ids` e `municipios[{id,nome}]`.

### Municipio do Setor

Derivado, nunca armazenado: `Setor -> SetorTerritorioEleitoral ->
TerritorioEleitoral(BAIRRO).municipio_id`. Status `RESOLVIDO`,
`SEM_MUNICIPIO` (sem composicao ou composicao sem relacao municipal) ou
`AMBIGUO` (bairros de mais de um municipio). Nunca ha escolha arbitraria.

### GET `/agente/missao/{pesquisa_id}` (aditivo)

Cada setor traz `territorio_status`, `municipio` (`{id, nome}` ou `null`) e
`pergunta_ids_aplicaveis` (ordem do questionario). A raiz traz
`possui_perguntas_territoriais`. Setor nao resolvido recebe apenas as GLOBAL.

### POST `/pesquisas/{pesquisa_id}/coletas/` (capability)

`questionario_territorial: true` declara que o cliente filtrou o questionario
por `pergunta_ids_aplicaveis`; com ele, resposta a pergunta nao aplicavel ao
`setor_id` (ou nao GLOBAL, sem setor) rejeita a coleta inteira com 422.
Cliente sem o marcador (legado) segue a politica anterior -- janela de
transicao, nao regra definitiva.

**Limitacao:** a identidade municipal e versionada por Base Eleitoral. Trocar
a base principal do projeto exige reassociar as perguntas TERRITORIAIS.

## 14. Auditoria de segurança (ADR-039)

### GET `/admin/auditoria/eventos` (Gerente ou Superadmin)

| Perfil | Escopo |
|---|---|
| Superadmin | global; `company_id` é filtro real |
| Gerente | **somente o próprio tenant**; `company_id` da query é ignorado como seletor de escopo (nunca amplia, nunca atravessa) |
| Coordenador / Supervisor / Cliente / Agente | 403 (`require_manager_or_superadmin`, no Backend) |

Filtros opcionais (todos combináveis): `event_type`, `severity`, `user_id`,
`project_id`, `ip_address`, `data_inicio`, `data_fim` (ISO-8601, filtram
`occurred_at`, timezone-aware) e, para Superadmin, `company_id`. Não há busca
textual.

Paginação: `limit` (default **50**, máximo **100** — `limit=500` responde 422)
e `offset` (≥ 0). Ordenação fixa: `occurred_at DESC, id DESC` (mais recente
primeiro). Mesmo formato de página de `TerritorioEleitoralPage`:

```json
{ "items": [
    { "id": 42, "occurred_at": "2026-08-27T14:00:00Z",
      "event_type": "CROSS_TENANT_ACCESS_ATTEMPT", "severity": "HIGH",
      "user_id": 5, "company_id": 10, "project_id": 201, "attempted_email": null,
      "ip_address": "203.0.113.9", "user_agent": "Mozilla/5.0 …",
      "http_method": "GET", "path": "/projetos/201", "status_code": 404,
      "request_id": "3f2a…", "details": { "motivo": "outro_tenant" } } ],
  "total": 1, "limit": 50, "offset": 0 }
```

Tipos: `LOGIN_SUCCESS`, `LOGIN_FAILED`, `ACCOUNT_INACTIVE_LOGIN`,
`TOKEN_EXPIRED`, `TOKEN_INVALID`, `TOKEN_REJECTED`, `RBAC_DENIED`,
`ACCESS_DENIED`, `CROSS_TENANT_ACCESS_ATTEMPT`, `PROJECT_ACCESS`,
`ACCOUNT_ACTIVATED`, `ACL_CHANGED`. Severidades: `INFO`, `WARNING`, `HIGH`.

`PROJECT_ACCESS` = *entrada lógica* no projeto, não cada requisição: nasce no
`GET /projetos/{id}` e é **deduplicado por 15 minutos** por (usuário, projeto).
`company_id` do evento é o tenant do **projeto** (relevante para Superadmin).

`path` guarda `request.url.path` — **nunca** a query string.

Toda resposta da API passa a trazer o header **`X-Request-ID`**, que casa com
`request_id` do evento. `details` nunca contém senha, token ou dado pessoal de
entrevistado. Nenhum contrato existente mudou.

### Notificação de acesso ao projeto (ADR-040)

Sem endpoint novo e sem mudança de contrato. Quando `GET /projetos/{id}`
**persiste** um `PROJECT_ACCESS` (isto é, fora da janela de 15 min), o
Backend envia um e-mail ao **Gerente responsável** do projeto
(`projetos.coordenador_id`) e registra o resultado na própria trilha:

| Evento | Severidade | `details` |
|---|---|---|
| `PROJECT_ACCESS_NOTIFICATION_SENT` | INFO | `{ "recipient_user_id" }` |
| `PROJECT_ACCESS_NOTIFICATION_SUPPRESSED` | INFO | `{ "recipient_user_id", "reason": self_access · no_project_manager · inactive_recipient · missing_recipient_email · smtp_not_configured }` |
| `PROJECT_ACCESS_NOTIFICATION_FAILED` | WARNING | `{ "recipient_user_id", "reason": smtp_error · exception:<Tipo> }` |

`user_id` = ator do acesso; `company_id` = tenant do projeto; `project_id` =
projeto acessado. Falha de e-mail **nunca** altera a resposta do GET (200).
Os três tipos aparecem em `GET /admin/auditoria/eventos` com os filtros usuais.

### GET `/admin/auditoria/resumo` (Gerente ou Superadmin) — ADR-041

Agregações do painel de segurança, calculadas em SQL (`COUNT`, `SUM(CASE)`,
`GROUP BY`, `MAX`). Mesma matriz de acesso de `/eventos`: Superadmin global
(`company_id` filtro real); Gerente **somente o próprio tenant** (`company_id`
externo ignorado; eventos **sem** `company_id` não entram); demais perfis 403.
Somente leitura — consultar o painel não gera evento.

Filtros: `data_inicio`, `data_fim` (default: últimas 24h), `company_id`
(Superadmin), `project_id`, `user_id`. Não há `event_type`/`severity`: os
cards já são recortes por tipo, e `limit/offset` não se aplicam a agregados.

```json
{ "periodo": { "data_inicio": "2026-08-27T12:00:00Z", "data_fim": "2026-08-28T12:00:00Z" },
  "granularidade": "hora",
  "totais": { "total_eventos": 174, "login_failed": 12, "access_denied": 7, "cross_tenant": 1,
              "project_access": 34, "notification_failed": 2, "high": 1 },
  "top_ips": [ { "ip_address": "203.0.113.10", "total": 23, "login_failed": 18,
                 "access_denied": 4, "cross_tenant": 1, "last_event_at": "…" } ],
  "top_attempted_accounts": [ { "attempted_email": "alvo@empresa.com", "total": 9, "last_event_at": "…" } ],
  "timeline": [ { "periodo": "2026-08-28T08:00:00", "login_failed": 3, "access_denied": 1, "cross_tenant": 0 } ] }
```

Semântica dos totais: `login_failed` = `LOGIN_FAILED` + `ACCOUNT_INACTIVE_LOGIN`;
`access_denied` = `RBAC_DENIED` + `ACCESS_DENIED` (cross-tenant **não** é
somado duas vezes); `project_access` conta o evento já deduplicado (15 min);
`notification_failed` conta só `…_FAILED` (`SUPPRESSED` não é falha);
`high` = `severity = HIGH`.

`top_ips` e `timeline` consideram apenas eventos de segurança
(`LOGIN_FAILED`, `ACCOUNT_INACTIVE_LOGIN`, `TOKEN_*`, `RBAC_DENIED`,
`ACCESS_DENIED`, `CROSS_TENANT_ACCESS_ATTEMPT`); `LOGIN_SUCCESS`,
`PROJECT_ACCESS` e `NOTIFICATION_*` ficam fora. `top_attempted_accounts`
agrupa `attempted_email` dos logins falhos. Rankings limitados a 10, ordenados
por volume e depois pelo evento mais recente. `granularidade` = `hora` até 48h
de período, `dia` acima; `periodo` da série é o início do bucket em UTC.

### Cotas por Perfil — tenant do Projeto (ADR-034)

`GET/PUT /projetos/{p}/pesquisas/{s}/cotas-perfil` e `/progresso`: a ACL do
Projeto autoriza; o plano é gravado e lido com `company_id` do **Projeto**,
não da empresa principal de quem configura. Superadmin de outra empresa e
Gerente com ACL cruzada configuram e leem o mesmo plano que o dono.
`GET` sem plano responde 404 ("nao configurado"): é estado funcional.

### Visibilidade de leitura da Base Eleitoral (ADR-034)

`GET /base-eleitoral/`, `/base-eleitoral/{id}`, `/territorios`,
`/importacoes`, `/divergencias`: visível = oficial, **ou** privada da empresa
principal, **ou** principal de um Projeto alcançado pela ACL do usuário. É o
que permite ao Superadmin (empresa Admin) e ao Gerente com ACL cruzada
carregar os municípios da Base do Projeto. A escrita (importar/validar/
parâmetros) continua restrita ao dono da Base ou Superadmin.

### Setor: município de referência (ADR-035-B)

`GET/POST/PATCH …/setores` devolvem, aditivamente, `municipio_territorio_id`,
`municipio {id, nome} | null` e `municipio_status` (RESOLVIDO · AMBIGUO ·
SEM_MUNICIPIO · FORA_DA_BASE · SEM_BASE). `POST/PATCH` aceitam
`municipio_territorio_id` opcional, validado contra a Base principal do
Projeto (404 fora dela) e contra a geometria (422 se contradiz). Setor
operacional que atravessa mais de um município → 422.

`GET …/cotas-perfil/contexto-territorial` → `[{territorio_id, territorio_nome,
setores_operacionais[{id,nome,meta}], meta_territorial}]`.
`PlanoCotaPerfilRead.diagnostico[]` ganha `meta_territorial`, `diferenca` e
`setores_operacionais`.
# Capacidades comerciais do usuário atual

`GET /usuarios/me/modulos/` exige autenticação e não recebe `company_id`. A
empresa é exclusivamente `current_user.company_id` (empresa principal/default).
O contrato não representa autorização por perfil e não ativa gating.

## Gates comerciais internos (Prompt 02)

Nenhuma rota pública foi criada ou alterada. Dependencies internas permitem que
rotas futuras exijam módulo ou feature. Para recurso já autorizado sem licença,
o contrato é HTTP 403. Recurso inexistente/não autorizado, módulo/feature
inexistente ou inativo retornam HTTP 404 com mensagem genérica. A rota
`GET /usuarios/me/modulos/` permanece sem gate e retorna `200 {"modulos": []}`
quando não há entitlement.

## Administração de módulos (Prompt 04)

Todas as rotas exigem Superadmin: `GET /admin/modulos/` consulta catálogo;
`GET /admin/empresas/{company_id}/recursos/` lista somente Projetos/Pesquisas da
empresa para os seletores; `GET` e `POST /admin/empresas/{company_id}/entitlements/`
listam/criam; `PATCH /admin/empresas/{company_id}/entitlements/{id}` altera
somente status/validade; `PUT .../{id}/funcionalidades` substitui explicitamente
as features. Duplicado retorna 409; payload/feature inválido retorna 422;
empresa ou recurso incompatível retorna 404. Não há DELETE.

```json
{
  "modulos": [
    {
      "chave": "inteligencia_eleitoral",
      "nome": "Inteligencia Eleitoral",
      "funcionalidades": []
    }
  ]
}
```

Sem entitlement efetivo: HTTP 200 com `{"modulos": []}`. IDs internos,
entitlements e dados de outros tenants não são expostos. Parâmetros extras como
`company_id` não alteram o tenant resolvido.

## Inteligência Eleitoral — Potencial de Crescimento (MVP 1, Prompt 04)

Prefixo real:
`/projetos/{projeto_id}/pesquisas/{pesquisa_id}/inteligencia-eleitoral/potencial-crescimento`

Rotas implementadas:

```http
GET  .../opcoes-configuracao
POST .../validar-configuracao
POST .../analisar
```

Autorização (ordem garantida): autenticação (401) → Pesquisa do path com ACL
e `Pesquisa.projeto_id == projeto_id` (404, inclusive cross-tenant, ANTES de
qualquer avaliação comercial) → `Permissao.INTELIGENCIA_VER` (403) →
`require_feature("inteligencia_eleitoral", "potencial_crescimento")` no
contexto da Pesquisa (404 capacidade inativa / 403 não contratada). A feature
está INATIVA no catálogo real: as rotas respondem 404
`CAPACIDADE_INDISPONIVEL` até a ativação formal do produto.

Request de validar/analisar: `GrowthAnalysisConfiguration` canônica
(doc/15), com `body.pesquisa_id == path.pesquisa_id` obrigatório (422
`SURVEY_PATH_BODY_MISMATCH`). Nenhum request/response contém
`company_id`/`tenant_id`.

Responses: `GET opcoes-configuracao` → `GrowthConfigurationOptionsResponse`
(perguntas com `compatible_as` técnico, valores canônicos, territórios da
Pesquisa, constraints sem defaults metodológicos). `POST validar-configuracao`
→ HTTP 200 `{valid, normalized_configuration, errors[], warnings[]}` mesmo
para configuração semanticamente inválida (422 apenas para transporte
impossível/mismatch). `POST analisar` → `GrowthAnalysisResponse` (snapshot,
universos, diagnostics, findings com evidências e denominadores explícitos,
warnings estruturados); configuração inválida → 422
`{code: GROWTH_CONFIGURATION_INVALID, errors, warnings}`; limite de
segmentos → 422 `{code: SEGMENT_LIMIT_EXCEEDED}`. Métricas em unidade
canônica (rate/interval 0–1, delta em pp, lift razão) como JSON numbers —
sem strings numéricas e sem arredondamento de apresentação;
`weighted_base`/`lift`/intervalos indisponíveis viajam como null. Execução
síncrona e efêmera: nada é persistido. Contrato completo em
`doc/17-inteligencia-eleitoral-potencial-crescimento-api.md`.

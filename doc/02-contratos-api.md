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
  "token_type": "bearer"
}
```

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

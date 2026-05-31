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

## 9. Coletas

### POST `/pesquisas/{pesquisa_id}/coletas/`

Endpoint usado pelo mobile para sincronizar coleta.

```json
{
  "data_inicio_coleta": "2026-05-03T09:00:00",
  "data_fim_coleta": "2026-05-03T09:04:00",
  "localizacao_inicio": { "lat": 0.0405, "lon": -51.1352 },
  "localizacao_fim": { "lat": 0.0406, "lon": -51.1351 },
  "respostas": [
    { "pergunta_id": 13, "valor_resposta": "Candidato A" }
  ]
}
```

**Regras:**

- `pesquisa_id` vem da URL.
- `agente_id` deve ser `current_user.id`.
- `company_id` não vem do payload.
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

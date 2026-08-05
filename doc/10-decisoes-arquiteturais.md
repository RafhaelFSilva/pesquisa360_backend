# Pesquisa360 — Decisões Arquiteturais

**Status:** registro de decisões pós-estabilização  
**Formato:** ADR simplificado

## ADR-001 — Backend como fonte da verdade

O backend é responsável por autenticação, autorização, multitenancy, vínculo de agente e validação de regras críticas.

Consequências:

- Web e Mobile não enviam `company_id`.
- Web e Mobile não definem tenant.
- Criação de coleta usa `current_user.id`.
- Toda rota sensível valida `current_user.company_id`.

## ADR-002 — Multitenancy por `company_id`

O isolamento é feito por `company_id`, associado a usuários e projetos.

```text
Usuário autenticado -> company_id -> projetos -> pesquisas -> dados
```

## ADR-003 — Soft delete para entidades estruturais

Projetos, pesquisas e perguntas devem usar soft delete para preservar histórico.

Status atual:

- Projeto: soft delete.
- Pesquisa: soft delete.
- Pergunta: soft delete.
- Setor: delete físico no estado atual.

Decisão futura: avaliar soft delete para setores.

## ADR-004 — Geometrias não retornam PostGIS cru

Nenhum endpoint deve retornar `WKBElement` ou objeto PostGIS cru.

Converter para:

- `{lat, lng}`;
- `poligono: [{lat, lng}]`;
- GeoJSON serializável.

## ADR-005 — `lat/lng` como padrão futuro

Novas rotas devem usar `lat/lng`.

Algumas rotas legadas usam `lon`. Manter compatibilidade até migração controlada.

## ADR-006 — Mobile offline-first

O app mobile deve funcionar sem internet.

Consequências:

- banco local com Drift/SQLite;
- upload posterior de coletas;
- `client_uuid` é gerado uma vez no Mobile, persistido localmente e reutilizado em retries;
- o Backend garante idempotência por `company_id + client_uuid` e retorna a coleta existente em reenvios do mesmo agente;
- `foi_offline` representa condição no momento da coleta;
- sincronização explícita.

## ADR-007 — Monitoramento atual é de coletas sincronizadas

A tela atual mostra coletas já enviadas ao servidor.

Não incluso:

- posição viva do agente;
- heartbeat;
- tracking contínuo.

Evolução: polling Web e depois heartbeat Mobile.

## ADR-008 — Geocoding não roda em GET de tela

Reverse geocoding não deve ser chamado ao abrir monitoramento.

Regra:

```text
criação/backfill -> calcula endereço
monitoramento -> retorna endereço salvo
```

## ADR-009 — Crosstab 2D antes de multivariável

O crosstab atual cruza duas perguntas categóricas.

Fora do escopo atual:

```text
Sexo + Faixa etária x Governador
```

## ADR-010 — Frontend usa services para API

Chamadas HTTP devem ficar em `src/api`.

Exemplo:

```ts
projectsService.getSetores(projectId, surveyId)
reportsService.getSimpleReport(surveyId)
```

## ADR-011 — React Router usa `projectId` e `surveyId`

No frontend:

```text
:projectId
:surveyId
```

No backend:

```text
projeto_id
pesquisa_id
```

## ADR-012 — Leaflet controls adicionados uma vez

Controles de desenho Leaflet/Geoman devem ser adicionados uma única vez por instância de mapa.

## ADR-013 — Checkpoints antes de novas features

Após estabilizar fluxos, criar checkpoint/tag antes de novas implementações.

## ADR-014 — Prompts econômicos para IA/Codex

Usar prompts pequenos, com escopo fechado, arquivo provável e erro específico.

Template:

```text
Modo econômico.
Escopo: Frontend Web apenas.
Arquivo: ...
Erro: ...
Patch: ...
Não mexer em: ...
Depois: build/test/status.
```

## ADR-015 — Documentação acompanha o contrato real

Docs devem refletir o comportamento validado, não apenas a intenção original.

Quando uma rota é ajustada, atualizar:

- `02-contratos-api.md`;
- `09-checklist-testes.md`;
- `08-roadmap.md`, se aplicável.

## ADR-016 — Importação administrativa de Shapefile por CLI

Importação administrativa de Shapefile por CLI, convertida para `Polygon`
EPSG:4326 e submetida às mesmas regras de tenant da aplicação. A ferramenta
reutiliza o contrato interno e o CRUD de criação de setor, sem endpoint ou
migration novos. `MultiPolygon` é rejeitado enquanto a coluna permanecer
`Geometry("POLYGON", srid=4326)`.

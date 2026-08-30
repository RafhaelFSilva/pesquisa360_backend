# Pesquisa360 — Arquitetura Atual

**Status:** baseline pós-validação Web/Backend  
**Escopo:** Backend FastAPI, Frontend Web React/Vite, Mobile Flutter  
**Objetivo:** registrar a arquitetura real do sistema após estabilização das funcionalidades existentes.

## 1. Visão geral

O Pesquisa360 é uma plataforma de pesquisa de campo composta por três camadas principais:

```text
Frontend Web React/Vite
  Gestão, território, monitoramento e relatórios
        │ HTTPS / JWT
Backend FastAPI
  Regras de negócio, autenticação, multitenancy, PostGIS
        │ SQLAlchemy / GeoAlchemy2
PostgreSQL + PostGIS
  Fonte central da verdade

Mobile Flutter
  Coleta offline-first e sincronização
```

A regra central é: **o backend é a fonte da verdade**. Web e Mobile não devem inferir tenant, agente ou permissões quando o backend consegue determinar isso pelo token autenticado.

## 2. Backend

### Stack

- Python
- FastAPI
- Pydantic
- SQLAlchemy
- GeoAlchemy2
- PostgreSQL
- PostGIS
- Alembic
- JWT / Passlib / python-jose
- Docker / Docker Compose
- Geopy/Nominatim para geocoding reverso

### Organização lógica

```text
pesquisa360/
  api/endpoints/
    login.py
    usuarios.py
    projetos.py
    coletas.py
    relatorios.py
    agente.py
    locais.py
  core/
    dependencies.py
    security.py
  db/
    models.py
    session.py
  utils/
    geocoding.py
  services/
    multidimensional_cross.py
    setor_shapefile_import.py
    auditoria.py
    email.py
    notificacoes.py
  crud.py
  schemas.py
migrations/
scripts/
  importar_setor_shapefile.py
  importar_setor_shapefile.sh
```

### Responsabilidades

| Camada | Responsabilidade |
|---|---|
| `api/endpoints` | Receber requisições, validar dependências e orquestrar resposta |
| `crud.py` | Consultas e persistência |
| `schemas.py` | Contratos Pydantic |
| `models.py` | Entidades SQLAlchemy/PostGIS |
| `dependencies.py` | `get_db`, `get_current_user`, validações de autenticação |
| `security.py` | Hash de senha, criação e validação de token |
| `utils/geocoding.py` | Geocoding reverso por coordenadas |
| `services/multidimensional_cross.py` | Motor multidimensional, opções analíticas, caminhos, bases, percentuais e filtros de categorias |
| `services/setor_shapefile_import.py` | Validacao e conversao pura de Shapefile para Polygon EPSG:4326 |
| `services/auditoria.py` | Trilha `audit_events` (ADR-039): registro fail-soft em sessão própria, contexto HTTP, deduplicação de `PROJECT_ACCESS` |
| `services/email.py` | Único cliente SMTP (ADR-035): envio transacional fail-soft, modo registro sem `SMTP_HOST` |
| `services/notificacoes.py` | Notificação ao Gerente responsável por acesso ao projeto (ADR-040): destinatário, mensagem, resultado auditado |
| `services/auditoria.resumo_seguranca` | Agregações SQL do painel de segurança (ADR-041): totais, ranking de IPs, contas mais tentadas, série temporal — nunca materializa `AuditEvent` |
| `scripts/importar_setor_shapefile.*` | Importacao administrativa que reutiliza o CRUD de setores |

### Regras

- Nunca confiar em `company_id` enviado pelo cliente.
- Nunca confiar em `agente_id` enviado pelo cliente para criação de coleta.
- Sempre derivar usuário autenticado via `current_user`.
- Em endpoints multitenant, validar acesso por `current_user.company_id`.
- Nunca retornar objetos PostGIS crus (`WKBElement`).
- Serializar geometrias manualmente com `ST_AsGeoJSON` ou estrutura `{lat, lng}`.

## 3. Frontend Web

### Stack

- React
- TypeScript
- Vite
- Tailwind CSS
- Axios
- React Router
- Zustand
- Recharts
- Leaflet / React-Leaflet
- Leaflet Geoman

### Organização esperada

```text
src/
  api/
    authService.ts
    projectsService.ts
    reportsService.ts
  components/
    GeofenceMap.tsx
    SectorManager.tsx
    ColetasMap.tsx
    ProtectedRoute.tsx
    AnalyticConfigurationPanel.tsx
    StrategicDimensionSelector.tsx
    StrategicCrossVisualization.tsx
    StrategicCrossingsReport.tsx
  pages/
    LoginPage.tsx
    ProjectsPage.tsx
    ProjectDetailPage.tsx
    SurveyDetailPage.tsx
    ReportsPage.tsx
    SurveyIntelligencePage.tsx
    StrategicCrossingsPage.tsx
    StatisticsReportPage.tsx
    CrosstabReportPage.tsx
    MonitoringPage.tsx
  lib/
    axios.ts
  store/
    authStore.ts
  types/
    project.ts
    question.ts
```

### Regras do Web

- Login deve chamar `/login/token` e depois `/usuarios/me/`.
- Não usar fallback de usuário fake.
- Não persistir `company_id` hardcoded.
- Axios deve injetar `Authorization: Bearer <token>`.
- Rotas privadas devem passar por `ProtectedRoute`.
- Services devem encapsular chamadas à API.
- Componentes de página não devem montar URL manualmente quando já houver service.

## 4. Mobile

### Stack

- Flutter / Dart
- Drift sobre SQLite
- BLoC / Cubit
- Dio
- flutter_secure_storage
- get_it
- geolocator

### Princípio

O mobile é **offline-first**. Toda coleta deve poder ocorrer sem internet e ser sincronizada depois.

### Regras

- Login deve obter token e perfil real do usuário.
- Identidade do agente deve vir de `/usuarios/me/`.
- Coletas locais devem ser vinculadas ao agente autenticado.
- Upload de coleta não deve enviar `company_id`.
- Upload de coleta não deve depender de `agente_id` hardcoded.
- Em troca de usuário, limpar ou isolar dados locais do usuário anterior.

## 5. Domínios principais

| Domínio | Descrição |
|---|---|
| Autenticação | JWT, usuário autenticado e perfil |
| Multitenancy | Isolamento por empresa/tenant |
| Projetos | Unidade superior de organização |
| Pesquisas | Questionários dentro de projetos |
| Perguntas | Estrutura do formulário |
| Coletas | Respostas + GPS + status de sync |
| Geofence | Cerca global da pesquisa |
| Setores | Subdivisao operacional e cotas no estado atual; separacao futura entre territorio operacional e analitico |
| Relatórios clássicos | Resumo das Respostas e Crosstab 2D |
| Inteligência | Configuração Analítica e Cruzamentos Estratégicos nos modos Explorar e Relatório |
| Monitoramento | Visualização de coletas em mapa |

## 6. Riscos conhecidos

1. Contratos geoespaciais ainda apresentam variações: `lon`, `lng`, `geometria` e `poligono`.
2. Mobile ainda precisa ser revalidado contra multitenancy.
3. Geocoding existe, mas precisa de política controlada para evitar chamadas em massa.
4. Monitoramento atual é de coletas sincronizadas, não heartbeat em tempo real do agente.
5. Cruzamentos Estratégicos entregam evidência descritiva; causalidade, propensão, conversão, oportunidade, migração e previsão continuam fora do ciclo atual.
6. A finalidade territorial `OPERACAO` / `RELATORIO` / `AMBOS` esta aprovada,
   mas ainda nao implementada no contrato atual.

## 7. Direção recomendada

- Consolidar contratos de API.
- Escrever testes mínimos para multitenancy.
- Separar CRUD/serviços por domínio à medida que o backend crescer.
- Criar camada explícita de sincronização mobile.
- Tratar geocoding como processamento controlado/backfill.
- Evoluir monitoramento em duas fases: polling Web e heartbeat Mobile.
- Implementar a separacao entre setores operacionais e analiticos preservando
  compatibilidade: todos os setores existentes devem iniciar como `OPERACAO`.

## 8. Arquitetura dos Cruzamentos Estratégicos

```text
Resposta.valor_resposta
  -> get_active_spontaneous_mapping_for_report
  -> resolve_reportable_response_value
  -> valor analítico/categoria ativa
  -> services/multidimensional_cross.py
  -> nodos, bases e percentuais
  -> filtro das categorias retornadas sem renormalização
```

O motor une respostas pela mesma `coleta_id`, materializa somente caminhos
observados e preserva o Crosstab 2D em contrato separado. Não existe fuzzy
matching dentro do motor. Na modelagem atual, categorias e mapeamentos
espontâneos ativos pertencem à pesquisa.

No Web, a Central de Inteligência contextual à pesquisa leva a uma página de
Cruzamentos Estratégicos com dois consumidores do mesmo contrato:

```text
Central de Inteligência
  -> Cruzamentos Estratégicos
       -> Modo Explorar
       -> Modo Relatório
```

A Configuração Analítica permanece na pesquisa e fornece
`papel_analitico` e `metadados_analiticos`. A rota global `/inteligencia`
continua sendo a Inteligência Territorial, distinta da central contextual.

## Ambiente de execução (ADR-038)

`APP_ENV` é a única variável de ambiente que descreve o modo de execução:

```text
APP_ENV=development   (default)  -> Swagger/ReDoc/OpenAPI disponíveis
APP_ENV=production               -> as três rotas não são registradas (404)
```

Lida em `pesquisa360/core/ambiente.py` e aplicada no construtor do `FastAPI()`
em `pesquisa360/main.py`. O ambiente é **configuração explícita**: não há
inferência por hostname, domínio, porta ou presença de Docker.

Chega ao container pelo `env_file: .env` do `docker-compose.yml`. O deploy de
produção precisa defini-la — o default preserva a produtividade local.

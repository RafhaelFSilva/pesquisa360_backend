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
  crud.py
  schemas.py
migrations/
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
  pages/
    LoginPage.tsx
    ProjectsPage.tsx
    ProjectDetailPage.tsx
    SurveyDetailPage.tsx
    ReportsPage.tsx
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
| Setores | Subdivisão operacional e cotas |
| Relatórios | Resumo e crosstab |
| Monitoramento | Visualização de coletas em mapa |

## 6. Riscos conhecidos

1. Contratos geoespaciais ainda apresentam variações: `lon`, `lng`, `geometria` e `poligono`.
2. Mobile ainda precisa ser revalidado contra multitenancy.
3. Geocoding existe, mas precisa de política controlada para evitar chamadas em massa.
4. Monitoramento atual é de coletas sincronizadas, não heartbeat em tempo real do agente.
5. Relatórios multivariáveis ficam fora do ciclo atual.

## 7. Direção recomendada

- Consolidar contratos de API.
- Escrever testes mínimos para multitenancy.
- Separar CRUD/serviços por domínio à medida que o backend crescer.
- Criar camada explícita de sincronização mobile.
- Tratar geocoding como processamento controlado/backfill.
- Evoluir monitoramento em duas fases: polling Web e heartbeat Mobile.

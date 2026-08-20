# Pesquisa360 — Frontend Web

**Status:** Web estabilizado para administração, território, relatórios e monitoramento  
**Stack:** React, TypeScript, Vite, Tailwind, Axios, Zustand, Recharts, Leaflet

## 1. Função do Web

A plataforma Web é usada por coordenadores, gerentes e supervisores para:

- autenticação;
- gestão de projetos;
- gestão de pesquisas;
- montagem de questionários;
- definição de cerca global;
- cadastro de setores e agentes;
- monitoramento de coletas;
- relatórios simples;
- crosstab;
- Configuração Analítica;
- Central de Inteligência e Cruzamentos Estratégicos.

## 2. Estrutura recomendada

```text
src/
  api/
    authService.ts
    projectsService.ts
    reportsService.ts
  components/
    ProtectedRoute.tsx
    GeofenceMap.tsx
    SectorManager.tsx
    ColetasMap.tsx
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
    analyticConfiguration.ts
    strategicCrossings.ts
  store/
    authStore.ts
  types/
    project.ts
    question.ts
    strategicCrossings.ts
```

## 3. Autenticação

Fluxo correto:

```text
POST /login/token
GET /usuarios/me/
authStore.setAuth(token, user)
navegar para /projetos
```

Proibido:

- criar usuário fake;
- assumir `company_id=1`;
- navegar para área privada se `/usuarios/me/` falhar;
- manter token inválido no Axios.

## 4. Rotas Web padronizadas

```text
/login
/projetos
/projetos/:projectId
/projetos/:projectId/pesquisas/:surveyId
/projetos/:projectId/pesquisas/:surveyId/relatorios
/projetos/:projectId/pesquisas/:surveyId/relatorios/simples
/projetos/:projectId/pesquisas/:surveyId/relatorios/crosstab
/projetos/:projectId/pesquisas/:surveyId/inteligencia
/projetos/:projectId/pesquisas/:surveyId/inteligencia/cruzamentos
/projetos/:projectId/pesquisas/:surveyId/monitoramento
/inteligencia
```

No React Router:

```ts
const { projectId, surveyId } = useParams();
```

Não misturar `projetoId/pesquisaId` com `projectId/surveyId`.

## 5. Services

Componentes não devem montar URL manualmente quando já existe service.

Correto:

```ts
await projectsService.getSetores(projectId, surveyId);
```

Services principais:

| Service | Responsabilidade |
|---|---|
| `authService` | login e `/usuarios/me/` |
| `projectsService` | projetos, pesquisas, perguntas, geofence, setores, monitoramento |
| `reportsService` | relatórios clássicos, mapas e cruzamentos multidimensionais |

Métodos dos Cruzamentos Estratégicos:

```ts
reportsService.getMultidimensionalCross(pesquisaId, payload)
reportsService.getMultidimensionalCrossOptions(pesquisaId)
```

O Web não envia `company_id`; o backend resolve o tenant pelo usuário
autenticado.

## 6. Geoespacial no Web

Padrão interno:

```ts
type LatLngPoint = {
  lat: number;
  lng: number;
};
```

Quando o backend retornar GeoJSON:

```ts
coordinates[0].map(([lng, lat]) => [lat, lng])
```

Antes de renderizar Marker/Polygon:

```ts
Number.isFinite(lat) && Number.isFinite(lng)
```

## 7. Gestão de território

- Mapa abre na cerca global se existir.
- Se não existir, abre em Macapá-AP: `[0.0349, -51.0694]`.
- Agentes vêm de `/usuarios/agentes/`.
- Setores devem renderizar tanto `poligono` quanto `geometria`.
- Controles Leaflet/Geoman devem ser adicionados uma única vez.

Estado atual: a gestao de territorio trabalha com setores operacionais.

Roadmap aprovado: o Web devera permitir finalidade `OPERACAO`, `RELATORIO` e
`AMBOS` quando o backend expuser esse contrato. Setores `RELATORIO` nao devem
exigir agente, meta/cota ou tolerancia operacional. Setores `OPERACAO` e
`AMBOS`, quando usados operacionalmente, continuam sujeitos as regras de agente,
cota, tolerancia/geofence e monitoramento.

## 8. Relatórios

Central:

```text
CENTRAL DE RELATÓRIOS
Resumo das Respostas
Cruzamento de Dados
Mapas Estratégicos
Relatório Executivo
```

Crosstab deve aceitar perguntas categóricas:

```text
escolha_simples
multipla_escolha
multipla_escolha_unica
multipla_escolha_multipla
MultiplaEscolha_Unica
MultiplaEscolha_Multipla
```

Renderizar texto com:

```ts
question.texto_pergunta
```

O Crosstab 2D continua separado dos Cruzamentos Estratégicos. A Central de
Relatórios não contém o card de Cruzamentos.

## 9. Monitoramento

- Azul: coleta online (`foi_offline=false`).
- Vermelho: coleta offline (`foi_offline=true`).
- Tabela principal: `ID | Agente | Modo | Início | Fim | Detalhes`.
- Endereço e dados técnicos ficam em expansão/detalhes.
- Padrão: 10 coletas por página.
- Mostrar todas deve ativar scroll vertical interno, sem esticar o mapa.
- Ao clicar na coleta, usar `map.flyTo([lat, lng], zoom)`.

## 10. Build e validação

```bash
npm run build
```

## 11. Checklist para novas telas

- Usa `ProtectedRoute`?
- Usa service em `src/api`?
- Usa params corretos?
- Não envia `company_id`?
- Trata loading, erro e vazio?
- Valida `lat/lng` antes de mapa?
- Não causa scroll horizontal?
- Não chama API em loop?
- Não duplica controles Leaflet?

## 12. Central de Inteligência e Cruzamentos Estratégicos

A Central de Inteligência contextual à pesquisa usa:

```text
/projetos/:projectId/pesquisas/:surveyId/inteligencia
```

Ela é distinta da Central de Relatórios e da Inteligência Territorial global
em `/inteligencia`. O card implementado abre:

```text
/projetos/:projectId/pesquisas/:surveyId/inteligencia/cruzamentos
```

`StrategicDimensionSelector` é compartilhado pelos modos Explorar e Relatório.
Ele recebe do backend as categorias analíticas já resolvidas e oferece:

- checkbox para perguntas e seleção múltipla;
- ordem inicial do questionário e reordenação por setas;
- expansão das perguntas;
- multiseleção das respostas;
- ações Selecionar todas e Limpar;
- bloqueio de execução quando uma dimensão fica sem respostas.

O Web não normaliza respostas espontâneas, não aplica fuzzy matching e não
reconstrói categorias combinando opções e respostas brutas.

### Modo Explorar

Executa profundidade progressiva, mantém breadcrumb e reutiliza níveis já
carregados em memória. Exibe base do segmento, `percentual_pai` e
`percentual_total`. Barras, Pizza e Rosca são alternativas puramente visuais e
não geram nova chamada ao backend.

### Modo Relatório

Permite definir profundidade, segmentos, respostas, tipo único de gráfico para
o documento e Detalhes/tabela. O agrupamento é feito por caminho pai:

```text
nodos do backend -> filhos do mesmo caminho pai -> seção do relatório
```

Detalhes são opcionais e ficam ocultos por padrão. Sem a tabela, o gráfico usa
toda a largura disponível. Quando habilitados, desktop usa tabela fluida e
mobile usa linhas empilhadas, sem scroll horizontal ou vertical interno.

A impressão usa HTML/SVG nativos, CSS A4 e `window.print()`. Configuradores e
controles administrativos são ocultados em print; a tabela respeita a escolha
do usuário. Não são usados `jsPDF` ou `html2canvas` neste fluxo.

### Tipos principais

```text
MultidimensionalCrossRequest / MultidimensionalCrossResponse
MultidimensionalCrossOptionsResponse
CrossOptionDimension / CrossOptionValue
CrossDimension / CrossNode / CrossPathItem
StrategicResponseFilters / StrategicReportConfig / StrategicChartType
```

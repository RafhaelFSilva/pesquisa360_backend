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
/projetos/:projectId/pesquisas/:surveyId/controle-campo
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
| `fieldControlService` | painel de Controle de Campo (`GET .../controle-campo`, snapshot gerencial; helpers puros em `lib/fieldControl.ts`) |

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

## Permissões na interface (ADR-037)

`src/lib/permissions.ts` é a camada única: lê `permissions` de `/usuarios/me/` e
responde `canCreateProject`, `canManageSurvey`, `canViewReports`, `canManageUsers`
etc. Nenhuma tela compara `perfil_nome` ou `perfil_id` por conta própria.

- `ProtectedRoute` continua exigindo sessão e agora manda o Agente para
  `/sem-permissao` — o painel não é o ambiente dele.
- `PermissionRoute` condiciona uma área a capacidades; digitar a URL não abre.
- `NoPermissionPage` (`/sem-permissao`) explica sem stack trace.
- Ações impossíveis **não são renderizadas** (Novo Projeto, Editar, Excluir,
  Nova Pesquisa) — mas isso é UX: a API responde 403 para a mesma tentativa
  feita por fora.
- Sessão anterior a esta versão não tem `permissions` no store; há um espelho da
  matriz como fallback, senão a tela ficaria vazia até o próximo login.

## Painel administrativo de segurança (ADR-041)

Rota `/admin/seguranca` — título **Segurança**, subtítulo "Acompanhe acessos,
tentativas bloqueadas e eventos de segurança da plataforma." Módulo
**administrativo**: não vive em Monitoramento nem em Controle de Campo, e não
usa coletas, agentes, GPS, cotas ou setores.

| Peça | Arquivo |
|---|---|
| Rota | `App.tsx` → `<PermissionRoute permissoes={['USUARIO_GERENCIAR']}>` (Gerente e Superadmin; demais caem em `/sem-permissao`) |
| Menu | `AdminLayout.adminNavItems(user)` (por capacidade) e link "Segurança" no `Header` de projetos, ambos via `canViewSecurityPanel` |
| Service | `api/securityService.ts` — `getSecuritySummary`, `getAuditEvents` (só GET) |
| Types | `types/security.ts` — `AuditEvent`, `AuditEventPage`, `SecuritySummary`, `SecurityFilters` |
| Regras puras | `lib/security.ts` — rótulos amigáveis, severidade (texto + marcador), período, paginação (máx. 100), montagem de query, `sanitizeDetails` |
| Página | `pages/SecurityPage.tsx` |
| Componentes | `components/security/` — `SecurityFilters`, `SecuritySummaryCards`, `SecurityTimelineChart` (Recharts), `SecurityRankings` (IPs + contas), `SecurityEventsTable`, `SecurityEventDetails` (Dialog) |

Regras da tela:

- Cards, rankings e série vêm **prontos** de `/admin/auditoria/resumo`; a
  tabela de `/admin/auditoria/eventos`. O Web nunca conta eventos da página
  para compor indicador.
- Período padrão 24h; atalhos 7d/30d/personalizado. Datas exibidas na
  timezone do navegador; o detalhe mostra também o ISO UTC.
- Gerente vê a empresa **somente leitura** e nunca envia `company_id`;
  Superadmin tem seletor de empresa (filtro real). Projetos e usuários dos
  filtros vêm dos services já existentes, cada um limitado pelo Backend.
- `details` é renderizado chave/valor por `sanitizeDetails`, que descarta
  chaves `password|senha|token|authorization|cookie|secret|hash|jwt|bearer`
  (inclusive aninhadas) — defesa de apresentação, não substituto da
  sanitização do Backend.
- Severidade sempre com texto e marcador (`•`, `!`, `!!`), nunca só cor.
  Ranking de IP traz o aviso "IP não é identidade" e nenhum rótulo de
  "malicioso". Nenhum botão de bloqueio/banimento: o painel observa.
- Estados: loading (spinner), erro ("Não foi possível carregar…" + Tentar
  novamente), vazio ("Nenhum evento de segurança encontrado no período
  selecionado."), sucesso.

Testes: `tests/security.test.mjs` (30 casos, PW01–PW25 e extras).

## Cotas por Perfil — Configuração Analítica

`/projetos/:projectId/pesquisas/:surveyId?tab=analitica` → card **Cotas
Amostrais por Perfil** (`components/analytics/ProfileQuotaPanel`). Responde
"**quem** preciso entrevistar?" (Sexo × Faixa etária por município); a meta
territorial do setor, na Gestão de Território, responde "onde/quantas?". As
duas coexistem e não se substituem.

| Peça | Arquivo |
|---|---|
| Service | `api/profileQuotaService.ts` — `getProfileQuotaPlan` (GET; 404 = não configurado → `null`), `saveProfileQuotaPlan` (PUT idempotente do plano inteiro), `getProfileQuotaProgress` |
| Types | `types/profileQuota.ts` — espelho de `PlanoCotaPerfilRequest/Read`, `ProgressoCotaPerfilRead` |
| Regras puras | `lib/profileQuota.ts` — perguntas elegíveis, sugestões de mapeamento, formulário ↔ contrato, validação espelhando o Backend, status por município, mensagens |
| Componentes | `ProfileQuotaPanel` (card/estado/salvar/progresso), `ProfileQuotaSetup` (4 etapas), `ProfileQuotaMunicipalityMatrix`, `ProfileQuotaProgress` |

Fluxo: Etapa 1 perguntas de Sexo (escolha simples) e Idade (escolha simples →
`CATEGORICA`, numérica → `NUMERICA`) com mapeamento das opções reais
(`sexo_valores`, `idade_valores`); Etapa 2 municípios da **Base Eleitoral
principal do Projeto** (`GET /projetos/{id}/base-eleitoral` →
`/base-eleitoral/{base}/territorios?tipo=MUNICIPIO`), com busca; Etapa 3
matriz por município (accordion, totais por linha/coluna/geral, percentual
visual, "meta municipal de referência" apenas para conferência — o Backend não
persiste meta municipal); Etapa 4 revisão com "Salvar como configuração"
(`ativo=false`) ou "Salvar e ativar" (`ativo=true`).

Regras: Backend é o motor (classificação, progresso e prioridade vêm do
`/progresso`; nada é recalculado no React); sem `company_id`; edição só para
Gerente/Superadmin (`canConfigureProfileQuota`, espelho de
`require_manager_or_superadmin`); opção de idade incoerente (ex. "35 a 34
anos") gera aviso e **não** é corrigida pela tela; plano com coletas e troca de
variáveis de classificação exige confirmação forte; desativar = `ativo=false`
(sem DELETE); vazio (404) ≠ erro (5xx); Base ausente bloqueia a configuração
com orientação para vincular no Projeto. Copiar distribuição proporcional
entre municípios ficou fora desta rodada.

Testes: `tests/profileQuota.test.mjs` (13 casos).

## Modais × mapas Leaflet (escala de camadas)

`components/ui/dialog.tsx` renderiza em **Portal no `document.body`**, com
overlay `fixed inset-0 z-[9000]` e conteúdo `z-[9010]`, `max-height:
calc(100vh - 2rem)` e rolagem interna; trava o scroll do body enquanto aberto
e o restaura ao fechar. `.leaflet-container { isolation: isolate }` no CSS
global contém os z-index internos do Leaflet (panes 400, controles 1000,
busca/Geoman custom 800–900). Escala documentada em `index.css`: conteúdo <
100 · Leaflet < 1000 · popovers ~1000 · overlay 9000 · modal 9010. Motivo: a
"Composição eleitoral" (Gestão de Território) ficava atrás do mapa. Nenhum
modal deve usar z-index avulso para "vencer" o mapa.

O estado vazio "Sem plano de cotas de perfil" do Controle de Campo leva a
`/projetos/:p/pesquisas/:s?tab=analitica` (Configuração Analítica → Cotas por
Perfil); a aba é selecionada pela query `tab`, já suportada pela página.

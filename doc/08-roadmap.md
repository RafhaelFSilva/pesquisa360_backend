# Pesquisa360 — Roadmap Técnico e Produto

**Status:** roadmap pós-estabilização das funcionalidades existentes  
**Objetivo:** orientar próximas implementações sem quebrar o produto validado.

## 1. Marco atual

Funcionalidades validadas:

- login;
- multitenancy backend;
- CRUD de projeto;
- CRUD de pesquisa;
- CRUD de pergunta;
- soft delete;
- cerca global;
- setores e agentes;
- relatórios simples;
- crosstab 2D;
- motor de Cruzamentos Estratégicos de 2 a N dimensões;
- metadados analíticos configuráveis por pergunta;
- Central de Inteligência com modos Explorar e Relatório;
- filtros por resposta sem renormalização dos percentuais;
- categorização compartilhada de respostas espontâneas;
- monitoramento de coletas;
- online/offline;
- endereço estimado;
- layout operacional de monitoramento.

## 2. Princípio de evolução

Toda nova feature deve seguir:

```text
Plan -> Do -> Check -> Act
```

E passar por:

```text
contrato backend
service frontend
tela frontend
teste manual
checkpoint git
documentação
```

## 3. Prioridade alta

### Mobile multitenancy/sync

- login + `/usuarios/me/`;
- remover `agente_id` hardcoded;
- salvar perfil local;
- limpar dados ao trocar usuário;
- upload de coleta com usuário real;
- validar offline/online;
- validar geofence no upload;
- testar coleta aparecendo no monitoramento.

### Testes mínimos Backend

- login;
- `/usuarios/me/`;
- isolamento de projetos por tenant;
- criação de coleta com agente autenticado;
- relatório simples;
- crosstab;
- monitoramento.

### Contratos geoespaciais

Consolidar:

```text
lat/lng para Web e leituras
compatibilidade com lon no POST legado
GeoJSON apenas quando explicitamente documentado
```

### Geocoding controlado

- Não chamar Nominatim em GETs de tela.
- Preencher endereço na criação da coleta.
- Criar script de backfill.
- Adicionar delay/rate limit.

## 4. Prioridade média

### Monitoramento tempo real — fase 1

Polling no Web:

- botão liga/desliga;
- intervalo padrão 60s;
- atualização manual;
- persistência da preferência do admin.

### Monitoramento tempo real — fase 2

Heartbeat mobile:

- endpoint de localização atual;
- envio periódico pelo app;
- controle de bateria;
- controle liga/desliga por pesquisa;
- status online/ausente.

### Setores e cotas

- concluido: importacao administrativa de Shapefile por CLI, restrita a
  `Polygon` EPSG:4326 e protegida pelas regras de tenant existentes;
- implementar finalidade territorial canonica `OPERACAO`, `RELATORIO` e
  `AMBOS`;
- migrar/interpretar todos os setores existentes como `OPERACAO`;
- isolar setores exclusivamente `RELATORIO` dos fluxos operacionais, Mobile,
  monitoramento operacional, geofence operacional e cotas;
- permitir que setores `RELATORIO` nao exijam agente, meta/cota ou tolerancia
  operacional;
- reutilizar a importacao interativa de Shapefile para escolha de finalidade;
- permitir edicao futura de nome, geometria e finalidade sem reclassificar
  silenciosamente dados historicos;
- associar coleta ao setor;
- calcular progresso por setor;
- alertar cota atingida;
- dashboard de cobertura territorial.

### Mapas Estrategicos

Modulo futuro aprovado, nao concluido:

- Cobertura das Coletas;
- Resultado por Setor;
- Lideranca por Setor;
- Distribuicao de Coletas;
- escolha de setores;
- filtros;
- previa;
- Relatorio Executivo de Mapas.

Classificacao espacial futura:

- realizar no Backend/PostGIS;
- preferir `ST_Covers`;
- usar localizacao inicial da coleta como referencia principal;
- usar localizacao final como fallback;
- classificar como `SEM_SETOR` quando a coleta nao estiver em nenhum setor
  analitico;
- resolver sobreposicao/conflito sem duplicar entrevistas.

### Exportação PDF

- relatório com capa;
- gráficos;
- resumo executivo;
- filtros aplicados;
- anexar mapa de cobertura.

## 5. Futuro

- semântica de opções e candidato canônico;
- Potencial de Crescimento;
- Consolidação da Base;
- Resistência;
- Indecisos;
- Tracking Inteligente;
- Heatmap.
- Cluster de marcadores.
- Portal do cliente.
- Branding por empresa.

## 6. Débitos técnicos conhecidos

| Débito | Risco | Prioridade |
|---|---:|---:|
| Contrato `lng/lon` inconsistente | bugs de mapa/mobile | Alta |
| Setores alternando `geometria/poligono` | bugs de renderização | Alta |
| Mobile não revalidado pós-multitenancy | vazamento operacional | Alta |
| Geocoding órfão/parcial | endereço ausente | Média |
| Poucos testes automatizados | regressão | Alta |
| Scripts seed no repo | sujeira operacional | Baixa |
| Deletes físicos em setores | perda histórica | Média |

## 7. Não fazer agora

- Reescrever backend inteiro.
- Migrar stack.
- Chamar OpenStreetMap em massa na tela.
- Criar tempo real antes do monitoramento básico estar estável.
- Mudar contratos validados sem versionamento.

## 8. Critério para próxima versão estável

- Backend com testes mínimos.
- Web sem erros no console nos fluxos principais.
- Mobile sincronizando com agente real.
- Documentação atualizada.
- Checkpoint/tag nos três repositórios.
- Matriz Empresa A x Empresa B validada.

## 9. Cruzamentos Estratégicos

Concluído neste marco:

- motor descritivo de 2 a N dimensões, com limites de segurança e isolamento por tenant;
- endpoint de opções e cardinalidade para configuração da seleção;
- configuração de papel e metadados analíticos na Web;
- seleção ordenada, filtros por resposta e tratamento de “Sem resposta”;
- categorização compartilhada de respostas espontâneas, sem fuzzy matching;
- modos Explorar e Relatório, gráficos, detalhes responsivos e impressão A4 pelo navegador.

Permanece para evolução futura:

- interpretação estratégica assistida e indicadores eleitorais derivados;
- persistência de análises e acompanhamento histórico inteligente;
- exportação PDF dedicada no backend, se necessária;
- revisão da granularidade da categorização espontânea, hoje vinculada à pesquisa.

O resultado atual é evidência descritiva. Ele não afirma causalidade nem produz
interpretação eleitoral automática.

## Entregue — Cadastro por convite e ativação de conta (ADR-035)

`POST /usuarios/` sem senha cria conta inativa e envia convite; o usuário define
a primeira senha em `/ativar-conta` (token de uso único, 24h). Política mínima
de senha centralizada em `core/password_policy.py`.

### Próximas etapas desta linha

- **RBAC completo** (matriz Gerente/Coordenador/Supervisor/Cliente/Agente) — a
  regra de perfil atual na criação foi **preservada**, não ampliada.
- **Recuperação de senha** ("esqueci minha senha") — reutilizar
  `password_policy` e o mesmo padrão de token com hash.
- Aplicar a política de senha também aos fluxos administrativos de troca/reset
  (hoje seguem a validação anterior).
- Auditoria (`audit_events`), notificação de acesso, rate limiting e MFA.

## Entregue — Documentação OpenAPI restrita por ambiente (ADR-038)

`APP_ENV=production` faz o FastAPI não registrar `/docs`, `/redoc` e
`/openapi.json`. Default `development` preserva o Swagger local.

### Pendências desta linha

- **Definir `APP_ENV=production` no deploy** — sem isso a documentação continua
  pública. É o único passo operacional desta entrega.
- Reverse proxy: se um Nginx próprio passar a ser versionado, adicionar
  `return 404` para as três rotas como defesa em profundidade.
- Próximas fases de segurança: audit_events, rate limiting, MFA, CSP/headers.

## Entregue — Auditoria central (ADR-039)

`audit_events` alimentada no Backend: login (inclusive conta inativa), token
(expirado / inválido / rejeitado), RBAC, ACL (com cross-tenant), acesso a
projeto (deduplicado por 15 min), ativação e mudança de ACL. Leitura em
`GET /admin/auditoria/eventos` — Superadmin global, Gerente só o próprio
tenant — com filtros por período, evento, severidade, usuário, projeto e IP,
página máxima de 100 e ordenação `occurred_at DESC`. `X-Request-ID` em toda
resposta. Índice composto para a deduplicação na migration `a4b5c6d7e8f9`.

## Entregue — Notificação de acesso ao projeto (ADR-040)

`PROJECT_ACCESS` persistido (fora da janela de 15 min) dispara e-mail ao
Gerente responsável (`projetos.coordenador_id`), com resultado auditado em
`PROJECT_ACCESS_NOTIFICATION_SENT / SUPPRESSED / FAILED`. Autoacesso do Gerente
é suprimido; Superadmin notifica; falha de SMTP nunca bloqueia o acesso. Sem
migration, sem fila, sem retry, Web e Mobile intocados.

Próximo desta linha: preferências de notificação (por projeto/Gerente, resumo
diário) e retry idempotente de SMTP.

## Entregue — Painel administrativo de segurança (ADR-041)

`GET /admin/auditoria/resumo` (agregação SQL: totais, top IPs, contas mais
tentadas, série temporal) + rota Web `/admin/seguranca` para Gerente (próprio
tenant) e Superadmin (global), com cards, gráfico, rankings, tabela paginada
(máx. 100), filtros e detalhe do evento. Só observação: nenhuma ação
automática ou manual de bloqueio. Sem migration.

Próximos desta linha (não implementados): rate limiting, bloqueio de IP/usuário,
alertas de brute force, MFA/CAPTCHA, exportação CSV/PDF, retenção/purge, SIEM.

### Próximas fases desta linha (não implementadas)

- ~~Notificação ao Gerente quando um projeto for acessado~~ — entregue na ADR-040.
- ~~Painel administrativo de segurança~~ — entregue na ADR-041.
- Indicadores/detecção de comportamento suspeito (ex.: N `LOGIN_FAILED` por IP).
- Retenção/expurgo da trilha por política — hoje é append-only sem limite.
- `AUDIT_TRUST_PROXY=true` + `--proxy-headers` quando houver reverse proxy declarado.
# Sprint 0 — Fundação da Modularização

- [x] Prompt 01 — Catálogo + Entitlements: implementação, migration, testes de domínio e regressão validados em runtime Windows temporário.
- [x] Prompt 02 — Enforcement Backend: gates reutilizáveis e G01–G26 validados, sem ativar rotas produtivas.
- [x] Prompt 03 — Contexto/Gates Web: infraestrutura de capabilities no Web com consulta a `GET /usuarios/me/modulos/`, estado Zustand (idle/loading/ready/error), ModuleGate/FeatureGate/ModuleRoute com fail-closed, limpeza de estado em logout/troca de usuário, menu modular, sem ativar módulos legados. Build e lint validados; QA manual documentada.
- [x] Prompt 04 — Administração de Licenças Backend/Web, auditoria e testes.
- [x] Prompt 05B/05C/05D — QA/Hardening e baseline final: ciclo PostgreSQL real
  completo, migrations históricas simétricas, segurança administrativa,
  regressões Backend/Web e fail-closed executável validados.

O Prompt 01 não implementa gating nem o motor de Potencial de Crescimento.
O ciclo completo também foi validado em PostgreSQL real descartável; nenhum
banco DEV compartilhado ou produção foi acessado. Sprint 0 encerrada.

# Inteligência Eleitoral — MVP 1: Potencial de Crescimento

Sequência aprovada:

- [x] Prompt 01 — Modelagem metodológica + contrato de domínio: **APROVADO**
  como baseline metodológica do MVP 1 (decisões D01–D15 registradas em
  `doc/14-inteligencia-eleitoral-potencial-crescimento-modelagem.md`).
- [x] Prompt 02 — Modelo de configuração da análise: contrato
  `GrowthAnalysisConfiguration` + validator em duas camadas em
  `pesquisa360/inteligencia_eleitoral/`, com testes C01–C65 e documentação em
  `doc/15-inteligencia-eleitoral-potencial-crescimento-configuracao.md`.
  Sem migration, sem API, sem persistência, sem motor; feature
  `potencial_crescimento` segue inativa.
- [x] Prompt 03 — Motor estatístico / cálculo: `analyze_growth_potential`
  em `pesquisa360/inteligencia_eleitoral/` (results/statistics/engine),
  consumindo o contrato validado; universos explícitos, elegibilidade por
  ballot mode, segmentação configurada, território pela regra oficial,
  sinais com denominadores explícitos, Wilson AAS aproximado, snapshot com
  `configuration_hash`/`input_fingerprint`/`engine_version`. Sem API, sem
  persistência, sem score/projeção; feature `potencial_crescimento` segue
  inativa. Documentação:
  `doc/16-inteligencia-eleitoral-potencial-crescimento-motor.md`.
- [x] Prompt 04 — API do produto: três rotas sob
  `/projetos/{id}/pesquisas/{id}/inteligencia-eleitoral/potencial-crescimento`
  (opções, validação-formulário, análise síncrona), gates de segurança na
  ordem 404→403 RBAC→comercial, DTOs HTTP com JSON numbers (Decimal
  permanece no domínio) e 422 tipados. Feature `potencial_crescimento`
  segue INATIVA (API implementada ≠ produto liberado). Documentação:
  `doc/17-inteligencia-eleitoral-potencial-crescimento-api.md`.
- [x] Prompt 05 — Experiência Web: rota protegida por Module+FeatureRoute,
  card gated na Central de Inteligência, configurador em 7 etapas orientado
  por `compatible_as` (sem heurística), validação como formulário com dirty
  state, execução da configuração normalizada e resultado básico (funil,
  warnings, diagnostics, segmentos e evidências com denominadores). Web:
  1175/1175 testes, build PASS, lint sem erro novo. Documentação:
  `doc/18-inteligencia-eleitoral-potencial-crescimento-web.md`.
- [x] Prompt 06 — Visualizações e interpretação: visão executiva + barra
  metodológica, leitura por sinal, delta chart e scatter escala×diferença
  (delta cru, zero line, sem quadrantes), comparação com IC de Wilson,
  interpretação determinística sem LLM (ADR-073), ordenação explícita,
  filtros só de visualização, leitura territorial antiagregação com mapa de
  SETOR (município adiado por contrato de geometria). Web 1229/1229, build
  PASS, lint sem erro novo. Documentação:
  `doc/19-inteligencia-eleitoral-potencial-crescimento-visualizacoes.md`.
- [x] Prompt 07 — QA metodológico, estatístico e E2E real: oráculos manuais
  independentes (112/112 via HTTP real), Wilson próprio do QA, PostgreSQL/
  PostGIS descartável (borda ST_Covers provada), E2E Web em browser real
  (PASS), gates/entitlement/404-antes-de-403 reais, stress (2.400 coletas /
  100 segmentos ~260 ms), determinismo/fingerprint. Correções Q1/Q2
  pré-existentes no Web (crash da ProjectsPage; deep link em rotas gated) +
  tipagem Recharts; achado F4 (build sem typecheck; 38 erros TS latentes)
  endereçado ao Prompt 08. GO — doc/20.
- [x] Prompt 08 — Hardening e checkpoint final: typecheck real do Web
  religado (`tsc -b` no build, ADR-074) com os 39 erros TS de baseline
  quitados mecanicamente sem flexibilizar tsconfig; fixes F1/F2/F3 sob
  guarda estática (`tests/hardening.test.mjs`); QA promovido a ferramenta
  permanente (`scripts/qa/growth_qa_{seed,run}.py`, dados sintéticos, env
  obrigatória) e E2E opt-in com credenciais via env; varreduras de
  segurança/linguagem limpas; regressão definitiva (Backend verde em 3.13;
  Web typecheck 0 erros, 1233/1233, build, lint sem erro novo); commits
  auditáveis e checkpoint em
  `doc/21-checkpoint-mvp1-potencial-crescimento.md`.
  **Status: MVP TECNICAMENTE VALIDADO / FEATURE INATIVA.** Evoluções
  futuras (ponderação, comparação temporal, mapa de município, persistência
  de execuções, ativação comercial) permanecem NÃO iniciadas e dependem de
  decisão de produto.

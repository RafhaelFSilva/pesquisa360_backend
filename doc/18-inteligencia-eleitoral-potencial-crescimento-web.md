# INTELIGÊNCIA ELEITORAL
# MVP 1 — POTENCIAL DE CRESCIMENTO
# EXPERIÊNCIA WEB

**Data:** 2026-09-02
**Status:** Prompt 05 implementado — experiência Web funcional ponta a ponta
(configuração → validação → execução → resultado básico). Backend intocado
nesta rodada; a feature `potencial_crescimento` permanece INATIVA no catálogo
real (a rota Web é fail-closed e não abre sem a capability).

## 1–2. Objetivo e arquitetura

Primeira experiência Web do Potencial de Crescimento, consumindo exatamente a
API do Prompt 04 (doc/17), sem duplicar regras do Backend e sem heurística.
Arquitetura (repo `pesquisa360-web`):

```text
src/types/growthPotential.ts        DTOs espelhando o Backend (snake_case, sem any)
src/api/growthPotentialService.ts   getConfigurationOptions / validateConfiguration / analyze
src/lib/growthPotential.ts          LÓGICA PURA: draft, reducer, payload, issues locais,
                                    formatadores, copy estruturada (testável no harness)
src/pages/GrowthPotentialPage.tsx   página (etapas 1–7 + resultado)
```

Estado: **local da feature** via `useReducer(growthPageReducer)` — nada no
`modulesStore` (que segue só com capabilities), nada em store global, nada em
localStorage/sessionStorage (execução efêmera). Justificativa: configuração e
resultado pertencem à Pesquisa aberta e não sobrevivem a logout/troca.

## 3–5. Rota, gates e menu

- Rota: `/projetos/:projectId/pesquisas/:surveyId/inteligencia-eleitoral/potencial-crescimento`,
  registrada em `App.tsx` DENTRO do `ModuleRoute(inteligencia_eleitoral)` e de
  um `FeatureRoute(potencial_crescimento)` (primeiro uso real do FeatureRoute
  da Sprint 0) — loading = fail-closed, error/não-ready = redirect;
- Card na Central de Inteligência (`SurveyIntelligencePage`) renderizado
  apenas com `status === 'ready' && hasFeature(...)`; sem a capability, a
  funcionalidade não aparece como utilizável;
- Frontend é UX gate; o Backend continua a autoridade (404/403 comerciais).
- Nenhum `company_id`/tenant em service, payload, tipos ou página (testado).

## 6–15. Configuração (etapas)

1. **Candidatura e cenário** — label/cargo; perguntas de intenção filtradas
   por `compatible_as: INTENTION` (badge "Sugerida como intenção de voto"
   quando `analytic_role=INTENCAO_VOTO` — sugestão, nunca seleção automática);
   `BallotSelectionMode` SINGLE/MULTIPLE/ORDERED_MULTIPLE (ordered gera slots
   `VOTO_n` + order pela ordem de seleção; sem pressupor "Senado = 2");
   bindings explícitos: checkboxes dos valores oficiais por pergunta. Não
   existe dropdown fictício de "candidatos do sistema".
2. **Universo elegível** — texto fixo: apoiadores atuais são excluídos
   (invariável D01, sem toggle); painel de classificação explícita dos
   valores da intenção em Indeciso/Branco-Nulo/NS-NR/Não pretende (selects,
   sem heurística — preferência do Prompt: nenhuma sugestão automática);
   4 checkboxes visíveis de include/exclude (defaults visíveis e editáveis:
   indeciso e branco/nulo incluídos).
3. **Sinais** — Rejeição/Segunda opção/Decisão do voto como enriquecimento
   opcional; perguntas filtradas por `compatible_as`; bindings da candidatura
   exigidos para os candidato-específicos; rejeição múltipla mostra o aviso
   dos >100%; decisão do voto classifica valores em Móvel/Cristalizado
   (não classificados ficam fora da base válida — explicado); sinal
   espontâneo exige `max_uncategorized_rate` **em percentual 0–100%** (a lib
   converte para fração 0–1 no payload; SEM default).
4. **Segmentação** — 1–2 dimensões; perguntas por `compatible_as`
   (PROFILE_CATEGORICAL/NUMERIC); categórica gera grupos determinísticos a
   partir dos valores oficiais (key = slug estável do rótulo, editável sem
   troca silenciosa); numérica edita faixas (min/max inclusivos, máx vazio =
   sem teto; inversão detectada localmente — Backend continua autoridade).
5. **Território** — NONE/SETOR/MUNICIPIO (município desabilitado quando a API
   informa indisponível); setores apenas os da API, `OPERACAO`-only marcados
   e não selecionáveis analiticamente; distinção explícita entre "usar
   território como dimensão do resultado" e "restringir universo aos setores
   escolhidos" (filtro estrutural); checkbox de contexto eleitoral com o
   texto obrigatório "Não representa projeção de votos".
6. **Políticas** — suppress/warn SEM defaults (validação local: suppress ≥ 1,
   warn > suppress; texto explica que n = entrevistas reais); cards fixos de
   Ponderação ("Não ponderado" + alerta), Referência ("Universo elegível") e
   Incerteza ("IC de Wilson 95% — aproximação AAS", sem "95% de certeza").
7. **Revisão e validação** — issues locais bloqueiam o CTA; `Validar
   configuração` chama a API; a página diferencia ERRO/ALERTA/INFORMAÇÃO por
   ícone+rótulo+texto (nunca só cor); errors preservam `code`/`path` e são
   rotulados pela seção (`issueSection(path)`); warnings separados;
   `VALUE_NORMALIZED` gera aviso discreto ("valores normalizados para os
   rótulos oficiais"); `valid=true` guarda a `normalized_configuration` e
   mostra o resumo (candidatura/cenário/sinais/segmentações/território/base/
   referência/ponderação).

## 16–17. Estados, dirty e execução

Estados explícitos: OPTIONS_LOADING/ERROR, CONFIGURING, VALIDATING,
VALIDATION_INVALID, VALIDATED, ANALYZING, ANALYSIS_READY, ANALYSIS_ERROR —
sem `loading` único. Fluxo: draft → validar → **normalized_configuration** →
analisar (o draft bruto nunca é executado). Qualquer edição após validado
marca `dirty`, limpa a normalizada e desabilita Analisar até nova validação.
"Analisar" mostra "Calculando segmentos e evidências..." (sem fake progress,
sem "prevendo votos"). Erros: 422 tipados (GROWTH_CONFIGURATION_INVALID,
SEGMENT_LIMIT_EXCEEDED) com copy própria; 403/404 fail-closed; 500 genérico.

## 18–25. Resultado básico

- Cabeçalho: candidatura, cenário, engine_version, executed_at; hashes
  (configuration_hash/input_fingerprint) em `<details>` "Detalhes técnicos";
- **Funil analítico** com contagens explícitas do universe summary (total →
  analítico → excluídos/sem intenção → elegível), sem percentuais inventados;
- **Limitações metodológicas**: todos os warnings do motor com copy
  estruturada (UNWEIGHTED_ANALYSIS, SRS_ASSUMPTION,
  REFERENCE_INCLUDES_SEGMENT, MULTIPLE_COMPARISONS_EXPLORATORY etc.);
- **Qualidade e cobertura dos dados**: diagnostics territoriais e coverage,
  separados dos segmentos de oportunidade;
- **Segmentos**: tabela na ORDEM RECEBIDA (sem ranking, sem sort default),
  colunas apenas dos sinais configurados; suprimido = "Base insuficiente
  para apresentar a métrica" (nunca 0%); `weighted_base` null não é exibido
  como 0; eleitorado apto em coluna própria "Contexto territorial" com
  tooltip "não é projeção da amostra";
- **Evidência expandível** por segmento: numerador/base, taxa e intervalo do
  SEGMENTO e da REFERÊNCIA, delta pp, lift e direção observada traduzida
  ("Sinal relativamente favorável/desfavorável", "Sem diferença observada" —
  nunca "significativo/comprovado"). Nenhuma narrativa automática (Prompt 06).

Formatadores centralizados (`lib/growthPotential.ts`): `formatRate`
(0.187→"18,7%"), `formatDeltaPp` (7.7→"+7,7 pp"; **nunca ×100**), `formatLift`
(1.8→"1,80×"), `formatCount`, `formatInterval` (0–1→"12,0%–25,0%");
null → "—" (nunca 0).

## 26–28. Segurança, troca de Pesquisa e acessibilidade

- Troca de projectId/surveyId: `RESET` total (options/draft/validação/
  resultado) + guarda de corrida por (sequence, surveyKey) — resposta
  atrasada da Pesquisa A nunca aparece na B (testado);
- desmontagem/logout: estado é local (useReducer), nada resta em store
  global; capabilities continuam sendo limpas pelo fluxo auth existente;
- acessibilidade: labels/fieldset+legend, `aria-expanded` nos expansores,
  severidade por ícone+rótulo+texto, inputs com aria-label, navegação por
  teclado nos controles nativos.

## 29–30. Testes e QA manual

Automatizados (harness real do projeto, `node --test`):
- `tests/growthPotential.test.mjs` — 27 testes de lógica pura (payload,
  ballot modes, taxonomia, sinais, espontânea %→fração, perfil, base mínima,
  reducer/dirty/race/reset, formatadores W61–W67/W71–W73, funil, provas
  negativas de copy);
- `tests/growthPotentialPage.test.mjs` — 29 verificações estáticas (service/
  paths, gates de rota, card gated, estados da página, compatible_as sem
  heurística, copy metodológica obrigatória, denominadores no resultado,
  diagnostics, efemeridade, erros HTTP, sem score/ranking/projeção, tipos sem
  `any`).
Suíte Web completa: **1175 passed, 0 fail** (baseline 1119 + 56); build de
produção PASS; lint com os mesmos 10 erros preexistentes (zero novo).
Ajuste pontual em `tests/leadershipEntrypoint.test.mjs`: duas contagens que
congelavam "exatamente 2 cards" na Central passaram a `>= 2` (a intenção do
teste — mesmo padrão visual/grid — foi preservada; a Central ganhou o card
gated de Potencial de Crescimento).

QA manual (navegador, exige banco LOCAL/descartável com a feature ativada e
entitlement de teste — nunca produção/staging/seed; descartar ao final):
1. mínimo: projeto→pesquisa→card visível→opções→intenção+binding→perfil→
   políticas→validar→analisar→funil+segmento+evidência;
2. enriquecido: + rejeição múltipla (aviso >100%), segunda opção, decisão do
   voto, território SETOR/MUNICIPIO + contexto eleitoral;
3. erros: configuração inválida (200 valid=false com campos), base pequena
   (supressão), nulls ("—"), pesquisa sem segunda opção (segue possível),
   feature indisponível (rota não abre; card ausente), erro de API (alerta).

## 31. Fronteira com o Prompt 06

Este Prompt entregou a experiência funcional validada (configuração →
validação → execução → leitura tabular/estruturada). O Prompt 06 implementou
sobre ela a camada analítica visual — visão executiva, seletor de sinal,
gráficos delta/escala, interpretação determinística, leitura territorial com
mapa de setor — conforme
`doc/19-inteligencia-eleitoral-potencial-crescimento-visualizacoes.md`,
sempre sem score, ranking automático ou projeção.

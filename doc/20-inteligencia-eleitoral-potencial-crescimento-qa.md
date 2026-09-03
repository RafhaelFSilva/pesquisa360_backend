# INTELIGÊNCIA ELEITORAL
# MVP 1 — POTENCIAL DE CRESCIMENTO
# QA METODOLÓGICO, ESTATÍSTICO E END-TO-END

**Data:** 2026-09-02
**Status:** Prompt 07 executado — validação INDEPENDENTE (oráculos manuais,
Wilson próprio do QA, PostgreSQL/PostGIS real, HTTP real, browser real).
**Regra central respeitada:** nenhum valor esperado veio da implementação;
o quadro oráculo foi somado à mão e o Wilson de referência foi implementado
no próprio QA (com âncoras de literatura), sem importar `statistics.py`.

## 1–3. Baseline e ambiente

Baselines auditadas e preservadas (Backend `3e632ab…` + trabalho P01–P04;
Web `e96d10d…` + P05–P06; Mobile limpo). Runtime: Python 3.13.14 (venv de
validação), Node do projeto. **PostgreSQL/PostGIS descartável** via Docker
(`postgis/postgis:16-3.4`, porta 55432, banco `p360qa`): `alembic upgrade
head` aplicado com head único `c6d7e8f9a0b1`. Backend real via uvicorn
(porta 8055; segunda instância 8056 com `P360_GROWTH_MAX_SEGMENTS=50` para o
teste de limite). Web real via `vite dev` apontando `VITE_API_URL` ao
backend QA; browser Chrome headless com CDP (harness E2E do projeto).

**Feature/entitlement**: o catálogo nasceu ESPELHANDO o real (módulo ativo,
`potencial_crescimento` INATIVA — verificado pós-migração). A ativação e o
entitlement (Empresa QA A) foram feitos SOMENTE no banco descartável,
durante o ciclo de teste do gate; Empresa QA B ficou sem entitlement.
Catálogo/seed/migrations do projeto NÃO foram alterados. Ambiente destruído
ao final (container removido).

## 4. Dataset oráculo

`qa_seed.py` (scratchpad de QA) documenta entrevista a entrevista o quadro
oráculo — Pesquisa A1 (20 coletas, cenário SINGLE, alvo "Candidato A"):
apoiadores (2), adversários B/C (11), indeciso (2), branco/nulo (1),
NS/NR (1), não pretende votar (1), sem resposta de intenção (2); sexo/idade;
segunda opção com rótulo distinto ("A. da Silva"); rejeição múltipla com
rótulo distinto ("Fulano A/B/C"), incluindo tripla (9005) e duplicada
(9008); decisão do voto com valor não classificado ("Talvez"); espontânea
com mapeada/CAIXA-ALTA/não categorizada; pontos em 2 setores + borda exata +
sobreposição + fora + sem coordenada; 2 municípios (Macapá/Santana) via
Base Eleitoral VALIDADA. Pesquisa A2 (6 coletas) cobre MULTIPLE/ORDERED e o
conflito de categorias especiais; Pesquisa A3 (2.400 coletas) cobre stress;
Pesquisa B1 pertence ao tenant B.

**Correção de oráculo durante o QA** (registrada): a primeira soma manual da
segunda opção esqueceu a resposta da coleta 9010 — a recontagem manual
(base 9, favoráveis 5; FEM 3/4, MASC 2/5) confirmou o motor. Não é bug do
produto; o quadro foi corrigido.

## 5–20. Matriz de QA metodológico (regra → oráculo → resultado)

Suite `qa_run.py` via HTTP real: **112/112 PASS**. Destaques:

| Regra | Oráculo manual | Obtido | Status |
|---|---|---|---|
| survey/analytical | 20/20 | 20/20 | PASS |
| apoiadores excluídos (D01) | 2 | 2 | PASS |
| sem resposta ≠ indeciso | 2 TECHNICAL_MISSING | 2 (INDECISO=2 só 9014/15) | PASS |
| especiais excluídas (policy) | 2 (NS/NR, NPV) | 2 | PASS |
| universo elegível | 14 | 14 | PASS |
| participação usa eligible_n | FEM 8/14 (≠8/20) | 8/14 | PASS |
| 2ª opção ref | 5/9 | 5/9 | PASS |
| 2ª opção FEM | 3/4=75%; +19,44 pp; lift 1,35; FAVORABLE | idem | PASS |
| rejeição múltipla = entrevista | base 7 (não 10 respostas); tripla conta 1; dup conta 1 | idem | PASS |
| rejeição ref / FEM / MASC | 3/7; 2/4 (+7,14 UNFAV); 1/3 (−9,52 FAV, delta cru) | idem | PASS |
| taxa de rejeição nunca invertida | 1/3, não 2/3 | 1/3 | PASS |
| decisão: não-classificado fora da base | ref 3/6 ("Talvez" fora) | 3/6 | PASS |
| MULTIPLE: alvo em qualquer posição | 1 apoiador, 1 elegível | idem | PASS |
| conflito especial divergente | exclusão conservadora + MIXED_SPECIAL_ELIGIBILITY | idem | PASS |
| ORDERED: alvo em qualquer slot | 2 apoiadores, 1 elegível | idem | PASS |
| base mínima por segmento e por sinal | (coberto nos testes E49–E53 do motor; políticas sintéticas 1/2 no E2E) | — | PASS |
| zero real vs ausência | rejeição-C MASC 0/3 → rate 0.0; pergunta sem respostas → SIGNAL_UNAVAILABLE, rate null | idem | PASS |
| lift ref=0 | null + LIFT_UNDEFINED_REFERENCE_ZERO (nunca Infinity/0) | idem | PASS |
| espontânea (limiar) | 1/4=0,25; t=0,5 e t=0,25 usável (borda: igual NÃO excede); t=0,2 → SIGNAL_UNAVAILABLE_QUALITY + HIGH_UNCATEGORIZED_RATE | idem | PASS |
| bindings com rótulos distintos | "Candidato A"/"Fulano A"/"A. da Silva" funcionam; binding removido → MISSING_TARGET_BINDING (sem fallback por nome); analisar → 422 tipado | idem | PASS |

## 6–18. Matriz de QA estatístico

| Métrica | Input | Esperado (independente) | Obtido | Tolerância | Status |
|---|---|---|---|---|---|
| Wilson (âncoras literatura) | 5/10; 0/10; 10/10 | (0,2366; 0,7634); high 0,2775; low 0,7225 | idem | 2e-3 | PASS |
| Wilson (todas as 12 taxas do oráculo) | seg+ref de 3 sinais × 2 segmentos | fórmula própria do QA | idem | 1e-9 | PASS |
| delta_pp | 0,75 vs 5/9 | +19,444 pp | idem | 1e-6 | PASS |
| lift | 0,75 ÷ 5/9 | 1,35 | 1,35 | 1e-9 | PASS |
| referência inclusiva | base ref = FEM+MASC | 9 = 4+5 | 9 | exata | PASS |
| sem significância | payload completo | ausência de significan*/p-value | ausente | — | PASS |
| cotas ≠ pesos | plano+cota meta 99999 | resultado idêntico byte a byte (exceto executed_at) | idêntico | — | PASS |
| eleitorado ≠ peso | eleitorado 50k→999999 | taxas/delta iguais; só contexto muda | idem | 1e-9 | PASS |

Determinismo/reprodutibilidade: duas execuções idênticas exceto
`executed_at`; hash de configuração estável e sensível a mudança de config;
`input_fingerprint` INSENSÍVEL a resposta de pergunta não-analítica (política
doc/16 confirmada) e SENSÍVEL a resposta analítica (com reversão provada).

## 20–21. Território em PostGIS real

- **Borda**: `ST_Covers(S1, POINT(0 0.5)) = true` com `ST_Contains = false`
  provado por SQL direto — a semântica correta do produto inclui o ponto de
  borda (coleta 9006 conta no segmento FEM|S1, n=6).
- Findings SETOR = {FEM|5001:6, MASC|5001:1, MASC|5002:3, FEM|5003:1};
  diagnostics sobreposição/sem setor/sem coordenada = 1/1/1; coverage 3;
  soma dos segmentos (11) + não segmentados (3) = elegível (14).
- MUNICÍPIO = {FEM|900:6 (+eleitorado 50.000), MASC|900:1, MASC|901:3
  (30.000)}; setor sem município → `municipio_nao_resolvido = 1`. Análise
  por município FUNCIONA no motor/API/Web tabular; apenas o MAPA de
  município permanece adiado (gap de geometria no contrato Web — limitação
  aceita, sem endpoint novo).

## 25–26. Matriz E2E (HTTP real + segurança)

| Cenário | Resultado | Status |
|---|---|---|
| feature inativa → GET options | 404 CAPACIDADE_INDISPONIVEL | PASS |
| ativa sem entitlement | 403 | PASS |
| entitlement Empresa A | 200 | PASS |
| Agente (sem INTELIGENCIA_VER) | 403 RBAC | PASS |
| B pede recurso de A (sem entitlement B) | **404 antes de 403 comercial** | PASS |
| B na própria pesquisa | 403 comercial | PASS |
| pesquisa inexistente/fora do projeto/id negativo | 404 | PASS |
| company_id/tenant_id/campo desconhecido no body | 422 | PASS |
| JSON malformado / enum inválido | 422 | PASS |
| pergunta do tenant B | QUESTION_NOT_FOUND (sem vazar existência) | PASS |
| setor do tenant B | SETOR_NOT_FOUND | PASS |
| lista de 500 valores | 200 valid=false (sem 500) | PASS |
| SEGMENT_LIMIT (servidor com limite 50, teórico 500) | 422 SEGMENT_LIMIT_EXCEEDED | PASS |

Options auditadas: pergunta TEXTO com texto enganoso ("Qual candidato você
rejeita?") sem NENHUMA compatibilidade (sem heurística); espontânea lista só
categorias ativas; setor OPERACAO não-analítico; municípios oficiais;
constraints sem defaults metodológicos.

## 27–30. E2E Web real (browser, sem mocks)

`tests/growthPotential.e2e.cdp.mjs` — **PASS** (Chrome headless + CDP + dev
server + backend QA): login real A → card gated visível → opções →
configuração completa na UI (bindings, taxonomia explícita por seleção,
segunda opção + rejeição com aviso de >100%, dimensão Sexo, políticas) →
validar → analisar → **funil 20/20/2/2/2/14 idêntico ao oráculo** → barra
metodológica e warnings visíveis → gráficos delta/scatter presentes →
ordenação default "ordem original" → detalhe do segmento com os NÚMEROS do
oráculo na tela (75,0%; 3 / 4; +19,4 pp; rejeição crua 50,0%; 2 / 4) → sem
linguagem proibida → viewports 1440 e 900 px sem scroll horizontal → troca
A→B real (logout/login): B bloqueado no hub e na rota do Potencial
(fail-closed), sem resíduo de resultado de A → **zero respostas HTTP 4xx/5xx**
no fluxo. QA visual desktop/intermediário: PASS pelos mesmos passos
(hierarquia, campos, erros, warnings, funil, gráficos, detalhes, tabela,
scroll). Dark mode: N/A (aplicação não possui tema escuro). Acessibilidade
básica: labels/fieldsets/aria verificados nos testes estáticos do P05/P06;
navegação por controles nativos exercitada no E2E.

## 31–32. Stress e performance

Tempos HTTP reais (síncrono, ADR-070 mantida): oráculo 20 coletas ~60 ms;
território setor/município 78/96 ms; **2.400 coletas / 25 segmentos ~200 ms;
2.400 coletas / 100 segmentos (25×20 observados) ~260 ms**. Limite
computacional exercitado (422 tipado). N+1: comportamento não-linear por
segmento já provado por contador de queries no teste E24 do motor;
coerente com os tempos medidos (4× segmentos ≈ +30% tempo). Nenhuma
evidência que justifique arquitetura assíncrona.

## 33–34. Linguagem e provas negativas

Varredura exaustiva (produto + docs 14–19): todas as ocorrências de termos
proibidos estão em contexto de PROIBIÇÃO/teste negativo; zero uso indevido.
Payloads reais sem score/ranking/projeção/significância (checado no JSON de
análise byte a byte).

## 35–36. Achados e correções

| # | Classe | Achado | Ação |
|---|---|---|---|
| F1 | **Q1** (pré-existente, Sprint 0 `e96d10d`) | `ProjectsPage` referencia `user` sem definição no escopo (a única definição foi para o `Header` extraído) → **crash de runtime da página inicial** para qualquer usuário; jamais detectado porque o script de build roda `tsc` bare sobre tsconfig solution-style (`files: []`) que não typechecka nada, e o E2E manual da Sprint 0 ficou pendente | **Corrigido** (1 linha: `const user = useAuthStore(...)` na página); suíte/build/lint verdes |
| F2 | **Q2/Q7** (pré-existente, Sprint 0) | `ModuleRoute`/`FeatureRoute` redirecionam no status `'idle'` (primeiro render antes de o bootstrap disparar `loadModules`) → **deep link expulsa usuário licenciado** | **Corrigido**: `'idle'` aguarda como `'loading'`; `'error'`/ready-sem-capability continuam fail-closed; provado no E2E (deep link A entra; B continua bloqueado) |
| F3 | **Q2** (Prompt 06) | Tipagem do `onClick` do `Bar` (Recharts v3) incompatível — só visível com typecheck real | **Corrigido** (cast estruturado, sem `any`) |
| F4 | **Q8/Q10** | `npm run build` **não typechecka** (`tsc` bare + solution-style); typecheck real (`tsc -p tsconfig.app.json`) revela **38 erros TS pré-existentes** em arquivos da baseline (ElectoralWeightMap 9, DashboardInteligencia 7, ReportComponents 5, StrategicMapsReportPage 4, …), nenhum na feature | **Não corrigido nesta rodada** (fora de escopo de QA); recomendação registrada para o Prompt 08: `tsc -b` no build + quitação do débito |
| F5 | Q9/Q10 | `TokenData` usa `EmailStr` e o email-validator rejeita domínios especiais (`.local`): login passa mas `get_current_user` invalida — descoberto com usuários sintéticos de QA | Sem mudança de produto (emails reais não usam `.local`); registrado |
| F6 | QA-interno | Soma manual do oráculo da 2ª opção esquecera a coleta 9010 | Quadro corrigido; motor confirmado |

Nenhum achado Q3 (estatístico), Q4 (metodológico) ou Q5 (segurança).

## 37–39. Riscos residuais e limitações aceitas

- **Mapa Web de MUNICÍPIO**: adiado por contrato de geometria ausente
  (análise municipal funciona; leitura tabular disponível).
- **Invalidação de coleta**: inexistente por decisão D13; confirmado que o
  produto não afirma excluir entrevistas inválidas.
- **Ponderação**: inexistente (D11); aviso ANÁLISE NÃO PONDERADA visível em
  todos os níveis; `weighted_base` null fim-a-fim (verificado).
- **Débito TS latente (F4)** e build sem typecheck — para o Prompt 08.
- Restrição ambiental Windows/Python 3.14 (WinError 50) permanece.

## Validade interna × validade externa

**VALIDADE INTERNA: APROVADA** — os cálculos refletem exatamente os dados
coletados/configurados (oráculos manuais, PostGIS real, HTTP real, browser
real). **VALIDADE EXTERNA: LIMITADA por construção** — sem ponderação e sem
desenho amostral estruturado, os resultados descrevem a amostra, não a
população; essa limitação é declarada de forma permanente na experiência
(UNWEIGHTED_ANALYSIS + SRS_ASSUMPTION) e nos documentos metodológicos.

## Regressões finais

- Backend (Python 3.13.14): suíte completa verde (baseline 1674+13 — ver
  doc/00; código backend não foi alterado nesta rodada).
- Web: 1229/1229; build PASS; lint com os MESMOS 10 erros preexistentes;
  `git diff --check` limpo; Mobile intacto.

## 40. Decisão

**GO — QA METODOLÓGICO, ESTATÍSTICO E E2E VALIDADO. APTO PARA O PROMPT 08
(HARDENING / CHECKPOINT MVP).** O mapa de Município permanece explicitamente
adiado (limitação aceita, fora do escopo atual); os achados F4/F5 estão
endereçados ao hardening.

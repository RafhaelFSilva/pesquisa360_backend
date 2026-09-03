# CHECKPOINT — INTELIGÊNCIA ELEITORAL — MVP 1 — POTENCIAL DE CRESCIMENTO

**Status: MVP TECNICAMENTE VALIDADO / FEATURE INATIVA.**

## 1. Objetivo do checkpoint

Congelar, de forma auditável e reproduzível, o estado final do MVP 1 do
Potencial de Crescimento após os Prompts 01–08: código, testes, QA
independente, hardening e documentação — SEM ativar a feature, sem deploy e
sem criar entitlement para nenhuma empresa real. Este documento é a
referência para uma futura ativação controlada.

## 2. Data

2026-09-03 (Prompts 01–07 executados em 2026-09-01/02; Prompt 08 em
2026-09-03).

## 3. Baseline Sprint 0

- Backend: branch `feature/mapa-liderancas`, baseline
  `3e632ab96818f9428cfb5bd4cc2b036635f02091`
  (`docs(modules): finalize Sprint 0 validation baseline`).
- Web: branch `feature/mapa-liderancas`, baseline
  `e96d10d1a6a6f89b2b7932ffe9ffa2314e4c3789`
  (`feat(modules): Sprint 0 - Modularizacao, Gates, Admin & QA`).
- Mobile: `cf6687a` (intocado em todo o MVP 1).
- Fundação usada: catálogo `modulos`/`modulo_funcionalidades`, entitlements,
  gates `require_module`/`require_feature`, stores/rotas gated no Web
  (doc/12, doc/13).

## 4. Escopo do MVP

Análise descritivo-comparativa de potencial de crescimento por segmento:
funil de universo (survey → analítico → apoiadores → sem classificação →
especiais excluídos → elegível), sinais SECOND_OPTION, REJECTION e
VOTE_DECISION contra referência ELIGIBLE_UNIVERSE, com IC de Wilson 95%,
segmentação por 1–2 dimensões de perfil + território de SETOR opcional.
Fora do escopo (por contrato): score, ranking, projeção de votos, narrativa
causal, ponderação, comparação temporal, persistência de execuções, mapa de
município (adiado), qualquer ativação comercial.

## 5. Metodologia aprovada

doc/14 (modelagem sobre dados reais). Sinais são leituras descritivas;
rejeição é sinal NEGATIVO (favorável = menor, nunca invertido); referência
inclusiva (contém o segmento); zero real ≠ ausência de sinal;
não-ponderado declarado (`weighted_base = null`, nunca `= n_bruto`).

## 6. Decisões D01–D15

Congeladas em doc/15 (contrato de configuração): exclusão de apoiadores,
taxonomia de intenção com política explícita de elegibilidade por classe
especial, bindings por VALOR explícito (nunca heurística textual), grupos de
decisão declarados, base mínima suppress/warn sem defaults, limiar de
espontânea como fração com `>` bloqueante, Wilson AAS aproximado declarado,
etc. Nenhuma decisão foi alterada nos Prompts 03–08.

## 7. Arquitetura final

Pacote isolado `pesquisa360/inteligencia_eleitoral/` (config, validation,
results, statistics, engine, options, http_contract) + endpoint fino
`api/endpoints/potencial_crescimento.py` + parser compartilhado
`services/response_values.py` (extraído verbatim de
`multidimensional_cross`). Web: página `GrowthPotentialPage` + lib pura
`lib/growthPotential.ts` + componentes `growth-potential/*`, estado local e
efêmero (ADR-072). Nada persiste resultado; execução síncrona (ADR-070).

## 8. Configuração

Contrato Pydantic v2 `extra="forbid"`, códigos de erro tipados
(`"CODE::mensagem"`), validação em duas camadas (transporte 422 vs
metodológica 200 `valid=false`), tenant-safe (404 indistinguível). 63
testes (`test_growth_analysis_configuration.py`).

## 9. Motor

`engine.py`: 1 Coleta = 1 observação; denominadores próprios por sinal;
rejeição múltipla conta ENTREVISTA; Decimal fim-a-fim com Wilson
(`statistics.py`, z de Acklam); `GROWTH_ENGINE_VERSION = "1.0"`;
`configuration_hash`/`input_fingerprint` SHA-256 sobre JSON canônico;
limite `P360_GROWTH_MAX_SEGMENTS` (default 5000) com 422 tipado. 46 testes
(`test_growth_analysis_engine.py`).

## 10. API

Três rotas sob
`/projetos/{id}/pesquisas/{id}/inteligencia-eleitoral/potencial-crescimento/`
(`opcoes-configuracao`, `validar-configuracao`, `analisar`), dependência
composta `growth_access` (recurso-404 → RBAC-403 → `require_feature`),
igualdade path/body (ADR-071), Decimal→float na borda (nunca strings). 36
testes (`test_growth_analysis_api.py`). Contratos em doc/17 e doc/02.

## 11. Web

Configurador em 7 passos (alvo por bindings explícitos, cenário, taxonomia,
elegibilidade, sinais, segmentação, base mínima), estado dirty, validação
prévia obrigatória, resultado com funil/achados/evidências; formatadores
pt-BR (0.187→"18,7%"; delta "+7,7 pp"; null→"—" nunca 0). Race guards por
(sequence, surveyKey); remount por `executed_at`.

## 12. Visualizações

Sumário executivo com barra metodológica permanente, leitura por sinal,
delta chart (linha de zero, rejeição CRUA), scatter escala×diferença (sem
quadrantes), comparação com IC, interpretação DETERMINÍSTICA sem LLM
(ADR-073), ordenação explícita (default ORIGINAL), filtros apenas visuais,
seleção única sincronizada.

## 13. Mapa de setor

Leitura territorial antiagregação (`territoryFindingsForCombination`) com
mapa de SETOR baseado no contrato de geometria existente; cor por delta
centrada em 0; suppressed/null → não plotável (nunca vira 0).

## 14. Mapa de município (adiado)

Sem contrato de geometria de município no Web — adiado com gap registrado
(doc/19); leitura tabular disponível. Nenhuma promessa de prazo.

## 15. Segurança

404-antes-de-403 comprovado (unitário + QA HTTP real); mass assignment
bloqueado (`extra="forbid"` + igualdade path/body); sem `company_id` em DTO;
RBAC `INTELIGENCIA_VER`; varredura estática limpa (sem literal de tenant,
sem bypass). `.env` do backend NÃO é rastreado pelo git (verificado com
`git ls-files --error-unmatch`); nenhum segredo nos commits do MVP.

## 16. Multitenancy

`EntitlementContext` carrega SOMENTE `pesquisa_id` (resolver rejeita escopo
duplo); ACL via `filtro_projeto_acessivel`/`filtro_company_acessivel`;
cross-tenant → 404 idêntico a inexistente (provado no QA com Empresa B).

## 17. Entitlement

Gate real `require_feature('inteligencia_eleitoral','potencial_crescimento')`.
Ciclo completo provado no QA descartável: inativa→404,
ativa-sem-entitlement→403, concedida→200. NENHUM entitlement criado em
banco real.

## 18. Feature status

`potencial_crescimento` semeada `ativo=false` na migration `c6d7e8f9a0b1`
(confirmado estaticamente no Prompt 08) e assim permanece no catálogo real.
Implementado ≠ ativo (doc/12).

## 19. Testes Backend

Suíte completa verde em Python 3.13.14 (venv de validação; Python 3.14
sofre WinError 50 ambiental em subprocess — documentado desde a Sprint 0):
baseline 1674 passed / 13 skipped, incluindo 63+46+36 testes do MVP.
`fixtures_qa` regeneram apenas timestamp e são restauradas após cada rodada.

## 20. Testes Web

`node --test` (sem vitest/jsdom): 1233/1233 no Prompt 08 (1229 do baseline
P07 + 4 guardas de `tests/hardening.test.mjs`). Estilos: transpilação de
lib pura via data:URL e verificação estática de fonte.

## 21. Typecheck

Comando canônico `npm run typecheck` = `tsc -b --pretty false` → **PASS, 0
erros**. Histórico: o `tsc` a seco do build antigo não checava nada
(tsconfig solution-style); os 39 erros pré-existentes de baseline foram
corrigidos mecanicamente no Prompt 08 sem NENHUMA flexibilização de
tsconfig (ADR-074). `tsconfig.node.json` corrigido para checar o
`vite.config.js` real.

## 22. Build

`npm run build` = `tsc -b && vite build && copy-public-assets &&
copy-htaccess` → PASS (typecheck REAL no caminho do build; guarda estática
em `tests/hardening.test.mjs`).

## 23. Lint

`eslint .` → os mesmos 10 erros pré-existentes (2 react-refresh em src, 8
em tests), ZERO novos. Não foram corrigidos por estarem fora do escopo do
MVP; candidatos a faxina futura.

## 24. PostgreSQL/PostGIS

QA em `postgis/postgis:16-3.4` descartável (porta 55432): migrations
`upgrade head` limpas, borda territorial provada com `ST_Covers=true` onde
`ST_Contains=false` (SQL direto), sobreposição e sem-coordenada
diagnosticados. Ambiente destruído ao final; ferramentas permanentes em
`scripts/qa/growth_qa_{seed,run}.py` (dados 100% sintéticos,
`P360_QA_DATABASE_URL` obrigatória).

## 25. E2E real

Browser real via CDP (`tests/growthPotential.e2e.cdp.mjs`, opt-in
`npm run qa:growth-e2e`, credenciais SOMENTE via env): fluxo completo com
funil 20/20/2/2/2/14 e números idênticos ao oráculo na tela, +19,4 pp,
troca de tenant A→B fail-closed, zero 4xx/5xx. PASS no Prompt 07.

## 26. Performance

Baseline observado (não é SLA): oráculo 20 coletas ~60 ms; 25 segmentos
~200 ms; stress 2.400 coletas/100 segmentos ~260 ms. Execução síncrona
mantida (ADR-070); limite de segmentos como proteção.

## 27. Validade interna

APROVADA: oráculos manuais independentes (112/112 via HTTP real), Wilson de
referência independente (tolerância 1e-9 + âncoras de literatura),
determinismo/hash/fingerprint (insensível a respostas não-analíticas,
sensível e reversível para analíticas), cotas/eleitorado provados NÃO-pesos.

## 28. Validade externa

LIMITADA POR CONSTRUÇÃO: amostra não ponderada (D11), IC Wilson aproximação
AAS, 1 entrevista = 1 observação. As limitações são declaradas na própria
experiência (barra metodológica) e nos docs; nenhuma inferência
populacional é prometida.

## 29. Limitações conhecidas

- L1: sem ponderação (`weighted_base = null`); leitura amostral crua.
- L2: Wilson 95% em aproximação AAS (desenho amostral complexo ignorado).
- L3: sem comparação temporal entre pesquisas/ondas.
- L4: mapa de município adiado (sem contrato de geometria no Web).
- L5: execução síncrona sem persistência de resultados (efêmera, ADR-066/070/072).
- L6: interpretação por templates fixos (sem LLM, por decisão — ADR-073).
- L7: espontâneas dependem de mapeamento ativo prévio; qualidade sob limiar
  bloqueia o sinal.
- L8: EmailStr rejeita domínios especiais (`.local`) no login (F5 — achado
  de convenção, sem mudança de produto).

## 30. Riscos residuais

- Ativação acidental: mitigada — exigiria UPDATE explícito no catálogo +
  entitlement + concessão de feature (três atos deliberados).
- Descrição do catálogo desatualizada ("Planejada: motor ainda nao
  implementado.") — corrigir na migration de ativação futura (nunca
  reescrever `c6d7e8f9a0b1`).
- Dívida cosmética: 10 erros de lint pré-existentes; chunk JS > 500 kB
  (aviso do Vite, pré-existente).
- `.env` local do backend contém segredos de desenvolvimento; NÃO é
  rastreado, mas recomenda-se rotação periódica do `SECRET_KEY` local e
  nunca reutilizá-lo em outro ambiente.

## 31. Commits

Backend (`feature/mapa-liderancas`):
- `90d8b81a529f19fbf11696d26f9f46d2abef69c4` — feat(intelligence):
  implement growth potential analysis (pacote + endpoint + parser
  compartilhado + 145 testes + QA tooling `scripts/qa/`).
- docs(intelligence): document growth potential MVP (doc/14–21,
  atualizações 00/01/02/04/08/09/10/12, ADRs 061–074) — é o commit que
  introduz ESTE documento: o sucessor imediato de `90d8b81a` no branch.

Web (`feature/mapa-liderancas`):
- `88fbb2a7f2d591e600f629e6ebff9b483f89b3f3` — feat(intelligence): add
  growth potential experience (página, lib, componentes, serviços, tipos,
  testes + E2E opt-in).
- `ef8268104292ffaa8a2decfb027322a9fd106ce2` — fix(web): harden routing
  and TypeScript build (F1/F2 do QA, 39 erros TS de baseline, build com
  `tsc -b`, guardas estáticas, remoção de arquivos mortos).

Staging feito com `git add` explícito por caminho (nunca `-A`/`.`), com
revisão de `git diff --cached` antes de cada commit.

## 32. HEADs finais

- Backend: o commit de docs descrito na seção 31 (sucessor imediato de
  `90d8b81a529f19fbf11696d26f9f46d2abef69c4` em `feature/mapa-liderancas`).
- Web: `ef8268104292ffaa8a2decfb027322a9fd106ce2`.
- Mobile: `cf6687a` (intocado).

## 33. Alembic head

`c6d7e8f9a0b1` (head ÚNICO, inalterado em todo o MVP 1 — nenhuma migration
nova foi necessária: o MVP não persiste nada).

## 34. Procedimento futuro de ativação (decisão de produto; NADA disto foi executado)

1. Decisão comercial formal registrada (quem, para qual empresa, quando).
2. Migration NOVA (nunca editar `c6d7e8f9a0b1`) marcando
   `modulo_funcionalidades.ativo = true` para `potencial_crescimento` e
   atualizando a descrição do catálogo.
3. Entitlement do módulo `inteligencia_eleitoral` para a(s) empresa(s)
   contratante(s) via fluxo admin de Superadmin (auditado).
4. Concessão explícita da feature no entitlement
   (`modulo_entitlement_funcionalidades`).
5. Verificação do ciclo de gate em staging próprio: 404→403→200 (roteiro de
   `scripts/qa/growth_qa_run.py`, FASE A).
6. Smoke E2E `npm run qa:growth-e2e` contra o ambiente de homologação.
7. Comunicação de limitações metodológicas (seção 29) ao cliente ANTES do
   uso; a barra metodológica da UI não substitui esse aviso.

## 35. Próxima evolução

Backlog declarado e NÃO iniciado (doc/08): ponderação declarada,
comparação temporal, mapa de município (contrato de geometria), persistência
de execuções/histórico, exportação, hardening de performance para volumes
maiores. Qualquer item exige novo ciclo com QA independente próprio.

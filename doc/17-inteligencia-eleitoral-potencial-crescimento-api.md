# INTELIGÊNCIA ELEITORAL
# MVP 1 — POTENCIAL DE CRESCIMENTO
# CONTRATO DA API

**Data:** 2026-09-02
**Status:** Prompt 04 implementado — camada HTTP fina sobre os contratos
validados; sem persistência, sem fila, sem Web/Mobile.
**CRÍTICO:** API implementada ≠ produto liberado. A feature
`potencial_crescimento` permanece **INATIVA** no catálogo real: fora de
fixtures de teste, as três rotas respondem `404 CAPACIDADE_INDISPONIVEL`
para qualquer tenant até a ativação formal (decisão posterior a Web + QA +
hardening). Nenhum entitlement real foi concedido; nenhum seed/migration
foi alterado.

## 1. Objetivo e rotas

Camada HTTP suficiente para o Prompt 05 montar a experiência Web sem novo
Backend para configuração/validação/execução:

```http
GET  /projetos/{projeto_id}/pesquisas/{pesquisa_id}/inteligencia-eleitoral/potencial-crescimento/opcoes-configuracao
POST /projetos/{projeto_id}/pesquisas/{pesquisa_id}/inteligencia-eleitoral/potencial-crescimento/validar-configuracao
POST /projetos/{projeto_id}/pesquisas/{pesquisa_id}/inteligencia-eleitoral/potencial-crescimento/analisar
```

Router: `pesquisa360/api/endpoints/potencial_crescimento.py`. DTOs/mapper:
`pesquisa360/inteligencia_eleitoral/http_contract.py`. Opções:
`pesquisa360/inteligencia_eleitoral/options.py`. Nenhuma matemática na
camada HTTP; motor intocado.

## 2. Cadeia de segurança

Ordem garantida pelo composto `growth_access` (dependency única, contexto
cacheado — uma resolução):

```text
autenticação (401)
  → resolução Projeto/Pesquisa pelo PATH + ACL/multitenancy (404)
  → Permissao.INTELIGENCIA_VER (403 RBAC)
  → require_feature("inteligencia_eleitoral", "potencial_crescimento",
     contexto da Pesquisa)  (404 capacidade inativa / 403 não contratada)
  → endpoint
```

- recurso cross-tenant, inexistente ou Pesquisa fora do Projeto do path →
  **404 antes de qualquer avaliação comercial** (usuário da Empresa A sem
  feature pedindo Pesquisa da Empresa B recebe 404, nunca 403 comercial);
- o contexto de entitlement carrega apenas `pesquisa_id`: o resolver deriva
  o projeto pai (escopos aditivos Empresa ∪ Projeto pai ∪ Pesquisa,
  semântica da Sprint 0 preservada);
- entitlement comercial ("empresa contratou") e permissão ("usuário pode
  visualizar inteligência") são camadas distintas — ambas exigidas,
  inclusive no GET de opções (estrutura analítica da Pesquisa não vaza a
  não autorizado).

## 3. Path é autoridade; tenant nunca vem do payload

`projeto_id`/`pesquisa_id` vêm do path e `Pesquisa.projeto_id == projeto_id`
é validado (404). O contrato aceita a configuração canônica completa no
body, mas exige `body.pesquisa_id == path.pesquisa_id`; divergência →
`422 {code: "SURVEY_PATH_BODY_MISMATCH", path_pesquisa_id, body_pesquisa_id}`
sem executar o motor. Nenhum request/response contém `company_id`/
`tenant_id` (extra="forbid" → 422), nenhum query param ou header de tenant.

## 4. GET opcoes-configuracao

Descreve os dados disponíveis **na Pesquisa** para montar uma análise (não
é catálogo comercial — isso é `GET /usuarios/me/modulos/`).

`GrowthConfigurationOptionsResponse`:

- `schema_version`, `engine_version`;
- `questions[]` (`QuestionOptionDTO`): `id`, `text`, `question_type`
  (canônico), `is_spontaneous`, `analytic_role` (**sugestão** — nunca
  autoridade), `analytic_metadata`, `applicability`, `compatible_as[]`,
  `values[]`;
- `territory`: `supported_levels` (NONE/SETOR/MUNICIPIO), `setores[]`
  (somente da Pesquisa: `id`, `nome`, `finalidade`,
  `analytically_eligible` = finalidade RELATORIO/AMBOS + geometria),
  `municipios[]` (resolução oficial via `municipio_territorio_id`/
  composição — nunca por nome nem Bairro legado) e
  `municipio_level_available`;
- `constraints`: `max_profile_dimensions=2`, ballot modes, sinais
  (REJECTION/SECOND_OPTION/VOTE_DECISION), modos de perfil, níveis
  territoriais, `weighting_modes=["NAO_PONDERADO"]`,
  `reference_types=["ELIGIBLE_UNIVERSE"]`,
  `uncertainty_methods=["WILSON_AAS_APPROX"]`, `reserved_values`
  (`__SEM_RESPOSTA__`) e flags de obrigatoriedade. **SEM defaults
  metodológicos**: nenhum `suppress_below_n`/`warn_below_n`/
  `max_uncategorized_rate` default é sugerido (os 10/30 de testes anteriores
  eram sintéticos).

`compatible_as` é calculado por **regra técnica explícita** (tipo canônico +
espontaneidade + aplicabilidade + contrato do produto), espelhando a matriz
do validator — nunca por texto e nunca só por `papel_analitico`:

| Condição | compatible_as |
|---|---|
| categórica (simples/múltipla) ou espontânea, GLOBAL | INTENTION, REJECTION, SECOND_OPTION (+ VOTE_DECISION se ESCOLHA_SIMPLES não espontânea) |
| ESCOLHA_SIMPLES não espontânea ou espontânea | PROFILE_CATEGORICAL |
| NUMERO | PROFILE_NUMERIC |
| categórica ou espontânea | RESPONSE_FILTER |
| aplicabilidade TERRITORIAL | **nunca** recebe compatibilidade de sinal (D14) |
| TEXTO não espontâneo (mesmo com papel PERFIL) | nenhuma |

`values`: rótulos canônicos (`Opcao.texto`) para categóricas; **categorias
ATIVAS** para espontâneas (inativas nunca aparecem). Identidade analítica é
`question_id + value` — nunca `opcao_id`. Diagnóstico de categorização
espontânea (taxa de não categorizada) NÃO é calculado nesta rota
(decisão de custo; o motor a calcula na execução).

## 5. POST validar-configuracao

Body: `GrowthAnalysisConfiguration` (contrato canônico). Fluxo: estrutura →
path/body → `validate_growth_configuration` (camada de domínio). **Não
executa o motor** (provado por spy em teste) e não persiste.

Semântica de status:

- **HTTP 200** para resultado de domínio (mesmo inválido) — a UI trata como
  formulário: `{valid, normalized_configuration, errors[], warnings[]}` com
  issues tipadas preservadas (`code`, `message`, `path`, `context` — ex.:
  `TARGET_VALUE_NOT_FOUND` em `target.bindings[0].values[0]`);
- **HTTP 422** apenas para transporte impossível: JSON malformado, campo
  desconhecido (`company_id`), enum inválido, violação estrutural do
  contrato Pydantic — e para `SURVEY_PATH_BODY_MISMATCH`.

`normalized_configuration` devolve a forma canônica (`"  candidato a "` →
`"Candidato A"`), com `VALUE_NORMALIZED` nos warnings — nada é normalizado
em silêncio. O response reusa o modelo canônico (não contém Decimal, então
não precisa de DTO próprio).

## 6. POST analisar

Fluxo: path → ACL → permissão → feature → configuração → `analyze_growth_potential`
→ mapper → 200. Execução **síncrona e efêmera** (request → cálculo →
response): sem persistência, sem `job_id`/`config_id`/`analysis_id`, sem
fila — o snapshot identifica a execução por `configuration_hash` +
`input_fingerprint`.

Erros mapeados explicitamente (nunca `except Exception → 422`):

| Situação | HTTP | Corpo |
|---|---|---|
| configuração inválida (`GrowthAnalysisConfigurationError`) | 422 | `{code: "GROWTH_CONFIGURATION_INVALID", message, errors[], warnings[]}` |
| `GrowthSegmentLimitExceededError` | 422 | `{code: "SEGMENT_LIMIT_EXCEEDED", message, context: {limit, found}}` |
| erro inesperado | 500 | fluxo/log padrão da aplicação (testado) |

Logging (INFO): `pesquisa_id`, `engine_version`, hash/fingerprint
abreviados, quantidade de findings e tempo — nunca JWT, payloads de
resposta ou dados individuais.

## 7. DTOs, Decimal e unidades (ADR-069)

- **Domínio permanece Decimal** (`results.py` intocado); a camada
  `http_contract.py` converte o valor FINAL para float na borda — nenhuma
  métrica é recalculada em float;
- **JSON numbers, nunca strings numéricas** (`"segment_rate": 0.5`, jamais
  `"0.5"`): aplicado a rate/participation_rate/delta_pp/lift/
  interval.low/high/confidence_level; counts/IDs/hashes não são convertidos;
- **Sem arredondamento de apresentação**: unidades canônicas — rate e
  interval em fração 0–1, `delta_pp` em pontos percentuais, `lift` razão.
  A Web decide "18,3%", "+8,2 pp", "1,8×";
- **null semantics preservadas**: `weighted_base=null` sempre; `lift=null`
  com referência zero; `interval=null` sem base; `eleitorado_apto=null` sem
  contexto — nunca 0 no lugar de indisponível.

## 8. Response de análise (`GrowthAnalysisResponse`)

Mapeia `GrowthAnalysis` integralmente — nenhum campo metodologicamente
relevante desaparece, ordem dos findings preservada:

- `snapshot`: engine_version, schema_version, configuration_hash,
  input_fingerprint, executed_at, pesquisa_id, universos (sem company_id);
- `universe`: survey_n, analytical_n, eligible_n,
  current_target_supporters_n, technical_missing_intention_n,
  excluded_special_n, mixed_special_conflict_n e contagens por
  classificação (o funil completo);
- `territory_diagnostics` e `coverage` (nada descartado em silêncio);
- `findings[]`: segment_key, profile_groups, territory (com
  `eleitorado_apto` opcional, claramente contexto), n_bruto,
  `weighted_base=null`, participation_rate, status, evidences, warnings;
- `evidences[]`: signal_type, status, direções, **numeradores e
  denominadores explícitos** de segmento e referência, taxas, intervalos,
  delta_pp, lift, warnings;
- `warnings[]`: `{code, severity, message, context}` estruturados
  (UNWEIGHTED_ANALYSIS, SRS_ASSUMPTION, REFERENCE_INCLUDES_SEGMENT etc.) —
  nunca texto concatenado.

Provas negativas em teste: o response não contém score, potential_level,
rank, projected_votes, potential_votes, company_id — a camada HTTP não
adiciona interpretação narrativa nem o que o motor deliberadamente não tem
(Prompt 06 cuidará da interpretação).

## 9. OpenAPI

As três rotas têm `response_model` nomeado (`GrowthConfigurationOptionsResponse`,
`GrowthValidationResponse`, `GrowthAnalysisResponse` + `GrowthAnalysisErrorDTO`
no 422 de analisar), summary/description e schemas de componentes tipados —
campos numéricos aparecem como `number` (nunca string) no schema; o
response de análise não é `object` genérico.

## 10. Testes

`tests/test_growth_analysis_api.py` — 36 testes (A01–A87 + E2E): segurança
(401/404/403, 404 antes de 403 comercial, permissão vs entitlement),
escopos aditivos de entitlement, path/body, opções (compatibilidades
técnicas, valores canônicos, categorias ativas, territórios, constraints
sem defaults), validação (200 valid=false, issues tipadas, normalização,
motor não chamado — spy), análise (motor uma vez, 422 tipados, erro
inesperado não vira 422), serialização numérica (numbers/unidades/null),
provas negativas, warnings estruturados, determinismo via HTTP e OpenAPI.
A fixture espelha o catálogo real (feature INATIVA) e prova tanto
`feature inativa → 404` quanto `ativa + entitlement → acesso`, ativando a
feature somente no banco de teste.

## 11. Contrato esperado pelo Prompt 05 (Web)

- consumir as três rotas sob os gates Web existentes
  (`FeatureGate`/`FeatureRoute` de `potencial_crescimento` + menu modular),
  lembrando que o Backend continua a autoridade (fail-closed);
- montar o formulário a partir de `opcoes-configuracao` (compatible_as +
  values canônicos + constraints), exigindo do usuário os parâmetros
  metodológicos sem default (base mínima; limiar de espontânea quando
  houver sinal espontâneo);
- usar `validar-configuracao` como validação de formulário (mapear
  `errors[].path` para campos; exibir warnings, incl. VALUE_NORMALIZED);
- enviar a `normalized_configuration` para `analisar`; formatar unidades na
  UI (fração→%, pp, ×) e nunca inventar score/ranking/projeção;
- tratar 404 CAPACIDADE_INDISPONIVEL como feature indisponível (rota some),
  403 comercial como não contratado, 422 tipado como erro de configuração.

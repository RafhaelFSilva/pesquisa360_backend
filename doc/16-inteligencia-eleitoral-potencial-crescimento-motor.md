# INTELIGÊNCIA ELEITORAL
# MVP 1 — POTENCIAL DE CRESCIMENTO
# MOTOR ESTATÍSTICO

**Data:** 2026-09-02
**Status:** Prompt 03 implementado — motor em memória, sem API, sem
persistência, sem Web/Mobile, sem score/projeção.
**Feature comercial:** `inteligencia_eleitoral.potencial_crescimento`
permanece INATIVA; nenhum gate aplicado; nenhum entitlement.

## 1. Objetivo

Transformar `GrowthAnalysisConfiguration` (doc/15) + coletas/respostas +
território em um `GrowthAnalysis` explicável: universos, bases, taxas,
referências, delta pp, lift, incerteza aproximada, evidências, diagnósticos
e warnings — sem inventar semântica, sem score, sem previsão de conversão,
sem projeção de votos, sem narrativa comercial.

## 2. Arquitetura

```text
pesquisa360/inteligencia_eleitoral/
    config.py       contrato canônico (Prompt 02)
    validation.py   validação estrutural + domínio (Prompt 02)
    results.py      modelos de resultado (Pydantic, sem HTTP/ORM)
    statistics.py   cálculo estatístico PURO (Decimal)
    engine.py       orquestração/dados + interface pública

pesquisa360/services/response_values.py
    parser compartilhado de múltipla escolha, extraído mecanicamente de
    multidimensional_cross.py SEM mudança de comportamento (o motor
    multidimensional agora o consome via alias)
```

Interface pública:

```python
analyze_growth_potential(db, current_user, configuration) -> GrowthAnalysis
```

Nunca aceita `company_id`/`tenant_id` como argumento analítico. A
configuração é REVALIDADA na entrada (`validate_growth_configuration`);
configuração inválida levanta `GrowthAnalysisConfigurationError` (erro de
domínio tipado, sem HTTPException — a API futura decide o mapeamento HTTP).

## 3. Entrada e fluxo

```text
GrowthAnalysisConfiguration
  → revalidação/normalização (recusa inválida)
  → universo da pesquisa (tenant/ACL)
  → filtros estruturais (universo analítico CONGELADO)
  → classificação de intenção → universo elegível
  → segmentação (perfil × território)
  → sinais → evidências (taxas, delta pp, lift, Wilson)
  → GrowthAnalysis {snapshot, universe, diagnostics, findings[], warnings[]}
```

## 4. Universos (denominadores explícitos)

- `universe.survey_n` — coletas da Pesquisa acessíveis ao usuário
  (Pesquisa⋈Projeto⋈ACL + `filtro_company_acessivel`), antes de filtros;
- `universe.analytical_n` — após `agent_ids` ∩ `setor_ids` ∩
  `response_filters`, aplicados UMA vez; o conjunto fica congelado (sem
  denominator drift);
- `universe.eligible_n` — elegíveis ao crescimento (Seção 5);
- `universe.current_target_supporters_n`, `technical_missing_intention_n`,
  `excluded_special_n`, `mixed_special_conflict_n` e
  `intention_classification_counts` detalham a diferença — respondendo "de
  N entrevistas, por que só M entraram?".

Unidade invariável: **1 Coleta = 1 observação** (`coleta_id` únicos; nunca
`count(Resposta.id)` como tamanho de amostra).

## 5. Elegibilidade e classificação de intenção

Classificação determinística por entrevista, usando exclusivamente
`TargetCandidacy.bindings`, taxonomia configurada e `ElectoralScenario`
(nunca texto): `CURRENT_TARGET_SUPPORTER`, `REGULAR_ELIGIBLE`, `INDECISO`,
`BRANCO_NULO`, `NS_NR`, `NAO_PRETENDE_VOTAR`, `TECHNICAL_MISSING`,
`SPECIAL_CONFLICT`.

Precedência (todas os ballot modes): alvo em qualquer intenção/slot →
SUPPORTER; senão ≥1 valor regular → REGULAR_ELIGIBLE; senão só categorias
especiais → EligibilityPolicy; sem valor reportável → TECHNICAL_MISSING.

- **SINGLE**: valor único da pergunta de intenção;
- **MULTIPLE**: conjunto de valores da mesma pergunta (alvo em qualquer
  posição = supporter; entrevista nunca conta duas vezes);
- **ORDERED_MULTIPLE**: mesma semântica sobre todos os slots configurados
  (alvo no 2º voto = supporter; 1º/2º voto NÃO são cenários separados).

Regras rígidas:

- só categorias especiais com políticas DIVERGENTES (ex.: INDECISO incluído
  + NS/NR excluído) → exclusão CONSERVADORA (`SPECIAL_CONFLICT`) + warning
  `MIXED_SPECIAL_ELIGIBILITY`; nenhuma precedência silenciosa. Classes
  múltiplas com a MESMA política usam ordem canônica documentada
  (INDECISO → BRANCO_NULO → NS_NR → NAO_PRETENDE_VOTAR) apenas para o
  rótulo do sumário;
- `TECHNICAL_MISSING` permanece no universo analítico, fora do elegível, e
  NUNCA vira indeciso (impossível saber se é eleitor atual do alvo);
- `CURRENT_TARGET_SUPPORTER` é sempre excluído do elegível (D01).

## 6. Filtros estruturais

- `response_filters`: reutiliza `services/filtros_universo` (OR dentro da
  pergunta, AND entre perguntas) — nenhuma DSL nova;
- `agent_ids`: restrição sobre `Coleta.agente_id`; IDs inexistentes só não
  produzem entrevistas (nada vaza);
- `setor_ids`: usa a regra territorial OFICIAL
  (`crud.classificar_coletas_por_setor` = ST_Covers +
  `coalesce(localizacao_inicio, localizacao_fim)` + setores RELATORIO/AMBOS)
  — NUNCA o `ST_Intersects` legado do Relatório Simples. Passa quem foi
  classificado UNICAMENTE em um dos setores do filtro (conflito/sem
  setor/sem coordenada ficam fora e são diagnosticáveis).

A classificação territorial é calculada no máximo 2 vezes por execução
(recorte do filtro e recorte da dimensão, quando diferentes) e reutilizada.

## 7. Segmentação

A configuração define a granularidade: 1–2 `ProfileDimensions` (+ território
opcional) geram SOMENTE os cruzamentos configurados — o motor não gera
níveis intermediários (SEXO e SEXO×IDADE nunca na mesma execução).

- **CATEGORICAL**: pertence ao grupo se algum valor reportável ∈
  `group.values` (comparação pela normalização única do projeto). Múltiplos
  valores podem colocar a entrevista em mais de um grupo (nunca duplicada
  DENTRO do grupo); warning `OVERLAPPING_SEGMENT_GROUPS`;
- **NUMERIC_RANGES**: parse estrito em `Decimal`; min/max INCLUSIVOS; valor
  não parseável/não coberto → fora dos segmentos, diagnosticado
  (`INVALID_NUMERIC:*` / `UNMATCHED_PROFILE:*` / `MISSING_PROFILE:*` em
  `coverage.by_reason`);
- **Território**: NONE | SETOR (setor analítico da classificação oficial) |
  MUNICIPIO (resolução oficial `municipio_territorio_id`/composição via
  `services/pergunta_territorio` — nunca nome, bairro legado ou coordenada
  aproximada);
- **Identidade**: `segment_key` determinística por keys configuradas e IDs
  territoriais reais (labels não são identidade):
  `profile:105=FEM|profile:106=18_34|territory:municipio=1600303`;
- **Explosão**: quantidade TEÓRICA de segmentos calculada antes de executar;
  acima de `GROWTH_MAX_SEGMENTS` (default 5000, env
  `P360_GROWTH_MAX_SEGMENTS`) → `GrowthSegmentLimitExceededError`
  (`SEGMENT_LIMIT_EXCEEDED`). Proteção computacional, não metodológica;
  nunca truncamento silencioso.

## 8. Buckets territoriais técnicos

`SEM_SETOR`, `TERRITORIO_SOBREPOSTO`, `SEM_COORDENADA` e
`MUNICIPIO_NAO_RESOLVIDO` entram em `territory_diagnostics` e em
`coverage.by_reason` — NUNCA viram `GrowthFinding`. Consequência explícita:
a soma da participação dos segmentos territoriais pode ficar abaixo de 100%
(warning `TERRITORY_PARTICIPATION_BELOW_100`).

## 9. Denominadores dos sinais

Para cada sinal, no segmento e na referência:

| Sinal | Base válida (denominador) | Numerador | Direção favorável |
|---|---|---|---|
| SEGUNDA_OPCAO | entrevistas com ≥1 valor reportável na pergunta | valores ∩ binding do alvo | HIGHER_IS_FAVORABLE |
| REJEIÇÃO | idem | rejeita o alvo (∩ binding); rejeição múltipla conta a entrevista UMA vez na base e no máximo UMA no numerador | LOWER_IS_FAVORABLE (taxa exibida é REJECTION_RATE — nunca 1−rejeição silencioso) |
| DECISÃO DO VOTO | entrevistas com valor ∈ (MOBILE ∪ CRYSTALLIZED) — valor fora dos grupos NÃO entra na base | valor ∈ MOBILE | HIGHER_IS_FAVORABLE (MOBILE_RATE; CRYSTALLIZED é contexto) |

INTENÇÃO não é sinal duplicado: alimenta elegibilidade e o sumário do
universo.

Ausência ≠ zero: sinal opcional não configurado gera apenas o warning
global `SIGNAL_NOT_CONFIGURED` (nunca evidência com 0%). Sinal configurado
com 0 respostas válidas no universo elegível → evidência
`SIGNAL_UNAVAILABLE` com `base = 0` e taxa/delta/lift `null`.

## 10. Base mínima (dois níveis, por segmento E por sinal)

`MinimumBasePolicy` (números sempre explícitos da configuração):

- `n_segmento < suppress_below_n` → finding `SUPPRESSED_BASE_INSUFFICIENT`:
  devolve key, labels, `n_bruto`, `participation_rate` e warning, SEM
  evidências;
- `suppress ≤ n < warn` → evidências calculadas + warning `SMALL_BASE`;
- **por sinal (crítico)**: mesmo num segmento grande, se
  `signal_valid_base_n < suppress` a EVIDÊNCIA daquele sinal é suprimida
  (sem taxa/delta/lift), enquanto outros sinais com base seguem
  disponíveis.

## 11. Taxas, delta pp, lift, direção

- taxas internas: frações 0–1 em `Decimal`; nada é arredondado antes de
  delta/lift/Wilson; arredondamento é apresentação;
- `delta_pp = (rate_segment − rate_reference) × 100` (pontos percentuais:
  0.18−0.10 → +8.0 pp; nunca "80% maior");
- `lift = rate_segment / rate_reference`; referência 0 → `lift = null` +
  `LIFT_UNDEFINED_REFERENCE_ZERO` (nunca infinito);
- `observed_direction` ∈ FAVORABLE/UNFAVORABLE/NEUTRAL conforme sinal do
  delta e direção favorável — direção OBSERVADA descritiva, jamais
  "significativo/provado/conclusivo";
- `participation_rate = n_segmento / eligible_n` (fração 0–1);
- NENHUMA agregação entre sinais (sem favorable_count/score/pesos/
  overall_*); `findings` ordenados deterministicamente por `segment_key` —
  sem ranking estratégico (a UI futura ordenará explicitamente).

## 12. Incerteza — Wilson AAS aproximado

`statistics.wilson_interval(x, n, nível)` em Decimal:

```text
center = (p + z²/(2n)) / (1 + z²/n)
half   = z/(1 + z²/n) · sqrt(p(1−p)/n + z²/(4n²))
```

z pela inversa da Normal (aproximação de Acklam; 95% → 1.959963984540054).
Limites em frações 0–1, recortados a [0,1]; nos extremos p=0/p=1 os limites
são exatos (0/1). `n = 0` → intervalo indisponível (`None`).

Limitações declaradas em TODA execução:

- `SRS_ASSUMPTION`: "aproximações sob hipótese de Amostragem Aleatória
  Simples; sem estratos, clusters, PSU ou desenho complexo";
- `REFERENCE_INCLUDES_SEGMENT`: a referência (universo elegível) contém o
  próprio segmento — comparações são DESCRITIVO-COMPARATIVAS; por isso o
  motor NÃO calcula p-value, z-test, qui-quadrado, IC de diferença de duas
  amostras independentes, nem usa sobreposição de Wilson como teste. A
  interpretação fica para os Prompts 06/07.

## 13. Referência

`ELIGIBLE_UNIVERSE` (D10): toda taxa de referência é calculada sobre
entrevistas ELEGÍVEIS com resposta válida para aquele sinal. O segmento é
subconjunto da referência (decisão metodológica explícita do MVP, warning
permanente).

## 14. Espontânea

Resolução pelo mecanismo existente (mapeamento ativo →
`resolve_reportable_response_value`; sem match → categoria real
"Não categorizada", que conta na base). Qualidade por sinal espontâneo:

```text
uncategorized_rate = entrevistas elegíveis com "Não categorizada"
                     / entrevistas elegíveis com resposta reportável
```

Acima de `SpontaneousQualityPolicy.max_uncategorized_rate` → o sinal NÃO é
usado como evidência: status `SIGNAL_UNAVAILABLE_QUALITY` + warning
`HIGH_UNCATEGORIZED_RATE` (condição de qualidade, não cosmética).

## 15. Múltipla escolha

Semântica única, a mesma já validada no motor multidimensional: uma
entrevista → conjunto de valores; parser compartilhado
`services/response_values.response_values` (JSON list, lista, escalar
legado, dedup na entrevista); duas LINHAS de `Resposta` para a mesma
(coleta, pergunta) continuam UMA entrevista na base (anomalia sinalizada em
`DUPLICATE_ANSWER_ANOMALY`). Elementos de múltipla são canonicalizados POR
ELEMENTO com a mesma chave de normalização única
(`crud.mapa_opcoes_canonicas`/`normalizar_chave_categoria`) — nenhuma
terceira semântica; nenhum comportamento do Relatório Simples/Crosstab 2D
foi reutilizado. Warning `MULTIPLE_CHOICE_SUM_MAY_EXCEED_100` nas
evidências de perguntas múltiplas (D12).

## 16. Warnings

`AnalysisWarning {code, severity (INFO|WARNING), message, context}` — código
é autoridade para a Web futura; mensagem é apresentação. Globais de toda
execução MVP: `UNWEIGHTED_ANALYSIS`, `SRS_ASSUMPTION`,
`REFERENCE_INCLUDES_SEGMENT` e, quando aplicáveis,
`MULTIPLE_COMPARISONS_EXPLORATORY` (mais de um segmento) e
`SIGNAL_NOT_CONFIGURED`. Warnings de configuração não duplicados são
propagados com `context.source = "configuration"`.

## 17. Snapshot, hashes e determinismo

`AnalysisSnapshot {engine_version, schema_version, configuration_hash,
input_fingerprint, executed_at, pesquisa_id, analytical_universe_n,
eligible_universe_n}` — nada é persistido.

- `GROWTH_ENGINE_VERSION = "1.0"` — versão SEMÂNTICA (não commit hash);
  mudança de semântica estatística exigirá nova versão;
- `configuration_hash` = SHA-256 do JSON canônico da configuração
  NORMALIZADA (UTF-8, keys ordenadas, separadores estáveis, sem campos de
  runtime, sem company_id);
- `input_fingerprint` = SHA-256 determinístico dos dados efetivamente
  usados: coleta_ids do universo analítico + valores reportáveis das
  perguntas analíticas (ordenados) + classificação territorial da execução.
  NÃO inclui `executed_at`;
- Reprodutibilidade (testada): mesma configuração + mesmos dados + mesma
  engine_version → mesmos hash, fingerprint, universos, findings, evidences
  e warnings — exceto `executed_at`.

## 18. Ausências deliberadas (invariáveis)

- **Ponderação**: nenhuma coluna/peso; cotas e eleitorado nunca são peso;
  sem `1.0` fingindo ponderação; `weighted_base = null` em todo finding;
- **Score/ranking**: sem score, potential_level, rank, agregado entre
  evidências;
- **Projeção**: sem projected_votes/potential_votes; `eleitorado_apto` é
  APENAS contexto territorial (exige Base Eleitoral principal VALIDADA e
  vínculo oficial; indisponibilidade → `electoral_context` nulo + warning
  `ELECTORAL_CONTEXT_UNAVAILABLE`, nunca falha da análise inteira). É
  PROIBIDO `taxa_segmento × eleitorado_apto = votos`.

## 19. Performance

Carregamento em lote: 1 query de coletas, ≤2 classificações territoriais,
1 query de respostas (todas as perguntas necessárias — derivadas da
configuração), 1 mapeamento espontâneo, 1 resolução municipal, ≤2 queries de
contexto eleitoral. Cálculo em memória; NENHUMA query por segmento/finding
(testado: o número de queries não muda de 1 para 2 dimensões).

## 20. Exemplo manual (caso oráculo, DADOS SINTÉTICOS)

Universo elegível 100; segmento Mulheres 20 (participação 20%); segunda
opção do alvo: 6/20 = 30% no segmento; 15/100 = 15% na referência →
delta = +15 pp; lift = 2.0; direção observada FAVORABLE. O teste
`test_oracle_manual_case` prova exatamente esses valores (não testa a
implementação contra ela mesma).

## 21. Testes

`tests/test_growth_analysis_engine.py` — 46 testes cobrindo E01–E112:
universos/elegibilidade, ballot modes (incl. conflito especial
conservador), segmentação (faixas inclusivas, missing, key determinística,
contagem de queries), território (classificação oficial, diagnósticos,
buckets não viram finding), sinais (oráculos de mão de segunda
opção/rejeição múltipla/decisão), base mínima (segmento e por sinal),
unidades (fração/pp/lift/referência zero), Wilson (valores conhecidos, sem
p-value), referência inclusiva, espontânea (limiar), múltipla escolha
(JSON/legado/dedup/linhas duplicadas), snapshot/hash/fingerprint,
não-ponderação, explicabilidade, determinismo, multitenancy, ausência de
projeção/score, caso oráculo e limite de segmentos.

## 22. Contrato esperado pelo Prompt 04 (API)

- expor `analyze_growth_potential` atrás de endpoint com
  `require_permissao` + gates comerciais (`require_module`/`require_feature`
  — a decidir na ativação), mapeando `GrowthAnalysisConfigurationError` →
  422 (com as issues tipadas) e `GrowthSegmentLimitExceededError` → 422;
- decidir persistência de configuração/execução (hoje inexistente) e, se
  houver, gravar `configuration_hash`/`input_fingerprint`/`engine_version`
  do snapshot;
- serializar `GrowthAnalysis` — resolvido no Prompt 04 (ADR-069): o domínio
  permanece Decimal e a camada HTTP (`http_contract.py`) converte o valor
  final para JSON number (float) na borda, sem recalcular métricas e sem
  arredondamento de apresentação;
- nunca aceitar `company_id` no payload; tenant continua vindo do contexto
  autorizado.

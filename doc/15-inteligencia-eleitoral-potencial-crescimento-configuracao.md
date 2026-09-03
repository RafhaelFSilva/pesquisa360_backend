# INTELIGÊNCIA ELEITORAL
# MVP 1 — POTENCIAL DE CRESCIMENTO
# CONTRATO DE CONFIGURAÇÃO DA ANÁLISE

**Data:** 2026-09-02
**Status:** CONTRATO IMPLEMENTADO E VALIDADO. Consumido pelo motor
estatístico do Prompt 03 (`GrowthAnalysisConfiguration → Growth Engine`,
ver `doc/16-inteligencia-eleitoral-potencial-crescimento-motor.md`) — ainda
sem API e sem persistência.
**Feature comercial:** `inteligencia_eleitoral.potencial_crescimento`
permanece INATIVA; nenhum gate foi aplicado; nenhum entitlement concedido.

## 1. Objetivo

Transformar a baseline metodológica aprovada (doc/14, decisões D01–D15) em um
CONTRATO DE CONFIGURAÇÃO canônico: `GrowthAnalysisConfiguration`, fortemente
tipado (Pydantic v2), serializável, validável e independente de HTTP, ORM,
persistência e frontend. O Prompt 03 (motor estatístico) receberá uma
configuração validada como entrada.

## 2. Arquitetura e localização

```text
pesquisa360/inteligencia_eleitoral/
    __init__.py     re-exports do domínio
    config.py       enums + contrato (validação ESTRUTURAL, camada 1)
    validation.py   issues tipadas + validação de DOMÍNIO (camada 2)
```

Justificativa: o domínio novo fica encapsulado num package próprio, fora do
`schemas.py`/`crud.py`/`models.py` monolíticos, seguindo o princípio de
separar CONFIGURATION CONTRACT ↔ PERSISTENCE ↔ ENGINE ↔ API ↔ WEB. Nesta
rodada existem apenas CONTRACT + VALIDATOR. Nada foi persistido; nenhuma
migration; `models.py` intacto; head Alembic `c6d7e8f9a0b1`.

Testes: `tests/test_growth_analysis_configuration.py` (casos C01–C65, 63
testes), no mesmo harness SQLite dos testes de cruzamentos.

## 3. Esquema conceitual

```text
GrowthAnalysisConfiguration
├── schema_version = 1            (versão do FORMATO; ≠ engine_version futura)
├── pesquisa_id                   (a Pesquisa é a onda; sem project_id, sem company_id)
├── target: TargetCandidacy       (label, cargo, bindings[])
├── scenario: ElectoralScenario   (ballot_selection_mode, intention_questions[])
├── intention_taxonomy            (listas explícitas: indeciso/branco-nulo/NS-NR/não vota)
├── eligibility: EligibilityPolicy
├── signals: [SignalDefinition]   (REJECTION | SECOND_OPTION | VOTE_DECISION)
├── profile_dimensions: 1..2      (CATEGORICAL | NUMERIC_RANGES)
├── territory: TerritoryConfiguration (NONE | SETOR | MUNICIPIO)
├── filters: StructuralFilters    (agent_ids, setor_ids, response_filters)
├── weighting: {mode: NAO_PONDERADO}
├── minimum_base: {suppress_below_n, warn_below_n}   (sem default numérico)
├── reference: {type: ELIGIBLE_UNIVERSE}
├── uncertainty: {WILSON_AAS_APPROX, 0.95}
└── spontaneous_quality: {max_uncategorized_rate}    (opcional/condicional)
```

## 4. Enums

| Enum | Valores |
|---|---|
| `BallotSelectionMode` | SINGLE, MULTIPLE, ORDERED_MULTIPLE |
| `SignalType` | INTENTION, REJECTION, SECOND_OPTION, VOTE_DECISION |
| `IntentionSpecialClass` | INDECISO_DECLARADO, BRANCO_NULO, NS_NR, NAO_PRETENDE_VOTAR |
| `ProfileDimensionMode` | CATEGORICAL, NUMERIC_RANGES |
| `TerritoryLevel` | NONE, SETOR, MUNICIPIO |
| `WeightingMode` | NAO_PONDERADO (somente) |
| `ReferencePopulationType` | ELIGIBLE_UNIVERSE (somente) |
| `UncertaintyMethod` | WILSON_AAS_APPROX (somente) |

`SignalType` é enum próprio do produto: `PapelAnalitico` (metadado da
Pergunta) atua apenas como sugestão/consistency-check, nunca como contrato
interno do motor.

## 5. TargetCandidacy e QuestionValueBinding

Não existe entidade Candidato. A candidatura é uma DECLARAÇÃO:

```json
{
  "label": "Candidato A",
  "cargo": "Senador",
  "bindings": [
    {"question_id": 101, "values": ["Candidato A"]},
    {"question_id": 102, "values": ["Candidato A"]}
  ]
}
```

- valores são valores REPORTÁVEIS/canônicos (`Opcao.texto` ou categoria
  espontânea ativa) — nunca `opcao_id` (não existe em `Resposta`);
- a mesma candidatura pode ter grafias diferentes em perguntas diferentes: um
  binding por pergunta explicita a equivalência; o motor nunca adivinha;
- `question_id` não se repete entre bindings.

**Decisão de fonte única da verdade:** sinais candidato-específicos
(REJECTION, SECOND_OPTION) NÃO carregam `target_values` próprios — os valores
da candidatura vivem exclusivamente em `target.bindings`. Isso evita duas
fontes conflitantes; o validator exige o binding correspondente
(`MISSING_TARGET_BINDING`) e marca binding sem uso com warning
(`UNUSED_TARGET_BINDING` — regra documentada: warning, não erro).

## 6. ElectoralScenario, ballot modes e intenção

O cenário NÃO é "uma pergunta": é o conjunto configurado de perguntas de
intenção mais o modo de votação.

```json
{
  "label": "Senado — cenário principal",
  "ballot_selection_mode": "ORDERED_MULTIPLE",
  "intention_questions": [
    {"question_id": 10, "slot": "PRIMEIRO_VOTO", "order": 1},
    {"question_id": 11, "slot": "SEGUNDO_VOTO", "order": 2}
  ]
}
```

Regras estruturais: `question_id` e `slot` únicos; SINGLE exige exatamente
uma pergunta; ORDERED_MULTIPLE exige ≥2 perguntas com `order` únicos e
consecutivos a partir de 1 (`SCENARIO_SLOTS_INCOHERENT` caso contrário).

**Fonte da verdade da intenção:** `scenario.intention_questions`. O sinal
INTENTION não aparece em `signals` (`INTENTION_SIGNAL_IN_SIGNALS` se enviado)
— elimina a duplicidade apontada na seção 20 do Prompt.

**Eleitor atual (CURRENT_TARGET_SUPPORTER):** a configuração possibilita a
resolução futura — SINGLE: resposta == valor do binding; MULTIPLE: valor ∈
respostas; ORDERED_MULTIPLE: valor em qualquer slot configurado. O cálculo em
si pertence ao Prompt 03.

## 7. Taxonomia de intenção e EligibilityPolicy

Categorias especiais por listas EXPLÍCITAS (D09) — sem regex, sem contains,
sem heurística; classes disjuntas entre si (`OVERLAPPING_TAXONOMY_VALUES`) e
disjuntas dos valores da candidatura (`TARGET_OVERLAPS_TAXONOMY`).

`EligibilityPolicy`:

- `current_target_supporter: "EXCLUDE"` — invariável D01, serializada
  explicitamente e sem outro valor possível;
- `include_indeciso`, `include_branco_nulo`, `include_ns_nr`,
  `include_nao_pretende_votar` — **todos obrigatórios**, sem default
  silencioso (a metodologia decide por pesquisa).

**Sem resposta técnica:** ausência de linha em `Resposta` é
`__SEM_RESPOSTA__`, categoria técnica reservada — proibida em qualquer lista
de valores do contrato (`RESERVED_VALUE`). Permanece contável no universo
analítico, mas fora da base válida de cada sinal; nunca é classificada como
intenção eleitoral.

## 8. Sinais

`SignalDefinition {type, question_id, enabled=true, decision_groups?}`:

- **REJECTION (opcional, D02):** evidência separada; aceita ESCOLHA_SIMPLES,
  MULTIPLA_ESCOLHA e espontânea categorizada; sem `penalty_weight`,
  `rejection_score` ou `automatic_exclusion` (não existem no contrato);
- **SECOND_OPTION (opcional, D03):** aceita ESCOLHA_SIMPLES e espontânea;
  MULTIPLA_ESCOLHA é permitida com warning `MULTIPLE_CHOICE_SECOND_OPTION`;
- **VOTE_DECISION (opcional):** não é candidato-específico (não exige
  binding); exige `decision_groups` explícitos `{mobile, crystallized}`
  disjuntos; valores fora dos grupos = UNKNOWN/OTHER para o motor; no MVP
  aceita apenas ESCOLHA_SIMPLES — ESCALA com cortes configurados fica
  registrada como evolução, não implementada;
- ausência de sinal opcional = configuração válida + warning
  (`NO_REJECTION` / `NO_SECOND_OPTION` / `NO_VOTE_DECISION`). A distinção
  futura SINAL_NAO_CONFIGURADO × SINAL_NAO_DISPONIVEL é preservada: o
  contrato registra o que foi configurado; o motor reportará disponibilidade.

Matriz de compatibilidade de tipos (D12/D14 aplicadas):

| Papel | ESCOLHA_SIMPLES | MULTIPLA_ESCOLHA | Espontânea | NUMERO | Outros |
|---|---|---|---|---|---|
| Intenção | ✔ | ✔ só com ballot MULTIPLE | ✔ | ✖ | ✖ |
| Rejeição | ✔ | ✔ | ✔ | ✖ | ✖ |
| Segunda opção | ✔ | ⚠ warning | ✔ | ✖ | ✖ |
| Decisão do voto | ✔ | ✖ | ✖ | ✖ | ✖ (ESCALA = evolução) |
| Perfil CATEGORICAL | ✔ | ✖ (entrevista em vários segmentos) | ✔ | ✖ | ✖ |
| Perfil NUMERIC_RANGES | ✖ | ✖ | ✖ | ✔ | ✖ |
| Filtro por resposta | ✔ | ✔ | ✔ | ✖ | ✖ |

Pergunta com `aplicabilidade = TERRITORIAL` não pode ser pergunta de
intenção nem sinal (`TERRITORIAL_SIGNAL_NOT_ALLOWED`, D14); como dimensão de
perfil é permitida com warning `TERRITORIAL_PROFILE_DIMENSION`.

## 9. Perfil (segmentação)

`ProfileDimension {question_id, label, mode, groups | ranges}` — generaliza o
padrão de `PlanoCotaPerfil.sexo_valores` (ponteiro + valores explícitos):

- CATEGORICAL: `groups[] {key, label, values}` — keys únicas, valores
  disjuntos entre grupos (`OVERLAPPING_GROUP_VALUES`);
- NUMERIC_RANGES: `ranges[] {key, label, min, max}` — limites INCLUSIVOS nas
  duas pontas; `max = null` = faixa aberta ("60+"); faixa invertida
  (`INVALID_NUMERIC_RANGE`) e sobreposição (`OVERLAPPING_NUMERIC_RANGES`,
  lembrando que [18,24] e [24,34] se sobrepõem no 24) são erros;
- profundidade: mínimo 1, máximo 2 dimensões (`PROFILE_DIMENSION_REQUIRED`,
  `TOO_MANY_PROFILE_DIMENSIONS`); território é adicional e não conta (D08).

## 10. Território

`TerritoryConfiguration {level, setor_ids, include_electoral_context}`:

- níveis do MVP: NONE, SETOR, MUNICIPIO (BAIRRO/LOCALIDADE/LOCAL_VOTACAO/
  SECAO ficam fora desta versão);
- território é DIMENSÃO DE SEGMENTAÇÃO do resultado, nunca sinal;
- SETOR: `setor_ids` opcional; vazio = todos os setores analíticos
  (finalidade RELATORIO/AMBOS) da Pesquisa; cada id informado precisa
  pertencer à Pesquisa (`SETOR_NOT_FOUND`, indistinguível de cross-tenant) e
  ser analítico (`SETOR_NOT_ANALYTICAL`); pesquisa sem setor analítico →
  `NO_ANALYTICAL_SECTORS`;
- MUNICIPIO: usa a resolução oficial (`setores.municipio_territorio_id` ou
  composição eleitoral via `services/pergunta_territorio`); se nenhum setor
  analítico resolve município → `MUNICIPIO_LEVEL_UNAVAILABLE`. Nunca por nome
  nem por Bairro legado;
- **diferença para `filters.setor_ids`:** o filtro RESTRINGE O UNIVERSO; o
  `territory.level` define se o território SEGMENTA O RESULTADO. É válido
  analisar só os setores 1,2,3 e ainda segmentar por setor;
- `include_electoral_context` pede `eleitorado_apto` da Base Eleitoral como
  CONTEXTO. **Invariável:** `taxa_segmento × eleitorado_apto = votos` é
  PROIBIDO — não existe projeção automática no Potencial de Crescimento
  (projeção pertence à Gestão de Lideranças, ADR-025). Exige nível
  territorial (`ELECTORAL_CONTEXT_REQUIRES_TERRITORY`); ausência com nível
  territorial gera warning `NO_ELECTORAL_CONTEXT`.

## 11. Filtros estruturais

`StructuralFilters {agent_ids, setor_ids, response_filters}` reutiliza a
semântica existente de `services/filtros_universo.py` — OR dentro da mesma
pergunta, AND entre perguntas — sem DSL nova.
`StructuralFilters.as_filtros_respostas()` entrega a forma
`[(pergunta_id, {valores})]` consumida por `aplicar_filtros_respostas`.
`filters.setor_ids` também exige setores analíticos da própria Pesquisa
(a classificação espacial do motor usará a regra oficial `ST_Covers`).
`agent_ids` não é validado contra o banco nesta rodada (filtro inócuo se o
agente não coletou; nenhuma existência vaza).

## 12. Ponderação e weighted_base (D11)

`WeightingDefinition {mode: NAO_PONDERADO}` — enum de um valor; qualquer
outro modo é `UNSUPPORTED_WEIGHTING_MODE`. Não existem `weight_column`,
`weight_question` nem `weight_formula`.

Invariável de contrato (verificada em teste): o contrato NÃO expõe
`weighted_base` nem `base_ponderada`. O resultado futuro deverá reportar
`n_bruto` real e `weighted_base = null` no modo NAO_PONDERADO; quando
ponderação real existir, `weighted_base` = soma dos pesos. Todo resultado do
modo não ponderado carrega o warning `UNWEIGHTED_ANALYSIS`.

## 13. Base mínima, referência, incerteza, espontânea

- `MinimumBasePolicy {suppress_below_n ≥ 1, warn_below_n > suppress_below_n}`
  — ambos obrigatórios, sem default (`INVALID_BASE_THRESHOLDS`); os valores
  10/30 que aparecem nos testes são SINTÉTICOS, não defaults de produção;
- `ReferencePopulation {type: ELIGIBLE_UNIVERSE}` — única referência do MVP
  (D10); contrato extensível por enum no futuro;
- `UncertaintyPolicy {method: WILSON_AAS_APPROX, confidence_level: 0.95}` —
  aproximação sob hipótese de Amostragem Aleatória Simples; não incorpora
  estratos, clusters, PSU ou desenho complexo; nenhuma conta é feita nesta
  rodada;
- `SpontaneousQualityPolicy {max_uncategorized_rate}` — fração 0–1
  (exclusivos, `INVALID_SPONTANEOUS_THRESHOLD`); obrigatória quando alguma
  pergunta espontânea participa como intenção/sinal
  (`SPONTANEOUS_POLICY_REQUIRED`), acompanhada do warning
  `SPONTANEOUS_SIGNAL`.

## 14. Erros e warnings tipados

`ConfigurationIssue {code, message, path, context?}` — ex.:

```json
{
  "code": "TARGET_VALUE_NOT_FOUND",
  "path": "target.bindings[1].values[0]",
  "message": "O valor 'Candidato Z' não existe entre os valores reportáveis da pergunta 102.",
  "context": {"question_id": 102}
}
```

ERROS (`GrowthConfigErrorCode`) impedem o uso pelo motor; WARNINGS
(`GrowthConfigWarningCode`) permitem configuração válida com limitações
declaradas. Catálogo completo nos enums de
`pesquisa360/inteligencia_eleitoral/validation.py`; principais:

- erros: `QUESTION_NOT_FOUND`, `QUESTION_FROM_OTHER_SURVEY`,
  `QUESTION_INACTIVE`, `TARGET_VALUE_NOT_FOUND`, `VALUE_NOT_FOUND`,
  `MISSING_TARGET_BINDING`, `TERRITORIAL_SIGNAL_NOT_ALLOWED`,
  `INCOMPATIBLE_QUESTION_TYPE`, `TOO_MANY_PROFILE_DIMENSIONS`,
  `OVERLAPPING_TAXONOMY_VALUES`, `TARGET_OVERLAPS_TAXONOMY`,
  `INVALID_BASE_THRESHOLDS`, `UNSUPPORTED_WEIGHTING_MODE`,
  `UNSUPPORTED_SCHEMA_VERSION`, `RESERVED_VALUE`, `SETOR_NOT_FOUND`,
  `SETOR_NOT_ANALYTICAL`, `NO_ANALYTICAL_SECTORS`,
  `MUNICIPIO_LEVEL_UNAVAILABLE`, `SPONTANEOUS_POLICY_REQUIRED`,
  `SCENARIO_SLOTS_INCOHERENT`, `PESQUISA_NOT_FOUND`, `UNKNOWN_FIELD`;
- warnings: `NO_REJECTION`, `NO_SECOND_OPTION`, `NO_VOTE_DECISION`,
  `UNWEIGHTED_ANALYSIS`, `SPONTANEOUS_SIGNAL`, `ANALYTIC_ROLE_MISMATCH`,
  `UNUSED_TARGET_BINDING`, `VALUE_NORMALIZED`,
  `MULTIPLE_CHOICE_SECOND_OPTION`, `TERRITORIAL_PROFILE_DIMENSION`,
  `NO_ELECTORAL_CONTEXT`.

`papel_analitico` divergente do uso configurado gera
`ANALYTIC_ROLE_MISMATCH` (warning, preservando pesquisas antigas);
`papel_analitico = null` é permitido sem warning; a Pergunta nunca é alterada.

## 15. Normalização de valores

Após validação, a configuração possui forma CANÔNICA: valores categóricos são
resolvidos contra `Opcao.texto` (via `crud.mapa_opcoes_canonicas` /
`normalizar_chave_categoria`) e espontâneos contra categorias ativas — a
MESMA normalização já usada pelos relatórios (nenhuma regra duplicada).
`"  candidato a "` vira `"Candidato A"` na `normalized_configuration`, com
warning `VALUE_NORMALIZED {original, canonical}` — o input original nunca é
alterado em silêncio. Valor inexistente nunca é aceito silenciosamente.

## 16. Validação em duas camadas

```text
CAMADA 1 — estrutural (Pydantic, sem banco)
    parse_growth_analysis_configuration(payload)
    -> (config | None, issues estruturais tipadas)

CAMADA 2 — domínio/banco
    validate_growth_configuration(db, current_user, config)
    -> GrowthConfigurationValidationResult
       {valid, normalized_configuration, errors, warnings}

Conveniência: validate_growth_configuration_payload(db, current_user, payload)
```

Multitenancy: o contrato não contém `company_id` (`extra="forbid"` rejeita,
`UNKNOWN_FIELD`); a Pesquisa é resolvida por `crud._validar_pesquisa_relatorio`
(ACL/tenant existentes, sem duplicação); Pesquisa/pergunta/setor cross-tenant
são indistinguíveis de inexistentes (`*_NOT_FOUND`), preservando o padrão 404.
Pergunta de OUTRA pesquisa do MESMO tenant é distinguível
(`QUESTION_FROM_OTHER_SURVEY`). Não há endpoint nesta rodada.

## 17. Serialização determinística e hash futuro

`model_dump(mode="json")` produz JSON estável: listas de IDs são
ordenadas/deduplicadas na entrada; listas de valores preservam a ordem
configurada; não há `set` no contrato serializado; round-trip preserva
semântica (testes C61–C63). Isso é pré-requisito para o futuro
`configuration_hash` do `AnalysisSnapshot` (Prompt 03/04) — o hash em si não
foi implementado.

`schema_version = 1` versiona o FORMATO da configuração; `engine_version`
pertencerá ao snapshot/resultado (conceitos distintos).

## 18. Exemplo canônico (sintético)

```json
{
  "schema_version": 1,
  "pesquisa_id": 10,
  "target": {
    "label": "Candidato A",
    "cargo": "Senador",
    "bindings": [
      {"question_id": 101, "values": ["Candidato A"]},
      {"question_id": 102, "values": ["Candidato A"]},
      {"question_id": 103, "values": ["Candidato A"]}
    ]
  },
  "scenario": {
    "label": "Senado — cenário principal",
    "ballot_selection_mode": "SINGLE",
    "intention_questions": [{"question_id": 101, "slot": "VOTO"}]
  },
  "intention_taxonomy": {
    "indeciso_declarado": ["Indeciso"],
    "branco_nulo": ["Branco/Nulo"],
    "ns_nr": ["NS/NR"],
    "nao_pretende_votar": ["Não pretende votar"]
  },
  "eligibility": {
    "current_target_supporter": "EXCLUDE",
    "include_indeciso": true,
    "include_branco_nulo": true,
    "include_ns_nr": false,
    "include_nao_pretende_votar": false
  },
  "signals": [
    {"type": "REJECTION", "question_id": 102, "enabled": true},
    {"type": "SECOND_OPTION", "question_id": 103, "enabled": true},
    {"type": "VOTE_DECISION", "question_id": 104, "enabled": true,
     "decision_groups": {"mobile": ["Pode mudar"], "crystallized": ["Definitivo"]}}
  ],
  "profile_dimensions": [
    {"question_id": 105, "label": "Sexo", "mode": "CATEGORICAL",
     "groups": [
       {"key": "FEM", "label": "Mulheres", "values": ["Feminino"]},
       {"key": "MASC", "label": "Homens", "values": ["Masculino"]}
     ]},
    {"question_id": 106, "label": "Faixa etária", "mode": "NUMERIC_RANGES",
     "ranges": [
       {"key": "18_24", "label": "18–24", "min": 18, "max": 24},
       {"key": "25_59", "label": "25–59", "min": 25, "max": 59},
       {"key": "60_MAIS", "label": "60+", "min": 60, "max": null}
     ]}
  ],
  "territory": {"level": "MUNICIPIO", "setor_ids": [], "include_electoral_context": false},
  "filters": {"agent_ids": [], "setor_ids": [], "response_filters": []},
  "weighting": {"mode": "NAO_PONDERADO"},
  "minimum_base": {"suppress_below_n": 10, "warn_below_n": 30},
  "reference": {"type": "ELIGIBLE_UNIVERSE"},
  "uncertainty": {"method": "WILSON_AAS_APPROX", "confidence_level": 0.95}
}
```

**10/30 são valores sintéticos de exemplo/teste — nunca defaults
metodológicos de produção (D04).**

## 19. Invariantes

1. Sem `company_id`/tenant no contrato; tenant só pelo contexto autorizado.
2. Sem heurística textual em lugar nenhum; toda semântica é declarada.
3. `current_target_supporter = EXCLUDE` (D01) — não configurável.
4. `weighted_base` não existe no contrato e será `null` no resultado não
   ponderado (D11) — jamais `n_bruto` disfarçado.
5. `__SEM_RESPOSTA__` é reservada e inconfigurável.
6. Base mínima sem default; validação `warn > suppress ≥ 1`.
7. `papel_analitico` é sugestão; o validator nunca altera a Pergunta.
8. Território nunca é sinal; pergunta TERRITORIAL nunca é sinal (D14).
9. Serialização determinística (pré-requisito de hash/reprodutibilidade).
10. Cross-tenant indistinguível de inexistente em toda a validação.

## 20. Itens ainda não implementados (desta linha)

- motor estatístico, delta pp, lift, IC de Wilson, findings, ranking
  (Prompt 03);
- API/endpoint de validação e de execução (Prompt 04) — o
  `GrowthConfigurationValidationResult` já está pronto para ser mapeado;
- persistência da configuração (decisão adiada até API + lifecycle; nenhuma
  tabela criada);
- `configuration_hash` e `AnalysisSnapshot`;
- ESCALA como pergunta de decisão do voto (cortes configurados) — evolução;
- ponderação real (`WeightingMode.PONDERADO` com origem declarada);
- gates comerciais nas rotas; ativação da feature; Web; Mobile.

## 21. Contrato esperado pelo Prompt 03

O motor receberá `GrowthConfigurationValidationResult.normalized_configuration`
(nunca o payload bruto) e deverá:

- montar o universo analítico com `filters` (semântica `filtros_universo`) e
  a regra espacial oficial de setor;
- resolver CURRENT_TARGET_SUPPORTER conforme `scenario.ballot_selection_mode`
  + `target.bindings` e aplicar `eligibility` sobre a taxonomia;
- calcular sinais por segmento (`profile_dimensions` × `territory`) com
  denominadores explícitos, n_bruto sempre presente e `weighted_base = null`;
- aplicar `minimum_base` (supressão declarada, nunca silenciosa), referência
  `ELIGIBLE_UNIVERSE`, incerteza `WILSON_AAS_APPROX`;
- propagar TODOS os warnings de configuração para o resultado, somando os
  estatísticos (`doc/14`, catálogo de `StatisticalWarning`).

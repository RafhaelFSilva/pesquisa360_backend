# INTELIGÊNCIA ELEITORAL
# MVP 1 — POTENCIAL DE CRESCIMENTO
# MODELAGEM METODOLÓGICA E CONTRATO DE DOMÍNIO

**Data:** 2026-09-02 (aprovação registrada em 2026-09-02, Prompt 02)
**Status:** APROVADO — baseline metodológica do MVP 1
**Escopo:** especificação conceitual. Nenhum código funcional foi alterado
nesta rodada de modelagem.
**Feature comercial:** `inteligencia_eleitoral.potencial_crescimento` — permanece
PLANEJADA/INATIVA no catálogo (semeada com `ativo=false` em `c6d7e8f9a0b1`).

## 0. Baseline auditada

| Repositório | Branch | HEAD | Worktree |
|---|---|---|---|
| Backend | `feature/mapa-liderancas` | `3e632ab96818f9428cfb5bd4cc2b036635f02091` | limpo |
| Web | `feature/mapa-liderancas` | `e96d10d1a6a6f89b2b7932ffe9ffa2314e4c3789` | limpo |
| Mobile | `feature/mobile-multitenancy-integration` | `cf6687af67fab8ff16f91fe2dba456e0da19e415` | limpo |

Alembic: head único `c6d7e8f9a0b1` (`modulos_entitlements`). Nenhuma divergência
com o checkpoint `doc/13-checkpoint-sprint0-modularizacao.md`.

## 0-bis. Decisões metodológicas APROVADAS (baseline congelada do MVP)

A revisão humana aprovou as recomendações deste documento como as decisões
D01–D15, congeladas como baseline do MVP e implementadas a partir do
Prompt 02 (`doc/15-inteligencia-eleitoral-potencial-crescimento-configuracao.md`):

- **D01** — quem já declara voto na candidatura-alvo NÃO pertence ao universo
  de crescimento (pertence à futura Consolidação da Base).
- **D02** — rejeição é EVIDÊNCIA SEPARADA: sem penalização numérica, sem
  exclusão por default, sem score de rejeição.
- **D03** — segunda opção é OPCIONAL; quando disponível é sinal forte de
  proximidade; a ausência não impede a análise.
- **D04** — base mínima tem dois níveis (SUPRESSÃO e ALERTA) e os números são
  parâmetros explícitos da configuração, nunca defaults inventados.
- **D05** — delta pp (primária de apresentação) e lift (secundária) são as
  métricas comparativas futuras.
- **D06** — sem score 0–100, sem pesos arbitrários entre sinais, sem
  ALTO/MÉDIO/BAIXO sem regra aprovada.
- **D07** — incerteza: IC de Wilson 95% como APROXIMAÇÃO sob hipótese de
  Amostragem Aleatória Simples; nunca apresentado como erro de desenho
  complexo.
- **D08** — profundidade máxima: até 2 dimensões de PERFIL + território
  opcional (território não conta como dimensão de perfil).
- **D09** — indecisão e categorias especiais por listas EXPLÍCITAS de
  valores; nunca inferência textual.
- **D10** — referência default (e única do MVP): UNIVERSO ELEGÍVEL.
- **D11** — MVP NÃO PONDERADO. **Correção sobre a Seção 17/18 abaixo:** o
  contrato correto é `weighting_mode = NAO_PONDERADO`, `n_bruto` real e
  `weighted_base = INDISPONÍVEL/null`. NÃO declarar `base_ponderada = n_bruto`
  (isso fabricaria uma grandeza inexistente); quando ponderação real existir,
  `weighted_base` passa a ser a soma dos pesos.
- **D12** — MULTIPLA_ESCOLHA pode ser sinal quando semanticamente apropriada;
  unidade continua a ENTREVISTA; soma de percentuais pode exceder 100%.
- **D13** — ausência de invalidação de Coleta não bloqueia o MVP; permanece
  dívida explícita de qualidade de dados (não inventar status novo).
- **D14** — pergunta com `aplicabilidade = TERRITORIAL` não pode ser SINAL
  ELEITORAL no MVP (apenas perguntas GLOBAL como sinais).
- **D15** — pergunta espontânea como sinal exige política explícita de
  qualidade de categorização; o limiar de "Não categorizada" é parâmetro da
  configuração, sem default aprovado.

Onde este documento (Seções 17–18) diz "no modo NAO_PONDERADO, base ponderada
≡ n bruto", vale a redação da D11: base ponderada é INDISPONÍVEL no modo não
ponderado — o resultado futuro reporta apenas `n_bruto` e `weighted_base = null`.

A auditoria inspecionou (somente leitura): `pesquisa360/db/models.py`,
`pesquisa360/schemas.py`, `pesquisa360/crud.py`, `pesquisa360/question_types.py`,
`pesquisa360/analytics.py`, `pesquisa360/services/` (`multidimensional_cross`,
`mapa_respostas_geo`, `filtros_universo`, `lideranca_analytics`, `cota_perfil`,
`pergunta_territorio`, `setor_municipio`, `modulos`, `base_eleitoral`),
`pesquisa360/api/endpoints/` (`relatorios`, `apuracao_espontanea`, `liderancas`,
`coletas`, `agente`), `pesquisa360/api/dependencies/modulos.py`,
`pesquisa360/core/rbac.py`, migrations relevantes (`c4d5e6f7a8b9`,
`c6d7e8f9a0b1`, `b1c2d3e4f5a6`, `8c2097e0a3de`, `a8b9c0d1e2f3`, `d5e6f7a8b9c0`,
`e6f7a8b9c0d1`, `f7a8b9c0d1e2`) e docs `00`–`13`.

---

## 1. Objetivo do produto

Apoiar a leitura estratégica de UMA pesquisa (uma onda), identificando
**segmentos e territórios da amostra com sinais comparativamente mais
favoráveis à expansão de uma candidatura**, sempre acompanhados de base,
referência, evidências e alertas.

O produto entrega **indícios estatísticos descritivo-comparativos**, não
previsão de voto, não projeção de migração, não causalidade.

## 2. Pergunta de negócio

> "Em quais segmentos e territórios da amostra existem sinais comparativamente
> mais favoráveis para expansão da candidatura, considerando as variáveis
> eleitorais disponíveis e respeitando as limitações da pesquisa?"

Formulação comercial de origem ("Onde posso crescer? Quem posso conquistar?")
é aceitável como rótulo de navegação, desde que a resposta apresentada use a
linguagem da Seção 30.

## 3. Não-objetivos

O Potencial de Crescimento NÃO responde e NÃO deve sugerir que responde:

- "Quem vai votar no candidato?" / "Quantos votos o candidato vai ganhar?"
- probabilidade individual de conversão de eleitores;
- causalidade entre perfil e voto;
- comparação entre ondas (tracking) — fica para evolução, ver Seção 38;
- substituição dos relatórios simples e cross-tabs (que permanecem como
  descrição bruta; este módulo é interpretação analítica — fronteira na
  Seção 4.8).

## 4. Fontes de dados atuais (auditoria factual)

Esta seção registra o estado REAL. Nomes são os do código.

### 4.1 Entidades centrais

| Conceito | Entidade real | Tabela | Observações-chave |
|---|---|---|---|
| Campanha | `Projeto` | `projetos` | sem soft delete; vínculo à Base Eleitoral via `ProjetoBaseEleitoral` |
| Onda | `Pesquisa` | `pesquisas` | **a Pesquisa É a onda** (`models.py:1148-1150`); soft delete `ativo`; cerca eletrônica própria |
| Pergunta | `Pergunta` | `perguntas` | `tipo_pergunta` (String normalizada), `eh_obrigatoria`, `eh_resposta_espontanea`, `papel_analitico`, `metadados_analiticos` (JSONB), `ativo`, `aplicabilidade` (GLOBAL/TERRITORIAL) |
| Opção | `Opcao` | `opcoes` | apenas `texto`, `ordem`, `proxima_pergunta_id` (pulo). **Sem `valor`/`codigo`: a chave analítica é o texto** |
| Entrevista | `Coleta` | `coletas` | `pesquisa_id`, `agente_id`, `company_id`, `client_uuid`, `setor_id` (nullable, sem backfill espacial), `localizacao_inicio/fim` (POINT 4326), `inconformidade_localizacao`, `status_sincronizacao` |
| Resposta | `Resposta` | `respostas` | somente `valor_resposta` **Text**. Não existe `opcao_id` |
| Abordagem | `TentativaCampo` | `tentativas_campo` | `resultado` ∈ EM_ANDAMENTO, RECUSA, NAO_ELEGIVEL, DESISTENCIA, INCOMPLETA, PROBLEMA_TECNICO, OUTRO, CONCLUIDA; `coleta_id` nullable |
| Respondente | **não existe** | — | política deliberada: nenhum dado pessoal do entrevistado (`schemas.py:1244`) |
| Formulário | **não existe** | — | o questionário é o conjunto de `perguntas` ativas da Pesquisa |

Fatos críticos sobre `Coleta`:

- o único valor gravado em `status_sincronizacao` é a string `"sincronizado"`
  (`crud.py:1336`); **não existem estados "concluída/invalidada"**, não há soft
  delete nem coluna de invalidação;
- **nenhum relatório atual filtra status de coleta** — o universo é
  `Coleta.pesquisa_id == X` mais filtros opcionais;
- entrevistas interrompidas/recusadas NÃO viram `Coleta`: ficam em
  `TentativaCampo` (que pode ou não apontar para uma coleta concluída).

### 4.2 Tipos de pergunta reais

Constantes de aplicação (`question_types.py:5-14`), não enum de banco:

`TEXTO`, `TEXTO_LONGO`, `NUMERO`, `ESCOLHA_SIMPLES`, `MULTIPLA_ESCOLHA`,
`DATA`, `IMAGEM`, `ESCALA` — com aliases normalizados (`RADIO`, `CHECKBOX`,
`RATING`…). Categóricas: `ESCOLHA_SIMPLES` e `MULTIPLA_ESCOLHA`.
"Espontânea" NÃO é tipo: é o boolean `eh_resposta_espontanea` combinável com
qualquer tipo (tipicamente `TEXTO`). Não existem tipos "seleção de candidato"
nem "ranking".

Papel possível de cada tipo na análise:

| Tipo | ALVO (sinal eleitoral) | SEGMENTAÇÃO | FILTRO | Observação |
|---|---|---|---|---|
| ESCOLHA_SIMPLES | SIM | SIM | SIM | caso principal |
| MULTIPLA_ESCOLHA | SIM (com cautela — Seção 4.6) | NÃO recomendado | SIM | uma entrevista contribui p/ várias categorias |
| TEXTO + `eh_resposta_espontanea` | SIM (via categorização) | SIM (via categorização) | SIM | depende de mapeamento ativo |
| ESCALA | POSSÍVEL (requer corte configurado) | POSSÍVEL (faixas) | SIM | sem semântica nativa de faixas |
| NUMERO | NÃO direto | SIM (faixas — só idade tem parsing pronto, via cotas) | SIM | faixas precisam de configuração |
| TEXTO/TEXTO_LONGO sem espontânea | NÃO | NÃO | NÃO | não categorizável |
| DATA, IMAGEM | NÃO APLICÁVEL | NÃO APLICÁVEL | NÃO | — |

### 4.3 Mapeamento semântico de perguntas — resposta à questão A/B/C

**Resposta: B — existe parcialmente.** A infraestrutura formal EXISTE, mas é
opcional e nenhum cálculo a consome:

- `perguntas.papel_analitico` — String(50) nullable, indexada (migration
  `c4d5e6f7a8b9`); enum de aplicação `PapelAnalitico`
  (`pesquisa360/analytics.py`): `INTENCAO_VOTO`, `REJEICAO`, `SEGUNDA_OPCAO`,
  `DECISAO_VOTO`, `PERFIL`, `TERRITORIO`, `OUTRO`;
- `perguntas.metadados_analiticos` — JSONB NOT NULL default `{}`,
  **schema-less** (única validação: ser objeto JSON). Não há chaves
  padronizadas; exemplos documentais usam `{"dimensao":"PERFIL","subtipo":"SEXO"}`;
- consumo atual: apenas pass-through descritivo no payload dos cruzamentos
  multidimensionais (`multidimensional_cross.py:277,671,688`). **Nenhuma query
  filtra por `papel_analitico`**;
- não existem papéis para sexo/idade/escolaridade/renda (caem em `PERFIL` +
  JSON livre); não existe vínculo pergunta↔candidato;
- lacunas do enum atual para este MVP: não distingue estimulada/espontânea,
  não marca avaliação de governo, não identifica candidatura.

Adicionalmente, `planos_cota_perfil.pergunta_sexo_id` / `pergunta_idade_id`
são o único precedente de "ponteiro semântico" consumido por cálculo
(`services/cota_perfil.py`), incluindo dicionário `sexo_valores` que mapeia
textos de opção → categorias canônicas. **Este é o padrão a generalizar no
Prompt 02.**

**Invariável assumida (reafirma a diretriz do prompt e a ADR-027):** o motor
NUNCA inferirá semântica pelo texto da pergunta ou da opção
(`if "rejeição" in texto` é proibido). Toda semântica virá de configuração
explícita e auditável.

### 4.4 Pesos amostrais — auditoria

**Não existe peso amostral em nenhuma entidade.** Verificação exaustiva
(grep `peso|ponderac|weight|expans|fator` em modelos, migrations e serviços):

- nenhuma coluna de peso em `coletas`, `respostas`, `setores`,
  `tentativas_campo`, `cotas_perfil`, `territorio_eleitoral`;
- as únicas ocorrências de "peso" são: literal `1` de ponto de heatmap
  (`crud.py:2622`), `peso_percentual` de eleitorado por bairro
  (`locais.py:152`, sem persistência, desconectado de coletas), pesos de
  `random.choices` em seeds e dígito verificador de CNPJ;
- consequentemente: relatórios simples NÃO ponderam, cross-tabs NÃO ponderam,
  mapas NÃO ponderam, percentuais atuais NÃO ponderam. Tudo conta entrevistas
  (ou linhas de resposta) com peso implícito 1;
- não existem estratos, clusters, PSU, desenho amostral ou pós-estratificação
  registrados. O que existe de referência populacional é: `cotas_perfil`
  (metas orientativas município×sexo×faixa) e a Base Eleitoral
  (`eleitorado_apto` por território, ADR-023/031/032).

Implicação metodológica na Seção 17.

### 4.5 Como os relatórios atuais calculam (denominadores reais)

Existem **quatro denominadores diferentes**, nenhum compartilhado:

1. **Relatório Simples** (`crud.get_relatorio_pesquisa`, `crud.py:2169-2324`):
   `percentual = contagem / total_pergunta`, onde `total_pergunta` é a soma
   das **linhas de `respostas`** da pergunta (`crud.py:2244`). Quem não
   respondeu não entra; NS/NR/branco entram como categorias normais;
   `total_coletas` é informado mas não usado em cálculo.
2. **Crosstab 2D** (`crud.get_report_crosstab` + `relatorios.py:398-412`):
   apenas **% de linha**; INNER JOIN duplo (só quem respondeu ambas);
   múltipla escolha gera produto cartesiano (defeito conhecido).
3. **Cruzamentos multidimensionais** (`services/multidimensional_cross.py`):
   o único motor com bases explícitas — `total_entrevistas`, `base_valida`
   (valor utilizável em todas as dimensões), `base_pai`, `percentual_pai`,
   `percentual_total`; unidade = entrevista (`set` de `coleta_id`);
   `__SEM_RESPOSTA__` opcional; múltipla escolha tratada corretamente com
   warning de soma >100%; tratamento de alvo ONE_VS_REST com
   `valores_excluidos` e `valores_preservados`.
4. **Análise de Lideranças** (`services/lideranca_analytics.py:136-157`):
   único lugar com "base válida = entrevistas com resposta reportável para a
   pergunta-alvo" explícita; usa `Decimal`; recortes filtrados devolvem
   apenas taxas, nunca votos absolutos; indisponibilidade declarada em vez de
   número inventado (padrão ADR-025/029).

**O Potencial de Crescimento deve seguir a família 3+4** (entrevistas como
unidade, bases explícitas, indisponibilidade declarada), jamais a família 1+2.

### 4.6 Resposta múltipla

`Resposta.valor_resposta` de `MULTIPLA_ESCOLHA` é uma lista JSON serializada
em uma linha (o parser `_response_values` também aceita linhas separadas,
com dedup e flag de duplicata). Efeitos:

- uma entrevista contribui para várias categorias → somas podem exceder 100%;
- o denominador correto é ENTREVISTAS (não respostas), como no motor
  multidimensional;
- o Relatório Simples e o Crosstab 2D não tratam múltipla corretamente
  (limitação registrada; não é escopo deste MVP corrigi-los);
- o mapa geo recusa múltipla com 422 (ambiguidade cartográfica).

Regra proposta para o domínio: sinais baseados em pergunta múltipla (caso
típico: rejeição múltipla) usam denominador de entrevistas elegíveis e
declaram `soma_pode_exceder_100` como warning estrutural.

### 4.7 Espontânea

Mecanismo existente e reutilizável (ADR-019): `eh_resposta_espontanea` +
`categorias_resposta_espontanea` + `mapeamentos_resposta_espontanea`
(por pesquisa, soft delete, índices únicos parciais sobre ativos),
normalização única (`utils/response_normalization.py`: casefold + NFKD +
remoção de pontuação), resolução por
`crud.get_active_spontaneous_mapping_for_report` +
`crud.resolve_reportable_response_value`; sem match → categoria literal
`"Não categorizada"`, que entra no denominador como qualquer outra.

O Potencial de Crescimento DEVE consumir este mecanismo sem duplicar lógica.
Warning obrigatório quando a taxa de "Não categorizada" na pergunta-sinal
ultrapassar um limiar configurável (curadoria incompleta contamina o sinal).

### 4.8 Fronteira com crosstab/relatórios (reutilização)

Reutilizar (contratos estáveis):

- `services/filtros_universo.py` — semântica de filtro OR dentro da pergunta,
  AND entre perguntas (ADR-026), com `carregar_valores_reportaveis` em lote;
- `crud.resolve_reportable_response_value` / `canonicalizar_valor_categorico`
  / mapeamento espontâneo;
- `_response_values` (parse de múltipla) e o padrão de bases explícitas do
  motor multidimensional;
- `crud.classificar_coletas_por_setor` (ST_Covers, setores
  `RELATORIO|AMBOS`, buckets `classificados`/`conflito_setor`/`sem_setor`/
  `sem_coordenada`);
- padrão `Decimal` + arredondamento só na apresentação
  (`lideranca_analytics`);
- limites de segurança (`core/analytics_config.py`) como precedente;
- gates comerciais `require_module`/`require_feature` (prontos, não aplicados
  a rotas produtivas) e `Permissao.INTELIGENCIA_VER`.

NÃO confundir: crosstab/cruzamentos = DESCRIÇÃO (contagens/percentuais sem
leitura); Potencial de Crescimento = INTERPRETAÇÃO ANALÍTICA (comparação
contra referência + evidências + alertas). O módulo novo não altera os
contratos descritivos validados (ADR-018).

Divergência registrada (não resolver aqui): o filtro `setor_ids` do
Simples/Crosstab usa `ST_Intersects` sobre `localizacao_inicio`, enquanto
Mapas/Cruzamentos/Lideranças usam `ST_Covers` sobre
`coalesce(localizacao_inicio, localizacao_fim)`. O motor novo deve usar a
regra oficial (`ST_Covers` + coalesce), a mesma dos Mapas Estratégicos.

### 4.9 Território disponível

Duas hierarquias independentes, sem inferência automática (ADR-031):

- **Operacional/analítica da Pesquisa:** `Setor` (POLYGON, `finalidade`
  OPERACAO/RELATORIO/AMBOS, `municipio_territorio_id` opcional), composição
  eleitoral declarada `SetorTerritorioEleitoral` (N:N com bairros da Base);
- **Base Eleitoral versionada (Projeto):** `BaseEleitoral` (status VALIDADA
  exigido para cálculo) → `TerritorioEleitoral` (ESTADO, MUNICIPIO, BAIRRO,
  LOCALIDADE, LOCAL_VOTACAO, SECAO; `eleitorado_apto`) + parâmetros de
  projeção humanos (`comparecimento_estimado`, `percentual_votos_validos`,
  ADR-025).

Localização individual: `coletas.localizacao_inicio/fim` (POINT). O
TERRITÓRIO DA AMOSTRA (setor analítico onde a entrevista caiu, via regra
espacial oficial) é distinto da LOCALIZAÇÃO INDIVIDUAL (coordenada do ponto
de coleta) — o produto nunca deve apresentar precisão territorial além do
setor/município analiticamente resolvido, e deve propagar os buckets
`SEM_SETOR`, `TERRITORIO_SOBREPOSTO` e `sem_coordenada` como categorias
técnicas, nunca descartando em silêncio.

---

## 5. Unidade de análise

**Proposta: 1 `Coleta` = 1 observação**, com peso implícito 1 no MVP
(Seção 17). Justificativas: é a unidade dos motores corretos existentes
(multidimensional, lideranças); não existe entidade Respondente; a
idempotência (`uq_coletas_company_client_uuid`) já previne duplicidade.

Tratamento de exceções (regra proposta):

| Caso | Situação real nos dados | Regra proposta |
|---|---|---|
| Entrevista incompleta/desistência/recusa | não vira `Coleta`; fica em `tentativas_campo` | fora do universo; disponível como contexto operacional futuro |
| Resposta parcial (coleta criada, pergunta sem linha em `respostas`) | possível (pulo, territorial, não obrigatória) | entrevista permanece no universo; por sinal, entra em "sem resposta" da pergunta (nunca no denominador do sinal) |
| Pergunta pulada (`proxima_pergunta_id`) | indistinguível de não-resposta (o pulo não é registrado) | idem acima; limitação documentada |
| Pergunta TERRITORIAL não aplicável ao setor | ausência estrutural legítima | idem; warning quando um sinal usa pergunta TERRITORIAL (denominador varia por território) |
| Duplicidade | prevenida por `client_uuid` | nada a fazer |
| Coleta invalidada/soft-deleted | **não existe** o conceito | lacuna registrada (Seção 35, Q13); universo = todas as coletas da pesquisa |
| Coleta não sincronizada | não existe no servidor até sincronizar | fora do universo por construção; relevante para reprodutibilidade (Seção 33) |

## 6. Universo analítico

**UNIVERSO ANALÍTICO** = conjunto de `Coleta` elegíveis para UMA execução da
análise:

```
Universo analítico =
  coletas da Pesquisa (tenant validado: Pesquisa ⋈ Projeto ⋈ company_id
                       + filtro_company_acessivel nas coletas)
  ∩ filtros estruturais da configuração (setor, agente, respostas de perguntas)
```

Distinções obrigatórias (padrão ADR-027, já provado no mapa geo):

```
total_universo             coletas da pesquisa (tenant)
universo_analitico         após filtros estruturais         ← denominador de escala
universo_elegivel          após regra de elegibilidade do potencial (Seção 10)
segmento                   recorte do universo elegível em análise
base_valida_do_sinal       entrevistas do segmento com resposta reportável
                           na pergunta do sinal              ← denominador do sinal
```

NÃO confundir amostra da pesquisa com segmento analisado; NÃO usar
denominador móvel (a seleção de categorias nunca redefine a base — ADR-020 e
ADR-027 se aplicam integralmente).

## 7. Candidatura-alvo

**Fato:** não existe entidade Candidato. "Acácio Favacho" existe nos dados
apenas como: (a) `opcoes.texto` de perguntas categóricas; (b) categoria
espontânea (`categorias_resposta_espontanea.nome`). Grafias distintas já são
unificadas pelo mecanismo espontâneo; em categóricas, o rótulo canônico é o
texto da opção.

**Contrato conceitual proposto — `TargetCandidacy`:** a candidatura-alvo é
uma DECLARAÇÃO DE CONFIGURAÇÃO, não uma inferência:

- `rotulo` — nome de exibição (ex.: "Acácio Favacho");
- `cargo` — texto declarado (não existe entidade Cargo);
- `valores_por_pergunta` — para cada pergunta-sinal configurada, o conjunto
  explícito de valores reportáveis que representam esta candidatura naquela
  pergunta (padrão já provado por `planos_cota_perfil.sexo_valores`);
- pertence a uma `GrowthAnalysisConfiguration` (portanto a uma Pesquisa).

O roadmap já prevê "semântica de opções e candidato canônico" como evolução;
este contrato não a bloqueia: quando existir candidato canônico, os
`valores_por_pergunta` passam a ser derivados dele.

## 8. Cenário eleitoral

**Fato:** não existe entidade Cenário. Porém uma Pesquisa PODE conter
múltiplas perguntas de intenção para o mesmo cargo (cenário 1/cenário 2,
estimulada/espontânea, 1º/2º voto de Senado) — hoje distinguíveis apenas
pelo texto e pela ordem, e `papel_analitico` não diferencia.

**Contrato conceitual proposto — `ElectoralScenario`:** o cenário é a
ESCOLHA CONFIGURADA de qual pergunta de intenção (e quais perguntas de
sinal associadas) participam de uma execução. Uma configuração referencia
exatamente um cenário; comparar cenários = executar duas análises. Eleições
com dois votos (Senado) são dois cenários. Isso evita regra genérica sem
dados que a sustentem, e mantém o denominador estável dentro de cada análise.

## 9. Sinais eleitorais — visão geral

Árvore conceitual do produto (norte para Prompts 02–08):

```
POTENCIAL DE CRESCIMENTO
        │
        ├── UNIVERSO ELEGÍVEL          (quem pode ser conquistado)
        │
        ├── AFINIDADE                  (o quanto o segmento se aproxima)
        │      ├── segunda opção
        │      ├── ausência de rejeição
        │      └── outros sinais disponíveis
        │
        ├── MOBILIDADE                 (o quanto o voto ainda pode mudar)
        │      ├── indecisão
        │      └── voto não cristalizado
        │
        ├── ESCALA                     (quanto o segmento representa)
        │      ├── n bruto
        │      ├── base ponderada (futuro; hoje = n bruto)
        │      └── participação no universo elegível
        │
        └── EVIDÊNCIA                  (por que e com que confiança)
               ├── referência (baseline)
               ├── delta pp
               ├── lift
               ├── incerteza
               └── alertas metodológicos
```

Suporte dos dados reais a cada peça:

| Peça | Suporte hoje | O que falta (Prompt 02) |
|---|---|---|
| Universo elegível | coletas + `filtros_universo` + tratamento de alvo | configurar pergunta de intenção + valores da candidatura + valores de indecisão/exclusão |
| Segunda opção | pergunta pode existir; `papel_analitico=SEGUNDA_OPCAO` disponível | vincular pergunta e valores da candidatura; política p/ ausência |
| Rejeição | idem (`REJEICAO`); frequentemente MULTIPLA_ESCOLHA — parser correto existe | idem; decidir papel (evidência/penalização/exclusão) |
| Indecisão | valores existem só como texto de opção | taxonomia por LISTAS EXPLÍCITAS de valores (sem heurística) |
| Cristalização | `papel_analitico=DECISAO_VOTO` disponível; pergunta pode não existir | vincular pergunta + valores "não definitivo" |
| Perfil | perguntas comuns; só sexo/idade têm precedente estruturado (cotas) | declarar dimensões de segmentação + faixas/valores |
| Território | setores analíticos + classificação espacial oficial + buckets técnicos | escolher nível (setor/município) e setores participantes |
| n bruto / participação | suportado integralmente | — |
| Base ponderada | **NÃO suportada** (não há peso) | fora do MVP; contrato preparado (Seção 17) |
| Referência/delta/lift | computável a partir das bases | escolher referência default |
| Incerteza | computável de forma simples (proporção não ponderada) | decidir método e apresentação |
| Explicabilidade | padrões existentes (bases explícitas, warnings, indisponibilidade declarada) | estrutura `GrowthEvidence` |

## 10. Intenção de voto (SINAL — INTENÇÃO ATUAL)

Papel duplo:

1. **Definir o universo elegível.** Recomendação: eleitor que JÁ declara voto
   na candidatura-alvo na pergunta de intenção do cenário NÃO pertence ao
   universo de crescimento — pertence à futura análise de Consolidação da
   Base. Implicação: a taxa de intenção atual continua sendo exibida como
   contexto, mas os sinais de crescimento são calculados sobre os elegíveis
   (não-eleitores atuais do alvo). Decisão aberta Q01 (Seção 35) — inclui a
   sub-decisão de indecisos pertencerem ao universo elegível (recomendado:
   sim, são o núcleo do crescimento).
2. **Contexto de leitura do segmento** (intenção do alvo e dos adversários no
   segmento, como evidência descritiva).

Pergunta de intenção é o único sinal proposto como OBRIGATÓRIO (Seção 32).

## 11. Rejeição (SINAL — REJEIÇÃO)

Formatos possíveis nos dados: escolha única ("qual candidato você não
votaria de jeito nenhum?"), múltipla (vários candidatos), escala
("poderia votar / não votaria"), ou inexistente. Múltipla é o formato mais
comum e o parser correto já existe (Seção 4.6).

Alternativas de papel (comparadas; decisão humana Q02):

| Papel | Prós | Contras |
|---|---|---|
| A) Exclusão do universo elegível | universo "conquistável" mais honesto | rejeição declarada não é imutável; reduz base; acopla decisão forte sem validação |
| B) Penalização em score | conveniente comercialmente | exige fórmula arbitrária — vetado pelo MVP (Seção 34) |
| C) Dimensão separada de afinidade | preserva transparência; comparável à referência | mais informação para o usuário digerir |
| D) Evidência descritiva | zero arbitrariedade | não ordena nada sozinha |

**Recomendação:** C+D no MVP — a taxa de rejeição ao alvo no segmento é uma
dimensão de afinidade invertida (menor rejeição relativa = sinal favorável),
sempre apresentada com a referência; exclusão (A) apenas como opção de
configuração explícita, desligada por padrão. Sem penalização numérica (B).

## 12. Segunda opção (SINAL — SEGUNDA OPÇÃO)

Metodologicamente, é o indicador mais direto de proximidade/conversão
potencial disponível em pesquisa de opinião. Porém a pergunta pode não
existir na pesquisa.

**Recomendação (Q03): sinal OPCIONAL quando disponível, com degradação
graciosa** (Seção 32) — a análise executa sem ele, declarando
`SINAL_NAO_CONFIGURADO`, e o nível de evidência do resultado cai (menos
sinais de afinidade disponíveis). Não deve ser critério obrigatório: isso
inviabilizaria o produto em pesquisas reais que não perguntam segunda opção.
NS/NR na segunda opção segue a taxonomia da Seção 13 (fora do numerador,
dentro da base válida do sinal conforme política configurada).

## 13. Indecisão — taxonomia conceitual

**Fato:** nenhuma dessas categorias tem representação estrutural; são textos
de opção ("Indeciso", "NS/NR", "Branco/Nulo", "Nenhum", "Não sei"…), e a
ADR-027 veda classificação heurística no servidor.

Taxonomia conceitual proposta (cada classe = LISTA EXPLÍCITA de valores
reportáveis, configurada por pergunta no Prompt 02; a UI pode sugerir por
heurística visível, o usuário confirma — padrão `valores_preservados`):

| Classe | Exemplo típico | Papel no universo elegível | Papel no denominador do sinal |
|---|---|---|---|
| INDECISO_DECLARADO | "Indeciso", "Ainda não decidi" | DENTRO (núcleo do crescimento) | dentro |
| BRANCO_NULO | "Branco/Nulo", "Nenhum" | DENTRO (configurável) | dentro |
| NAO_SABE_NAO_RESPONDEU | "NS/NR", "Não sabe" | configurável | política própria (Q09) |
| NAO_PRETENDE_VOTAR | "Não vou votar" | FORA (configurável) | fora |
| SEM_RESPOSTA_TECNICA | ausência de linha em `respostas` (`__SEM_RESPOSTA__`) | mantém-se no universo | fora do denominador do sinal, sempre |
| NAO_CATEGORIZADA (espontânea) | sem mapeamento ativo | warning de curadoria | dentro, com warning |

Invariável: essas classes NÃO são automaticamente equivalentes e nunca são
somadas em silêncio; toda fusão é decisão de configuração visível.

## 14. Cristalização / decisão do voto

`papel_analitico=DECISAO_VOTO` já existe para marcar perguntas do tipo "seu
voto é definitivo ou pode mudar?". Papel conceitual proposto: dimensão de
MOBILIDADE — a taxa de voto não cristalizado entre eleitores de adversários
no segmento, comparada à referência, qualifica o quão disputável o segmento
é. Sem fórmula: entra como evidência ao lado das demais, com a mesma
mecânica (taxa, referência, delta, base). Opcional com degradação graciosa.

## 15. Perfil sociodemográfico

**Fato:** perfil é pergunta, não campo estruturado. Somente sexo e idade têm
tratamento de primeira classe (via `planos_cota_perfil` — ponteiros +
dicionário de valores + parsing de idade numérica/categórica). Escolaridade,
renda, religião, ocupação etc. existem apenas se a pesquisa as perguntar,
como perguntas comuns.

Classificação proposta: perfil atua prioritariamente como **SEGMENTAÇÃO**
(e filtro), nunca como sinal eleitoral nem como causa de voto. A
configuração declara as dimensões de segmentação (pergunta + eventual
agrupamento de valores/faixas); o motor não presume que qualquer dimensão
exista. Reutilizar o plano de cotas quando existir (sexo/idade prontos).

## 16. Território

- Nível de segmentação territorial do MVP: **setor analítico**
  (`finalidade RELATORIO|AMBOS`) e **município** (via
  `municipio_territorio_id`/composição resolvida), usando exclusivamente a
  regra espacial oficial (`classificar_coletas_por_setor`, ST_Covers,
  coalesce início/fim);
- buckets técnicos SEMPRE reportados: `SEM_SETOR`, `TERRITORIO_SOBREPOSTO`
  (conflito — não duplicar entrevista), `SEM_COORDENADA`;
- a ESCALA territorial pode ser enriquecida com `eleitorado_apto` da Base
  Eleitoral VALIDADA quando a composição do setor estiver declarada
  (ADR-029/031/032); sem vínculo declarado, o eleitorado é INDISPONÍVEL —
  nunca aproximado por área (ADR-030) nem por nome;
- o produto não infere precisão territorial inexistente: nada abaixo de
  setor; coordenadas individuais não geram "micro-segmentos".

## 17. Pesos e ponderação (`WeightingDefinition`)

Estado real: sem peso, sem desenho amostral (Seção 4.4). Política proposta:

- **MVP executa NÃO PONDERADO por declaração explícita**, nunca por omissão:
  a configuração carrega `WeightingDefinition = {modo: NAO_PONDERADO}` e todo
  resultado estampa o alerta "análise não ponderada; percentuais refletem a
  amostra coletada, não a população";
- o contrato de domínio já nasce com os DOIS conceitos (n bruto e base
  ponderada) para não bloquear a evolução; no modo NAO_PONDERADO,
  base ponderada ≡ n bruto;
- quando peso existir (evolução: coluna em `coletas` ou pós-estratificação
  via cotas/Base Eleitoral), o modo muda para PONDERADO com origem declarada
  — decisão futura, fora deste MVP;
- é PROIBIDO tratar ausência de peso como "peso 1 silencioso" sem o alerta, e
  é PROIBIDO usar as metas de cotas como pesos implícitos (cotas são
  orientativas, `models.py:1326`).

## 18. Base bruta vs base ponderada — invariável

```
N_BRUTO         = quantidade real de entrevistas (len do set de coleta_id)
BASE_PONDERADA  = soma dos pesos (no MVP: ≡ N_BRUTO)
```

INVARIÁVEL: todo número exibido carrega o n bruto que o sustenta. Nunca usar
apenas base ponderada para aparentar tamanho ("n bruto = 7, expandido = 25"
continua sendo evidência de 7 entrevistas). Política de base mínima opera
sobre o N BRUTO.

## 19. Segmentos

**SEGMENTO ANALÍTICO** = subconjunto do universo elegível definido por
valores de dimensões de segmentação (perfil e/ou território):

- simples: 1 dimensão ("Mulheres"; "Setor X");
- multivariado: interseção de 2+ dimensões ("Mulheres ∧ 18–34 ∧ Macapá").

Risco de explosão combinatória: sexo × idade × escolaridade × município ×
… cresce geometricamente e multiplica comparações (Seção 26). Necessidades
futuras registradas (SEM implementar): profundidade máxima (precedente:
`MAX_CROSS_DIMENSIONS=8`; para este produto a recomendação inicial é bem
menor — Q08), seleção explícita de variáveis, base mínima, pruning por base,
ranking controlado.

## 20. Baseline / população de referência

Candidatas:

| Ref. | Descrição | Prós | Contras |
|---|---|---|---|
| A | média geral da pesquisa | familiar | mistura eleitores do alvo (não elegíveis) |
| B | **média do universo elegível** | compara "conquistáveis com conquistáveis"; coerente com a definição do produto | exige explicar o conceito ao usuário |
| C | média do mesmo território | isola efeito territorial | bases pequenas; nem sempre disponível |
| D | média do mesmo perfil | isola efeito de perfil | idem |
| E | candidatura concorrente | leitura competitiva | muda a pergunta respondida |

**Recomendação (Q10): B como referência default**, com a referência SEMPRE
nomeada no resultado (nunca "a média" sem qualificação — mesmo princípio da
legenda rotulada do ADR-027). C/D/E como evoluções de configuração.

## 21. Métricas candidatas (descritivas)

Para um sinal S, segmento G, referência R (todas sobre entrevistas, com
denominadores explícitos):

```
taxa_segmento    = n(S favorável em G elegível com resposta) / base_valida_sinal(G)
taxa_referencia  = idem sobre R
delta_pp         = taxa_segmento − taxa_referencia          (pontos percentuais)
lift             = taxa_segmento / taxa_referencia          (adimensional)
participacao     = n_bruto(G) / n_bruto(universo elegível)  (escala)
```

Nenhuma delas é "a métrica final de potencial" — são evidências. Denominador
de cada taxa é SEMPRE registrado junto do número.

- **Delta pp:** leitura direta, não explode com baseline pequena; recomendado
  como métrica primária de exibição.
- **Lift:** bom para comparar sinais de magnitudes diferentes; perigoso com
  baseline pequena (10%→18% = 1,8x parece enorme); interpretação permitida:
  "o sinal aparece 1,8 vez mais frequentemente neste segmento que na
  referência" — NUNCA "80% mais chance de votar".
- **Recomendação (Q05):** calcular e armazenar ambos; exibir delta pp como
  primário e lift como secundário, sempre com n bruto e incerteza.

## 22. Eixo A — AFINIDADE

"Quão acima/abaixo da referência os sinais favoráveis aparecem no segmento?"
Evidências: taxas por sinal (segunda opção, não-rejeição, contexto de
intenção), delta pp, lift, incerteza. Sem fórmula consolidada de agregação
entre sinais no MVP — os sinais são apresentados lado a lado (Seção 34).

## 23. Eixo B — ESCALA

"Quanto do universo elegível o segmento representa?" Evidências: n bruto,
base ponderada (≡ n bruto no MVP), participação percentual no universo
elegível; opcionalmente `eleitorado_apto` territorial como contexto
(Seção 16), claramente separado da amostra. PROIBIDO converter escala em
projeção de votos dentro deste produto (projeção pertence à Gestão de
Lideranças, com parâmetros humanos — ADR-025).

## 24. Eixo C — CONVERTIBILIDADE

Conceito: combinação de proximidade (segunda opção), ausência de rejeição e
mobilidade (indecisão/não cristalização) num indicador de "facilidade de
conversão". **Recomendação (Q06-adjacente): NÃO existir como número no MVP**
— é o eixo com maior risco de virar score arbitrário. No MVP, a
convertibilidade é narrada pelas evidências individuais (o usuário vê
segunda opção alta + rejeição baixa + voto não cristalizado e conclui);
uma métrica composta fica para evolução, se aprovada metodologicamente.

## 25. Incerteza estatística

Pergunta estatística que o MVP precisa responder: *"a diferença observada
entre o segmento e a referência é maior do que a variação esperada pela
amostragem?"* — separando SIGNIFICÂNCIA (estatística) de RELEVÂNCIA
(estratégica: delta pequeno em segmento gigante pode importar mais que delta
grande em n=12).

Restrições reais: sem peso, sem desenho amostral (sem estratos/PSU) →
qualquer intervalo assume amostragem aleatória simples, o que deve ser
declarado como limitação. NÃO afirmar intervalos de desenho complexo.

Alternativas (decisão humana Q07):

| Método | Adequação ao MVP |
|---|---|
| IC de proporção (Wilson) por taxa + IC da diferença | simples, honesto, explicável; recomendado |
| Teste de diferença de proporções (z) | equivalente prático ao IC; pode complementar |
| Qui-quadrado/exato | menos legível para o usuário-alvo |
| Regressão/bootstrap/modelos | sofisticação sem pergunta que a exija no MVP |

**Recomendação:** IC de Wilson (95%) por taxa + classificação qualitativa da
evidência (ex.: "diferença maior que a incerteza combinada" vs "dentro da
margem"), sem p-valores na UI. ERRO AMOSTRAL: é PROIBIDO aplicar o ±X% global
da pesquisa a subgrupos — cada segmento tem incerteza própria, crescente
quando n cai; o produto exibe a incerteza do segmento, não a da pesquisa.

## 26. Limitações amostrais, base mínima e múltiplas comparações

**Política de base mínima (`MinimumBasePolicy`)** — sem número inventado
nesta rodada. Alternativas para decisão humana (Q04):

| Política | Prós | Contras |
|---|---|---|
| Mínimo absoluto fixo (ex.: n≥30) | simples | arbitrário; cego ao contexto |
| Dois limiares: suprimir < n₁; warning entre n₁ e n₂ | gradação honesta | dois números a definir |
| Configurável por análise com default metodológico | flexível; auditável | permite abuso se default fraco |
| Derivado da metodologia da pesquisa | ideal teórico | metadado inexistente hoje |

**Recomendação:** dois limiares (suprimir/alertar) configuráveis com
defaults definidos pelos responsáveis metodológicos; supressão nunca
silenciosa (segmento aparece como SUPRIMIDO_BASE_INSUFICIENTE, padrão
"ausência declarada" das ADR-024/029).

**Múltiplas comparações / overfitting:** varrer centenas de segmentos e
destacar os extremos gera achados ocasionais garantidos. Mecanismos futuros
registrados (sem decidir agora): profundidade limitada, base mínima,
ranking com penalização, validação entre ondas, FDR ou equivalente. Para o
MVP, a mitigação mínima recomendada é: profundidade 1–2 dimensões +
território (Q08), base mínima ativa e alerta padrão de exploração múltipla
no rodapé de qualquer ranking.

## 27. Multivariado (futuro)

O documento conceitual prevê evolução além de cruzamentos. Avaliação de
propósito (nenhuma escolhida nem implementada):

| Técnica | Problema que resolveria | MVP precisa? |
|---|---|---|
| Árvores de decisão | descoberta automática de segmentos | não — segmentos configurados bastam |
| Regressão logística | efeito ajustado de cada variável | não — exige leitura estatística madura |
| Modelos hierárquicos | território com poucos dados | não |
| Clustering | personas | não |
| Regras de associação | combinações frequentes | não |

O MVP entrega comparação descritiva controlada; multivariado entra apenas
quando houver pergunta de produto que o exija e validação metodológica.

## 28. Evidência ≠ resultado

- **RESULTADO** (`GrowthFinding`): "Mulheres de 18–34 em Macapá aparecem
  como segmento de atenção."
- **EVIDÊNCIAS** (`GrowthEvidence[]`): segunda opção acima da referência
  (com taxa, referência, delta, n); rejeição abaixo da referência (idem);
  voto pouco cristalizado (idem); base suficiente (n bruto, participação).

Todo resultado carrega as evidências que o sustentam e os alertas que o
limitam. É isso que permite responder "POR QUE este segmento apareceu?".

## 29. Explicabilidade — invariável de domínio

Nenhum resultado pode ser apenas "SCORE = 78". Todo resultado apresenta:
dados que sustentam a leitura, referência comparativa nomeada, bases
(n bruto sempre), sinais utilizados e não utilizados (com motivo), e
limitações. Registrado como INVARIÁVEL: um contrato de saída que não consiga
reconstruir essa cadeia está errado por definição.

## 30. Linguagem permitida e proibida

**Permitida** (cautelosa, comparativa, amostral):

- "Este segmento apresenta sinal de segunda opção acima da média da base
  elegível."
- "Há maior concentração relativa de eleitores não rejeitantes neste grupo."
- "O segmento merece atenção estratégica." / "Os dados sugerem oportunidade
  relativa."
- "A diferença observada é maior que a incerteza estimada das duas bases."

**Proibida** (causal/determinística/projetiva sem suporte):

- "Esses eleitores vão votar no candidato." / "O candidato ganhará X votos."
- "Esse grupo tem 80% de chance de converter."
- "A campanha deve obrigatoriamente…"
- "A variável X causa voto em Y." / "A pesquisa prova que…"

Esta lista vira requisito de QA (Prompt 07): textos gerados pelo produto são
validados contra o padrão proibido.

## 31. Contrato de domínio proposto

Conceitos (nomes ajustáveis; NENHUMA classe implementada nesta rodada):

| Conceito | Responsabilidade | Campos conceituais | Invariantes | Origem dos dados |
|---|---|---|---|---|
| `GrowthAnalysis` | uma execução da análise | configuração, snapshot, findings, warnings globais, versão do motor | reproduzível dada (config, dados, versão) | derivado |
| `TargetCandidacy` | candidatura-alvo declarada | rótulo, cargo, `valores_por_pergunta` | valores explícitos; nunca inferidos por texto | configuração |
| `ElectoralScenario` | escolha das perguntas do cenário | pergunta de intenção; perguntas de sinal associadas | 1 cenário por análise | configuração |
| `AnalyticalUniverse` | coletas elegíveis | pesquisa, filtros estruturais, regra de elegibilidade, contagens (total/analítico/elegível) | tenant validado; denominador imóvel após configurado | `coletas` + `filtros_universo` |
| `SignalDefinition` | um sinal eleitoral | papel (`PapelAnalitico`), pergunta, mapeamento de valores (favorável/desfavorável/indecisão), obrigatoriedade | sem heurística textual; ausência ≠ zero (`SINAL_NAO_CONFIGURADO`/`SINAL_NAO_DISPONIVEL`) | configuração + `perguntas` |
| `SegmentDefinition` | recorte do universo elegível | dimensões (pergunta+valores/faixas; território) | profundidade limitada; identidade estável | configuração |
| `TerritoryDefinition` | nível e conjunto territorial | nível (setor/município), setores analíticos, política p/ SEM_SETOR/sobreposição/sem coordenada | regra espacial oficial; buckets sempre reportados | `setores`, classificação PostGIS |
| `WeightingDefinition` | política de ponderação | modo (NAO_PONDERADO no MVP), origem futura | ausência declarada, nunca implícita | configuração |
| `MinimumBasePolicy` | supressão/alerta por base | limiar de supressão, limiar de alerta | opera sobre n bruto; supressão declarada | configuração |
| `ReferencePopulation` | baseline de comparação | tipo (default: universo elegível), rótulo | sempre nomeada no resultado | derivado |
| `GrowthEvidence` | uma evidência | sinal, taxa, referência, delta pp, lift, n bruto, base ponderada, incerteza, direção | denominadores explícitos; n bruto sempre presente | motor (Prompt 03) |
| `GrowthFinding` | um resultado por segmento | candidatura, segmento, território, bases, evidências, nível de evidência, warnings, interpretação textual estruturada | explicável (Seção 29); linguagem da Seção 30 | motor |
| `StatisticalWarning` | um alerta tipado | código, severidade, contexto | nunca suprimido na apresentação | motor |
| `AnalysisSnapshot` | identidade temporal da execução | pesquisa/onda, momento, contagem de coletas, versão do motor, hash/versão da configuração | permite comparação futura entre ondas sem implementar tracking | motor |

Códigos iniciais de `StatisticalWarning` (extensíveis): `NAO_PONDERADO`,
`BASE_PEQUENA`, `SUPRIMIDO_BASE_INSUFICIENTE`, `SINAL_NAO_CONFIGURADO`,
`SINAL_NAO_DISPONIVEL`, `ALTA_TAXA_NAO_CATEGORIZADA`,
`ALTA_TAXA_SEM_RESPOSTA`, `PERGUNTA_TERRITORIAL_NO_SINAL`,
`MULTIPLA_ESCOLHA_SOMA_PODE_EXCEDER_100`, `TERRITORIO_SOBREPOSTO`,
`SEM_SETOR_PRESENTE`, `MULTIPLAS_COMPARACOES`, `DESENHO_AMOSTRAL_ASSUMIDO_AAS`.

## 32. Configuração vs resultado

- **CONFIGURAÇÃO** = o que o usuário/metodologista pediu
  (`GrowthAnalysisConfiguration`, base do Prompt 02): pesquisa, candidatura
  (`TargetCandidacy`), cenário, sinais (`SignalDefinition[]` para intenção,
  rejeição, segunda opção, cristalização), dimensões de perfil, território,
  filtros estruturais, `WeightingDefinition`, `MinimumBasePolicy`,
  `ReferencePopulation`. Não é JSON definitivo ainda.
- **RESULTADO** = o que o motor encontrou (`GrowthAnalysis` →
  `GrowthFinding[]`), Prompt 03.

**Sinais obrigatórios vs opcionais** — dois modelos comparados:

| Modelo | Composição | Utilidade | Risco |
|---|---|---|---|
| MÍNIMO | intenção + perfil | roda em quase toda pesquisa eleitoral | leitura pobre (só "quem não vota no alvo por segmento") |
| ENRIQUECIDO | + rejeição + segunda opção + cristalização | leitura estratégica real | exigi-lo inviabiliza pesquisas sem essas perguntas |

**Recomendação:** OBRIGATÓRIO apenas intenção de voto (define elegibilidade)
+ ≥1 dimensão de segmentação; todos os demais sinais OPCIONAIS com
**degradação graciosa**: distinção explícita entre `SINAL_NAO_CONFIGURADO`
(usuário não configurou) e `SINAL_NAO_DISPONIVEL` (pesquisa não tem a
pergunta), nunca tratados como taxa zero; o `GrowthFinding` declara com
quais sinais foi construído e o nível de evidência reflete isso.

**Filtros:** reutilizar integralmente `filtros_respostas` (OR/AND de
`filtros_universo`), `setor_ids`, `agente_ids` — sem duplicar mecanismos.

## 33. Reprodutibilidade e auditabilidade

Requisitos registrados (implementação futura):

- mesma (pesquisa, configuração, dados, versão do motor) → mesmo resultado;
- conceito de `engine_version`/`analysis_version` no snapshot (sem campo
  criado agora);
- dados mudam com sincronizações tardias do Mobile: o `AnalysisSnapshot`
  registra momento e contagem de coletas para explicar divergências;
- auditabilidade: o resultado guarda/reconstrói configuração, métricas,
  denominadores, referências, sinais e versão — respondendo "por que esse
  segmento apareceu?".

## 34. Score único vs ranking transparente

| Arquitetura | Explicabilidade | Robustez | Comercial | Risco metodológico | Auditabilidade |
|---|---|---|---|---|---|
| A) Ranking por métricas transparentes | alta | alta | exige educação do cliente | baixo | alta |
| B) Score composto 0–100 | baixa | frágil (pesos arbitrários) | sedutor | alto (falsa precisão) | baixa |

**Recomendação (Q06): A no MVP.** Ordenação por regra documentada e simples
(ex.: delta pp do sinal primário disponível, com escala e base como
qualificadores exibidos — regra final no Prompt 02/03 com aprovação
metodológica), nunca por número opaco. Categorias ALTO/MÉDIO/BAIXO (Q06b):
somente se derivadas de regras documentadas e aprovadas; alternativa
preferida no MVP: ranking contínuo + evidências, sem rótulo de tricotomia —
thresholds não são inventados nesta rodada.

## 35. Questões metodológicas em aberto (exigem aprovação humana)

- **Q01** — eleitor que já vota no alvo entra no universo de crescimento?
  (Recomendação: não — vai para Consolidação da Base. Sub-decisão: indecisos
  e branco/nulo entram? Recomendação: sim/configurável.)
- **Q02** — rejeição exclui, penaliza ou é dimensão separada?
  (Recomendação: dimensão separada; exclusão opcional off por default.)
- **Q03** — segunda opção obrigatória ou opcional? (Recomendação: opcional
  com degradação graciosa.)
- **Q04** — números da base mínima (limiares de supressão e alerta).
- **Q05** — delta pp, lift ou ambos? (Recomendação: ambos; delta primário.)
- **Q06** — score único? Categorias ALTO/MÉDIO/BAIXO? (Recomendação: não;
  ranking transparente + evidências.)
- **Q07** — método de incerteza. (Recomendação: IC Wilson 95% + leitura
  qualitativa; sem p-valor na UI.)
- **Q08** — profundidade multivariada de segmento no MVP. (Recomendação:
  1–2 dimensões de perfil + território.)
- **Q09** — taxonomia de indecisão: quais classes existem e o papel de NS/NR
  no denominador de cada sinal.
- **Q10** — população de referência default. (Recomendação: universo
  elegível.)
- **Q11** — política de ponderação: aceitar formalmente o MVP não ponderado
  com alerta permanente? Roteiro futuro de pesos (coluna vs
  pós-estratificação)?
- **Q12** — sinais em pergunta MULTIPLA_ESCOLHA: denominador de entrevistas
  elegíveis com warning de soma >100%? (Recomendação: sim, semântica do
  motor multidimensional.)
- **Q13** — o produto precisa de invalidação de coleta (não existe hoje)?
  Se sim, é pré-requisito de dados fora deste módulo.
- **Q14** — pergunta TERRITORIAL como sinal: permitir com warning ou vetar
  no MVP? (Denominador varia por território.)
- **Q15** — taxa máxima aceitável de "Não categorizada" numa pergunta-sinal
  espontânea antes de suprimir o sinal.

## 36. Matriz de decisão

| # | Decisão | Alt. A | Alt. B | Recomendação | Pró | Contra | Impacto | Aprovação? |
|---|---|---|---|---|---|---|---|---|
| Q01 | universo elegível | excluir eleitores do alvo | incluir todos | A | conceito limpo de "crescimento" | reduz base | define TODOS os denominadores | SIM |
| Q02 | papel da rejeição | dimensão separada | exclusão do universo | A (B opcional off) | transparente, reversível | usuário lê mais números | motor + UI | SIM |
| Q03 | segunda opção | opcional | obrigatória | A | roda em qualquer pesquisa | leitura mais fraca sem ela | configuração | SIM |
| Q04 | base mínima | dois limiares configuráveis | fixo único | A | gradação honesta | dois números a definir | supressão de resultados | SIM (números) |
| Q05 | métrica comparativa | ambos (delta primário) | só uma | A | leituras complementares | mais campos | contrato de evidência | SIM |
| Q06 | score | ranking transparente | score 0–100 | A | auditável, defensável | menos "vendável" | arquitetura do produto | SIM |
| Q07 | incerteza | IC Wilson + leitura qualitativa | testes formais/nada | A | honesto e explicável | assume AAS (declarado) | motor + UI | SIM |
| Q08 | profundidade | 1–2 dim. + território | livre até 8 | A | controla comparações múltiplas | menos exploração | motor + config | SIM |
| Q09 | indecisão | listas explícitas por classe | heurística de texto | A | ADR-027; auditável | trabalho de configuração | configuração | SIM (taxonomia) |
| Q10 | referência | universo elegível | pesquisa inteira | A | compara conquistáveis | conceito novo p/ usuário | todas as comparações | SIM |
| Q11 | ponderação | não ponderado declarado | bloquear sem peso | A | produto viável hoje | limitação real | validade externa | SIM |
| Q12 | múltipla escolha | denominador entrevistas + warning | vetar múltipla como sinal | A | rejeição múltipla é comum | soma >100% exige educação | motor | SIM |

## 37. Exemplo puramente ILUSTRATIVO (números fictícios)

Pesquisa fictícia: 1.000 entrevistas. Candidato A — intenção atual: 15%.
Universo elegível (não votam em A hoje): 850 entrevistas.

Segmento: Mulheres 18–34 — n bruto 180 no universo elegível; participação
21,2% do elegível.

| Sinal | Segmento | Referência (elegível) | Delta | Lift |
|---|---|---|---|---|
| Segunda opção = A | 18% (base válida 165) | 10% | +8,0 pp | 1,8 |
| Rejeição a A | 12% (base válida 172) | 22% | −10,0 pp | 0,55 |

Leitura ACEITÁVEL (modelo para Prompt 05/06):

> "Entre mulheres de 18 a 34 anos, a candidatura A aparece como segunda
> opção com frequência superior à referência analisada (18% contra 10% da
> base elegível; +8 pontos percentuais, n=165), ao mesmo tempo em que a
> rejeição observada é menor (12% contra 22%; −10 pontos, n=172). A
> combinação sugere um segmento de atenção para aprofundamento estratégico,
> respeitando a base amostral e a incerteza da pesquisa. Análise não
> ponderada."

Leitura PROIBIDA: "A candidatura converterá esse segmento." Nenhum score é
produzido; os números acima são fictícios e não representam resultado real.

Contrato de saída humana (orienta Prompts 05/06): SEGMENTO (quem é) → BASE
(quantas entrevistas sustentam) → SINAL (o que está acima/abaixo da
referência) → ESCALA (participação no universo) → EVIDÊNCIAS (por que
apareceu) → ALERTAS (que limitação considerar).

## 38. Recomendações para o Prompt 02 (Configuração)

1. Generalizar o padrão `planos_cota_perfil` (ponteiro para pergunta +
   dicionário explícito de valores) para `SignalDefinition` e
   `TargetCandidacy.valores_por_pergunta`.
2. Aproveitar `papel_analitico` como SUGESTÃO de pré-preenchimento na UI de
   configuração (nunca como decisão automática do motor); avaliar ampliar o
   enum apenas se necessário (ex.: AVALIACAO), sem migração nesta fase.
3. Definir o JSON/entidade de `GrowthAnalysisConfiguration` cobrindo todos
   os conceitos da Seção 31–32, com validação de que perguntas pertencem à
   pesquisa e valores existem entre os reportáveis (usar
   `carregar_valores_reportaveis`).
4. Especificar a UX de classificação de valores (favorável/indecisão/NS-NR)
   com sugestão heurística visível e confirmação humana (padrão
   `valores_preservados`).
5. Incorporar as decisões aprovadas da matriz (Seção 36) como defaults.
6. Manter a configuração por Pesquisa (onda), com identidade estável para o
   futuro tracking entre ondas (via `AnalysisSnapshot`).

## 39. Itens fora do MVP

- Ponderação/expansão amostral e desenho complexo (Q11 — roteiro futuro);
- score composto e categorias ALTO/MÉDIO/BAIXO sem regra aprovada;
- convertibilidade como métrica numérica;
- técnicas multivariadas (árvores, regressão, clustering…);
- tracking entre ondas (o snapshot apenas o viabiliza);
- correção de múltiplas comparações formal (FDR);
- Consolidação da Base, Resistência, Indecisos como produtos próprios;
- projeção de votos (permanece na Gestão de Lideranças);
- correções nos motores legados (múltipla escolha no Simples/Crosstab 2D,
  divergência ST_Intersects/ST_Covers) — registradas como débitos, não são
  escopo deste módulo;
- invalidação de coleta (Q13) — pré-requisito de dados a decidir fora.

## 40. Critério de conclusão e decisão GO/NO-GO

Checklist do Prompt 01: estrutura real auditada (✓ Seção 4), tipos de
pergunta (✓ 4.2), pesos (✓ 4.4), relatórios/crosstab (✓ 4.5/4.8), sinais
modelados (✓ 9–14), universo (✓ 6), candidatura/cenário (✓ 7–8), segmento
(✓ 19), território (✓ 16), bases (✓ 18), métricas discutidas sem fórmula
arbitrária (✓ 21–24), sem causalidade indevida (✓ 30), domínio proposto
(✓ 31), explicabilidade (✓ 29), questões humanas (✓ 35), matriz (✓ 36),
exemplo (✓ 37), nenhum código funcional alterado (✓).

**DECISÃO: GO — MODELO METODOLÓGICO SUFICIENTEMENTE DEFINIDO PARA
DISCUSSÃO/APROVAÇÃO.**

Não é GO para implementação. Próximo passo: revisão humana das questões
Q01–Q15 com os responsáveis metodológicos; somente depois, Prompt 02
(Modelo de Configuração da Análise).

Lacunas que NÃO bloqueiam a modelagem, mas condicionam o Prompt 02:
ausência de peso (Q11), ausência de invalidação de coleta (Q13), taxonomia
de indecisão dependente de configuração (Q09), enum `papel_analitico` sem
vínculo a candidato (resolvido por `TargetCandidacy.valores_por_pergunta`).

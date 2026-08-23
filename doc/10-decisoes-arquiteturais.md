# Pesquisa360 — Decisões Arquiteturais

**Status:** registro de decisões pós-estabilização  
**Formato:** ADR simplificado

## ADR-001 — Backend como fonte da verdade

O backend é responsável por autenticação, autorização, multitenancy, vínculo de agente e validação de regras críticas.

Consequências:

- Web e Mobile não enviam `company_id`.
- Web e Mobile não definem tenant.
- Criação de coleta usa `current_user.id`.
- Toda rota sensível valida `current_user.company_id`.

## ADR-002 — Multitenancy por `company_id`

O isolamento é feito por `company_id`, associado a usuários e projetos.

```text
Usuário autenticado -> company_id -> projetos -> pesquisas -> dados
```

## ADR-003 — Soft delete para entidades estruturais

Projetos, pesquisas e perguntas devem usar soft delete para preservar histórico.

Status atual:

- Projeto: soft delete.
- Pesquisa: soft delete.
- Pergunta: soft delete.
- Setor: delete físico no estado atual.

Decisão futura: avaliar soft delete para setores.

## ADR-004 — Geometrias não retornam PostGIS cru

Nenhum endpoint deve retornar `WKBElement` ou objeto PostGIS cru.

Converter para:

- `{lat, lng}`;
- `poligono: [{lat, lng}]`;
- GeoJSON serializável.

## ADR-005 — `lat/lng` como padrão futuro

Novas rotas devem usar `lat/lng`.

Algumas rotas legadas usam `lon`. Manter compatibilidade até migração controlada.

## ADR-006 — Mobile offline-first

O app mobile deve funcionar sem internet.

Consequências:

- banco local com Drift/SQLite;
- upload posterior de coletas;
- `client_uuid` é gerado uma vez no Mobile, persistido localmente e reutilizado em retries;
- o Backend garante idempotência por `company_id + client_uuid` e retorna a coleta existente em reenvios do mesmo agente;
- `foi_offline` representa condição no momento da coleta;
- sincronização explícita.

## ADR-007 — Monitoramento atual é de coletas sincronizadas

A tela atual mostra coletas já enviadas ao servidor.

Não incluso:

- posição viva do agente;
- heartbeat;
- tracking contínuo.

Evolução: polling Web e depois heartbeat Mobile.

## ADR-008 — Geocoding não roda em GET de tela

Reverse geocoding não deve ser chamado ao abrir monitoramento.

Regra:

```text
criação/backfill -> calcula endereço
monitoramento -> retorna endereço salvo
```

## ADR-009 — Crosstab 2D separado dos Cruzamentos Estratégicos

O crosstab cruza duas perguntas categóricas e mantém seu contrato estável. Os
Cruzamentos Estratégicos usam endpoint e schemas próprios para 2 a N dimensões
ordenadas, ligadas pela mesma `coleta_id`.

Nenhum dos fluxos substitui o outro: combinações pareadas continuam no
crosstab; navegação hierárquica pertence à Central de Inteligência.

## ADR-010 — Frontend usa services para API

Chamadas HTTP devem ficar em `src/api`.

Exemplo:

```ts
projectsService.getSetores(projectId, surveyId)
reportsService.getSimpleReport(surveyId)
```

## ADR-011 — React Router usa `projectId` e `surveyId`

No frontend:

```text
:projectId
:surveyId
```

No backend:

```text
projeto_id
pesquisa_id
```

## ADR-012 — Leaflet controls adicionados uma vez

Controles de desenho Leaflet/Geoman devem ser adicionados uma única vez por instância de mapa.

## ADR-013 — Checkpoints antes de novas features

Após estabilizar fluxos, criar checkpoint/tag antes de novas implementações.

## ADR-014 — Prompts econômicos para IA/Codex

Usar prompts pequenos, com escopo fechado, arquivo provável e erro específico.

Template:

```text
Modo econômico.
Escopo: Frontend Web apenas.
Arquivo: ...
Erro: ...
Patch: ...
Não mexer em: ...
Depois: build/test/status.
```

## ADR-015 — Documentação acompanha o contrato real

Docs devem refletir o comportamento validado, não apenas a intenção original.

Quando uma rota é ajustada, atualizar:

- `02-contratos-api.md`;
- `09-checklist-testes.md`;
- `08-roadmap.md`, se aplicável.

## ADR-016 — Importação administrativa de Shapefile por CLI

Importação administrativa de Shapefile por CLI, convertida para `Polygon`
EPSG:4326 e submetida às mesmas regras de tenant da aplicação. A ferramenta
reutiliza o contrato interno e o CRUD de criação de setor, sem endpoint ou
migration novos. `MultiPolygon` é rejeitado enquanto a coluna permanecer
`Geometry("POLYGON", srid=4326)`.

## ADR-017 - Separacao entre territorio operacional e territorio analitico

Status: decisao aprovada para implementacao futura. Nao representa contrato
HTTP ja disponivel no estado atual.

O dominio de setores passara a distinguir a finalidade territorial por valores
canonicos:

```text
OPERACAO
RELATORIO
AMBOS
```

Regras aprovadas:

- Setor `OPERACAO` e usado para planejamento e execucao da coleta, pode ter
  agente responsavel, participa de meta/cota, tolerancia/geofence operacional,
  envio ao Mobile e monitoramento operacional.
- Setor `RELATORIO` e usado para analise territorial, mapas e relatorios
  executivos. Agente, meta/cota e tolerancia operacional nao sao obrigatorios.
- Setor `RELATORIO` exclusivo nao deve ser enviado ao Mobile como setor
  operacional nem aparecer nos fluxos operacionais atuais.
- Setor `AMBOS` pode exercer as duas funcoes; quando atuar operacionalmente,
  deve cumprir as regras de operacao.
- Todos os setores existentes devem ser considerados `OPERACAO` para preservar
  compatibilidade com Mobile, planejamento, monitoramento e cotas.
- O Backend continua sendo a fonte da verdade para tenant, finalidade,
  permissoes e classificacao territorial. Web e Mobile nao escolhem
  `company_id`.
- A classificacao espacial futura sera feita no Backend/PostGIS,
  preferencialmente com `ST_Covers`, usando a localizacao inicial da coleta como
  referencia principal e a localizacao final como fallback.
- Coleta que nao estiver em nenhum setor analitico deve ser classificada como
  `SEM_SETOR`.
- Sobreposicao/conflito entre setores analiticos nao deve duplicar entrevistas.
- Edicoes futuras de nome, geometria e finalidade devem preservar historico:
  edicao territorial nao deve reclassificar silenciosamente dados historicos.
- Importacao e edicao futuras devem reutilizar a infraestrutura existente de
  setores/Shapefile, acrescentando a escolha de finalidade sem criar tenant no
  cliente.

## ADR-018 — Motor multidimensional descritivo separado

- O crosstab 2D permanece estável e com contrato inalterado.
- O cruzamento multidimensional possui endpoint e schemas próprios.
- A Central de Inteligência consome `papel_analitico` e
  `metadados_analiticos` das perguntas sem exigir enum nativo no banco.
- O motor atual entrega somente evidência descritiva bruta, baseada em
  entrevistas distintas ligadas pela mesma `coleta_id`.
- A separação evita que a evolução de navegação, IA ou interpretação altere
  relatórios já validados.

## ADR-019 — Categorização espontânea compartilhada

Os Cruzamentos Estratégicos reutilizam
`get_active_spontaneous_mapping_for_report` e
`resolve_reportable_response_value`. Não há fuzzy matching. Valores sem
mapeamento aparecem como “Não categorizada”. Na modelagem atual, a
categorização ativa é vinculada à pesquisa, não individualmente à pergunta.

## ADR-020 — Filtros não renormalizam percentuais

`filtros_respostas` é aplicado depois do cálculo da árvore, das bases e dos
percentuais. O filtro reduz os ramos exibidos, mas preserva `base_valida`,
`base_pai`, `percentual_total` e `percentual_pai` originais.

## ADR-021 — “Sem resposta” é categoria técnica explícita

Quando solicitado, o backend representa ausência pela chave
`__SEM_RESPOSTA__` e pelo rótulo “Sem resposta”. O Web envia
`incluir_sem_resposta=true`; o default legado `false` continua aceito pelo
contrato.

## ADR-022 — Inteligência contextual separada de Relatórios

Os Cruzamentos Estratégicos ficam na Central de Inteligência da pesquisa. A
Central de Relatórios mantém Resumo e Crosstab 2D, e a rota global
`/inteligencia` continua dedicada ao contexto territorial. O modo Relatório
usa impressão nativa do navegador em HTML/SVG/CSS A4.

## ADR-023 — Base Eleitoral oficial e privada, fixada pelo Projeto

A Base Eleitoral é dado de referência versionado, independente dos setores
operacionais e das ondas de campo.

```text
base_eleitoral.company_id IS NULL   -> base oficial/global
base_eleitoral.company_id = N       -> base privada do tenant N
```

Consequências:

- a base oficial é única por `uf + ano + versao` e legível por todos os tenants;
- a base privada é única por `uf + ano + versao + company_id`;
- a visibilidade de leitura é `company_id IS NULL OR company_id = current_user.company_id`;
- base privada de outro tenant é invisível, e acesso inválido retorna 404 (ADR-001);
- o Web e o Mobile nunca informam `company_id` ao criar ou consultar base eleitoral;
- o vínculo com a campanha é `Projeto -> ProjetoBaseEleitoral -> BaseEleitoral`;
- não existe vínculo direto `Pesquisa -> BaseEleitoral`.

Motivo do vínculo no Projeto: `Projeto` é a campanha e `Pesquisa` é a onda. Todas
as ondas de uma campanha devem comparar contra a mesma versão eleitoral, senão
indicadores territoriais entre ondas ficam incomparáveis.

Um Projeto pode manter histórico de vínculos, mas apenas uma base é `principal`
por vez, garantido por índice único parcial.

A regra que um Projeto do tenant A não use base privada do tenant B não é
expressa por constraint SQL: exigiria duplicar `company_id` na tabela de vínculo.
Ela é contrato da camada de serviço, **implementado na Fase 3A** em
`services/base_eleitoral._assegurar_base_elegivel_para_projeto`, que responde 404
como se a base não existisse.

### Complementos da Fase 3A

- **Base para cálculo exige `VALIDADA`.** Existem duas operações distintas:
  `obter_base_principal_projeto_para_conferencia` aceita qualquer status visível;
  `obter_base_principal_projeto_para_calculo` recusa qualquer coisa diferente de
  `VALIDADA` com `BaseEleitoralNaoValidadaError`. Leitura histórica de base
  `SUBSTITUIDA` continua possível, mas nunca pela porta de cálculo.
- **Validação é humana.** Importação com zero divergências termina em
  `IMPORTADA`, nunca em `VALIDADA`. A promoção exige ação explícita e só é aceita
  quando não resta nenhum território em `EM_CONFERENCIA`.
- **Importação não sobrescreve versões.** O SHA-256 do arquivo é persistido em
  `importacao_base_eleitoral.hash_arquivo`; hash repetido é recusado com
  referência à importação anterior. Não há `UPDATE` em massa nem `DELETE` de
  versão: conteúdo novo gera nova versão de base.
- **Divergência não é reconciliada automaticamente.** Resumo diferente da soma
  dos detalhes preserva o valor declarado, marca `eleitorado_apto_divergente` e
  move o território para `EM_CONFERENCIA`, sem alterar nenhum filho. A resolução
  humana registra valor anterior, valor final, justificativa, usuário e data em
  `metadados`, e marca a divergência do lote como resolvida sem apagá-la.
- **Permissões.** Base oficial (`company_id IS NULL`) só aceita escrita de
  Superadmin; base privada aceita Gerente ou Superadmin do tenant proprietário.
  Agente nunca importa nem valida. Nenhum perfil novo foi criado.
- **Absorção do legado.** `services/base_eleitoral_legado` lê `bairros` e
  `locais_votacao` sem alterá-los e produz uma base privada
  `fonte = MIGRACAO_LEGADO`, sempre em `EM_CONFERENCIA`. Município de bairro só é
  atribuído com evidência única em `locais_votacao`; zero ou múltiplas evidências
  viram divergência. O campo legado `votos` nunca é tratado como
  `eleitorado_apto`.

## ADR-024 — Liderança política é do Projeto; setor e cota são da onda

`LiderancaPolitica` (pessoa cadastrada para a campanha) tem raiz em `Projeto`.

```text
Projeto
  └── LiderancaPolitica          nome, localizacao (POINT, uso futuro), ativo
       ├── LiderancaPesquisaConfig    por onda: setor_id + cota_votos_validos
       └── LiderancaTerritorioEleitoral   bairros da Base Eleitoral (N:N)
```

Motivo: `Projeto` é a campanha e `Pesquisa` é a onda. Um `setor_id` direto na
liderança a prenderia ao setor de uma onda específica — e `Setor` tem delete
físico e `cascade delete-orphan` a partir da Pesquisa. Por isso setor e cota
vivem em `lideranca_pesquisa_config`, com `UNIQUE(lideranca_id, pesquisa_id)`:
renegociar a meta na Onda 2 não reescreve a Onda 1.

Consequências:

- o tenant deriva de `LiderancaPolitica -> Projeto -> company_id`; o cliente
  nunca envia `company_id`, e recurso de outro tenant responde 404;
- `setor_id` usa `ON DELETE SET NULL`: apagar o setor não apaga o histórico;
- exclusão de liderança é soft delete (`ativo=false`);
- os bairros são a âncora territorial **estável**, porque sobrevivem às ondas.

### Setor e Bairro são dimensões complementares

`Setor` define o universo **amostral** (quais coletas entram na taxa).
`Bairros` definem o universo **eleitoral** (quantos aptos a liderança
representa). O sistema não infere um do outro: sem geometria de bairro na Base,
qualquer casamento espacial ou textual seria adivinhação. A associação é
administrada pelo usuário; `setor_territorio_eleitoral` continua não existindo.

Sem setor configurado, o escopo amostral é a Pesquisa inteira, e a resposta
declara `escopo_amostral: PESQUISA` — a UI não pode fingir recorte territorial.

### Cota é em votos válidos, não em aptos

`cota_votos_validos` é deliberadamente distinto de `Setor.meta`, que significa
meta de **coletas**. A projeção é:

```text
aptos_dos_bairros × comparecimento_estimado × percentual_votos_validos
  = votos_validos_projetados
votos_validos_projetados × taxa_da_resposta_alvo
  = votos_projetados_alvo
gap_plus = votos_projetados_alvo − cota_votos_validos
```

Sem `comparecimento_estimado` ou `percentual_votos_validos` na Base, não há
projeção: o resultado vem `null` com motivo `PARAMETROS_ELEITORAIS_AUSENTES`.
Assumir 100% transformaria aptos em votos válidos — exatamente o erro que a
modelagem se propôs a impedir. Os demais motivos são `SEM_COTA`,
`SEM_TERRITORIO_ELEITORAL`, `BASE_ELEITORAL_NAO_VALIDADA`,
`SEM_RESPOSTAS_VALIDAS` e `PERGUNTA_ALVO_INVALIDA`.

Gap/Plus **não é persistido**: depende da onda, da pergunta alvo, da cota e dos
parâmetros da Base, então gravar produziria dado stale.

### Filtros adicionais são diagnósticos, não projetivos

O alvo (pergunta + resposta) é o desempenho medido. Filtros adicionais
(sexo, idade…) recortam uma subamostra cuja população no território é
desconhecida. Por isso `recorte_filtrado` devolve **apenas taxas** — nunca votos
absolutos nem Gap/Plus — e adicionar um filtro **não altera** o
`resultado_principal`. Há teste de regressão dedicado a essa garantia.

`LIDERANCA_SETOR` e `MAPA_LIDERANCA_SETOR` continuam significando a opção mais
votada em um setor, sem qualquer relação com esta entidade.

## ADR-025 — Parâmetros de projeção são decisão humana, editáveis em base validada

**Contexto.** `comparecimento_estimado` e `percentual_votos_validos` convertem
eleitores aptos em votos válidos projetados:

```
aptos × comparecimento_estimado × percentual_votos_validos = votos válidos projetados
```

Sem eles, a Gestão de Lideranças não projeta votos nem calcula Gap/Plus.

**Decisão.** Os dois parâmetros são configurados explicitamente por
`PATCH /base-eleitoral/{base_id}/parametros-projecao`, restrito a
Gerente/Superadmin (base oficial exige Superadmin). O sistema **nunca** os
preenche por convenção, nem no import, nem por inferência a partir da pesquisa,
nem por consulta externa. `NULL` é o estado legítimo de "não configurado" e
produz `PARAMETROS_ELEITORAIS_AUSENTES`, jamais 1.0 ou 100%.

**Base VALIDADA aceita a alteração.** São parâmetros de projeção: não alteram
território, eleitorado operacional, hierarquia, divergências, `data_referencia`,
hash nem a conferência da fonte, e por isso não invalidam a base nem mudam seu
status. A UI avisa que a mudança afeta imediatamente as projeções. Base
`SUBSTITUIDA` é uma versão morta e recusa a escrita.

**Forma canônica.** O banco guarda a fração em `Numeric(5,4)` (`0.8000`), nunca
o percentual inteiro (`80`). A UI conversa em percentual e converte na borda.

**Distinção.** Peso Eleitoral mede participação territorial no universo
operacional; os parâmetros de projeção transformam aptos em votos. São conceitos
diferentes e ocupam blocos distintos do Workspace.

**Lacuna conhecida.** O projeto não possui infraestrutura de auditoria de
alterações administrativas. A mudança destes parâmetros, portanto, **não é
historiada** — apenas `atualizado_em` muda. Guardar histórico em campo
inadequado seria pior que a ausência; a trilha depende de uma decisão futura
sobre auditoria transversal.

## ADR-026 — Mapa de Respostas Georreferenciadas: o filtro define o universo

**Rota.** `POST /relatorios/pesquisas/{pesquisa_id}/mapas/respostas-georreferenciadas/`

`POST` porque o recorte aceita N dimensões de filtro, o que não cabe em query
string. O prefixo segue o padrão já consolidado dos Mapas Estratégicos.

**Payload.**

```json
{
  "pergunta_id": 57,
  "valores": ["Candidato A", "Candidato B"],
  "filtros_respostas": [
    {"pergunta_id": 52, "valores": ["Feminino"]},
    {"pergunta_id": 53, "valores": ["25-34", "35-44"]}
  ],
  "setor_ids": [33],
  "agente_ids": []
}
```

`company_id` é recusado (`extra="forbid"`): o tenant vem do JWT. Configuração
visual — cores, zoom, tamanho de marcador — também não pertence ao contrato.

**Resposta.** `resumo` (universo/filtrado/com coordenada/sem coordenada),
`categorias` (valor + total) e `pontos` (`coleta_id`, `lat`, `lng`, `valor`,
`setor_id`). Nenhum dado pessoal do entrevistado.

**Semântica dos filtros.** OR dentro dos valores da mesma pergunta, AND entre
perguntas distintas. Isto é **restrição de universo**, não geração de tabela
cruzada — o cruzamento em árvore continua em `multidimensional_cross`. A
implementação vive em `services/filtros_universo.py`, compartilhada com a
Gestão de Lideranças: uma semântica, um código.

**Tipos suportados.** Apenas categóricas de **resposta única**
(`ESCOLHA_SIMPLES` e seus aliases) e espontâneas já categorizadas.
`MULTIPLA_ESCOLHA` é recusada com 422: uma coleta produz um ponto, e duas
categorias no mesmo ponto seriam ambíguas cartograficamente. Suportá-la exige
uma decisão de produto sobre como representar isso, e fica para uma fase futura.

**Regra de coordenada.** `coalesce(localizacao_inicio, localizacao_fim)`, a
mesma `_coleta_ponto_referencia` dos demais Mapas Estratégicos — a mesma coleta
cai no mesmo lugar em todos eles. Coordenada ausente ou fora de faixa entra em
`total_sem_coordenada`; nunca vira `(0,0)` nem é descartada em silêncio.

**Setor.** Pertencimento pela regra espacial oficial
(`classificar_coletas_por_setor` → `ST_Covers`), em lote. Coletas não têm coluna
`setor_id`, e o cliente não determina pertencimento.

**Multitenancy.** `_validar_pesquisa_relatorio` (Pesquisa ⋈ Projeto ⋈
company_id) mais `Coleta.company_id`. Recurso de outro tenant responde **404**,
nunca 403.

**Limitações do MVP.** Sem paginação: o mapa analítico precisa representar todo
o universo filtrado, e truncar em silêncio produziria leitura territorial falsa.
Medição no DEV: 1000 coletas → 696 pontos em **10 queries constantes**. Se o
volume crescer a ponto de exigir limite, as alternativas a avaliar são cluster,
Canvas, simplificação de payload e recorte por viewport — nenhuma implementada
aqui.

## ADR-027 — Agrupamento em "Outros" e os dois denominadores da legenda

Evolução do ADR-026. Contrato **aditivo**: sem os campos novos, requisição e
resposta continuam byte-a-byte as de antes.

### O problema

`total_filtrado` misturava dois recortes de naturezas diferentes: os filtros
**estruturais** (setor, agente, dimensões de resposta) e a **seleção de
categorias** (`valores` / `valores_secundarios`). Consequência: ao destacar dois
candidatos, o universo caía de 1.000 para 48 e todo percentual passava a ter
denominador móvel — mudava a cada clique do usuário.

### Universo analítico

```
total_universo             pesquisa inteira, só o tenant
total_universo_analitico   após os filtros ESTRUTURAIS, antes da seleção  ← novo
total_filtrado             coletas efetivamente representadas
total_sem_categoria        no universo analítico, sem valor único em A     ← novo
```

`total_universo_analitico` é o **denominador do "% do universo"**. Sai de
`len(coleta_ids)` logo após `aplicar_filtros_respostas`, onde os estruturais já
rodaram e a seleção ainda não — **zero consulta adicional**.

`total_sem_categoria` existe para o cliente não precisar deduzir a diferença por
subtração: universo analítico = representados + sem categoria (+ `total_sem_par`
no modo cruzado).

### Seleção: filtrar ou destacar

`agrupar_nao_selecionadas` muda o **papel** de `valores`:

| | papel de `valores` | não selecionadas |
|---|---|---|
| `false` (padrão) | FILTRA | saem do mapa |
| `true` | DESTACA | viram o balde "Outros" |

O caminho sem agrupamento é **exatamente** o legado — a interseção com os
permitidos acontece antes da exigência de valor único, o que importa para
espontâneas, onde uma coleta pode ter mais de um valor reportável. Trocar essa
ordem mudaria o resultado de perguntas espontâneas em silêncio.

Agrupar **não afrouxa a regra cartográfica**: uma coleta sem valor único
continua fora do mapa. E, no cruzamento, "Outros" reúne quem **respondeu** outra
coisa — nunca quem não respondeu, que permanece em `total_sem_par`.

### Identidade da categoria

A identidade é o **par `(valor, agrupado)`**, nunca o texto sozinho. Uma
pergunta pode ter uma opção real chamada "Outros"; ela e o balde convivem na
mesma resposta como duas linhas distintas. O cliente pinta o balde de cinza pela
**flag**, não pelo rótulo.

### Branco/Nulo e NS/NR

O projeto **não tem classificador semântico** — nem no backend nem no frontend.
Inferir essas categorias por texto no servidor mudaria em silêncio a semântica
de respostas que o instituto precisa ler separadas.

Por isso o servidor **não adivinha**: recebe `valores_preservados` explícito. A
interface sugere os candidatos por heurística de texto, exibe a sugestão marcada
e deixa o usuário ajustar. A decisão viaja explícita; a heurística é visível.

`valores_preservados` sem `agrupar_nao_selecionadas` é **422**: preservar de um
balde que não existe é instrução sem efeito, e aceitá-la em silêncio esconderia
um erro de chamada.

### Os dois percentuais da legenda (frontend)

Denominadores diferentes **sempre rotulados**, nunca `percentual1`/`percentual2`:

| percentual | denominador | quando |
|---|---|---|
| `% do universo` | `total_universo_analitico` | sempre |
| `% do recorte` | soma das linhas exibidas | agrupamento desligado |
| `% do grupo` | total da categoria de A na linha | agrupamento ligado |

O segundo percentual é **suprimido** quando não informa nada: denominador
ausente, a linha é o próprio denominador (seria sempre 100%), ou o denominador
coincide com o universo (repetiria o número ao lado).

### Performance

Com agrupamento, o payload de pontos cresce para o universo analítico inteiro —
é o objetivo declarado da funcionalidade ("as demais respostas continuam no
mapa"). O número de consultas **não muda**: continua constante, como no ADR-026.
A ausência de paginação segue valendo pelas mesmas razões.

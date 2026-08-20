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

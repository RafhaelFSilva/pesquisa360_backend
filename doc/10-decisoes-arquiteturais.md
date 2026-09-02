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

## ADR-052 — Web capabilities são estado UX, não segurança

**Decisão.** Web carrega capabilities via `GET /usuarios/me/modulos/` em Zustand
com estado explícito (`idle|loading|ready|error`). Gates Web fazem fail-closed
(bloqueiam em loading/error), mas Backend é a autoridade final. Web nunca
inventa capacidades; Backend nunca autoriza baseado em decisão Web.

Consequências:
- logout limpa `modulesStore` completamente
- troca de usuário recarrega módulos
- Empresa A não vaza para Empresa B
- URL direta sem módulo retorna UX clara (redireciona ou mostra "não disponível")

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

## ADR-028 — Posicionamento é atributo da liderança, não entidade

`BASE`, `OPOSICAO` e `INDEFINIDA` são valores de uma coluna em
`liderancas_politicas`. Não existem `liderancas_oposicao`, `oposicoes` nem
`liderancas_base`.

```text
LiderancaPolitica
  └── posicionamento   VARCHAR NOT NULL DEFAULT 'INDEFINIDA'
                       CHECK IN ('BASE','OPOSICAO','INDEFINIDA')
```

Motivo: uma liderança de oposição é a **mesma coisa** que uma liderança de base
— tem nome, setor por onda, cota de votos válidos e bairros da Base Eleitoral.
Duplicar a entidade duplicaria CRUD, multitenancy, analytics e migrations para
descrever uma única diferença: de que lado ela está. O que muda é um atributo,
então é um atributo.

Consequências:

- o CRUD é um só; filtrar por campo político é
  `GET /projetos/{id}/liderancas?posicionamento=OPOSICAO`, e a ausência do
  parâmetro devolve todas, como antes;
- o tenant continua derivando de `LiderancaPolitica -> Projeto -> company_id`;
  posicionamento não é chave de acesso e não abre exceção ao 404 de outro tenant;
- o domínio segue `String + CHECK` (como `TerritorioEleitoral.tipo`), não
  `sa.Enum`: o valor viaja como texto do início ao fim e o banco recusa o resto.

### O padrão é INDEFINIDA, nunca BASE

Liderança já cadastrada recebe `INDEFINIDA` pelo `server_default` da migration.
Presumir `BASE` atribuiria em silêncio um campo político a milhares de registros
que ninguém classificou — e o erro seria invisível, porque um dado errado com
cara de dado certo não gera pergunta. `INDEFINIDA` é um estado honesto: diz que
a informação não existe, em vez de inventá-la.

Pelo mesmo motivo, criar sem informar `posicionamento` resulta em `INDEFINIDA`,
e o formulário abre nesse valor.

### Posicionamento e Gap/Plus são conceitos distintos

Posicionamento diz **de que lado** a liderança está. Gap/Plus diz **como ela
está performando** contra a cota. São eixos independentes: existe liderança de
oposição em PLUS e liderança de base em GAP.

Os dois convivem no mapa sem compartilhar variável nem paleta — posicionamento
usa azul/vermelho/cinza, Gap/Plus usa verde/âmbar/teal. A regra de negócio lê
`lideranca.posicionamento`; **cor é representação, não fonte de verdade**:

```ts
switch (lideranca.posicionamento) { case 'BASE': ... }   // correto
if (markerColor === 'red') { /* oposição */ }            // errado
```

---

## ADR-029 — Cobertura eleitoral usa o universo do Setor, ou não existe

O percentual de cobertura de uma liderança responde: **quanto do eleitorado
daquele Setor ela cobre**.

```text
cobertura_percentual = eleitorado_coberto_lideranca / eleitorado_total_do_setor
```

O denominador é o Setor. Município, projeto e estado **não são substitutos**.
Trocar o denominador muda a pergunta que o número responde: 3.000 eleitores
sobre um setor de 10.000 é 30% de cobertura; os mesmos 3.000 sobre um município
de 200.000 é 1,5% — e nenhum dos dois números é "aproximadamente" o outro. Um
fallback silencioso para o município produziria percentuais que parecem baixos
por falha da liderança quando na verdade são baixos por troca de universo.

### Sem setor confiável, o percentual é indisponível

Quando não há Setor de referência, o sistema mostra o **valor absoluto** de
eleitorado coberto e `cobertura_percentual: null`, com motivo explícito
(`SEM_SETOR_REFERENCIA`). Segue o padrão de indisponibilidade já usado na
análise de liderança (ADR-024): ausência declarada, nunca número inventado.

### Não existe associação Setor × Território Eleitoral

Hoje `Setor` e `TerritorioEleitoral` não têm vínculo formal — nem FK, nem tabela
associativa, nem campo que determine o setor de um território. A ausência é
**deliberada** e está guardada por teste
(`RegressaoEscopoFase2Tests.test_setor_continua_sem_vinculo_eleitoral_automatico`).

Enquanto esse vínculo não existir, o denominador do Setor não é calculável e o
indicador **não deve ser implementado**. Similaridade de nome entre setor e
bairro não é associação auditável.

---

## ADR-030 — Área geográfica e eleitorado são grandezas diferentes

Interseção espacial (`ST_Intersection`) informa **área**. Não informa eleitores.

Converter percentual de área em percentual de eleitorado assume distribuição
uniforme da população pelo território — uma premissa que quase nunca vale: metade
da área de um bairro pode conter 5% dos seus eleitores. O sistema não faz essa
conversão automaticamente.

### Território disputado usa unidades completas

Quando duas lideranças de campos opostos atuam na mesma região, o eleitorado
disputado é a soma dos territórios **em comum**, deduplicados por
`territorio_eleitoral_id`:

```text
Base:     A + B + C
Oposição: B + C + D
Comuns:   B + C  ->  eleitorado(B) + eleitorado(C)
```

O modo geométrico pode informar a área da interseção como dado espacial, mas não
produz eleitorado estimado sem uma metodologia própria e declarada.

## ADR-031 — Composição eleitoral do Setor é declarada, não inferida

`Setor` e `TerritorioEleitoral` são conceitos de origens diferentes: o primeiro
é divisão operacional/analítica da Pesquisa, o segundo pertence à Base
Eleitoral. O sistema **não deduz um do outro**.

```text
Setor  N:N  TerritorioEleitoral   ->  setor_territorio_eleitoral
                                      (setor_id, territorio_eleitoral_id)
                                      UNIQUE(setor_id, territorio_eleitoral_id)
```

A composição é **explícita, administrada pelo usuário e auditável**. Nunca
inferida por geometria, nome, proximidade, interseção, município ou área. Os
dois lados até têm geometria, mas cruzá-las produziria vínculo plausível e não
verificável — e um vínculo errado aqui contamina todo indicador eleitoral que
vier depois.

Sem `company_id`: o tenant deriva de `setor -> pesquisa -> projeto`. Sem
`pesquisa_id`: é derivável por `setor.pesquisa_id`, e denormalizá-lo apenas para
viabilizar um `UNIQUE(pesquisa_id, territorio)` descreveria a regra **errada**
(ver exclusividade, abaixo).

### Somente BAIRRO, por ora

O schema é genérico — aponta para `territorio_eleitoral.id`, de qualquer tipo.
A regra de serviço não é: hoje aceita exclusivamente `BAIRRO`, porque na Base
atual (Amapá 2026) ele é:

- a **menor unidade existente** — `LOCAL_VOTACAO` e `SECAO` têm 0 registros;
- **100% coberto** — 198/198 bairros com `eleitorado_apto > 0`;
- **folha** — 198/198 sem descendentes, logo somá-los nunca duplica eleitor.

`MUNICIPIO` e `ESTADO` são recusados por motivo oposto: são **agregações** dos
bairros (os três níveis somam os mesmos 577.894), então aceitá-los contaria o
mesmo eleitorado duas vezes.

Quando existir base com seções, a estrutura já comporta — muda a regra, não o
schema.

### Não existe rateio parcial de Bairro

O bairro entra inteiro na composição ou não entra. Não há coluna de peso,
percentual ou fração. Dividir um bairro entre dois setores exigiria repartir seu
eleitorado, e a Base não tem unidade menor para sustentar essa divisão —
qualquer repartição seria inventada.

### Um Bairro, um universo analítico

> O mesmo BAIRRO não pode compor dois setores **analíticos** da mesma Pesquisa.

Porque o bairro é indivisível: se entrasse em dois universos analíticos, o mesmo
eleitorado seria contado duas vezes.

"Analítico" é `RELATORIO` ou `AMBOS` — exatamente `crud.FINALIDADES_ANALITICAS`,
o conjunto que `_validar_setores_analiticos` usa para decidir quem entra em
Mapas Estratégicos, Cruzamentos e na análise de lideranças.

| Setor A | Setor B | Mesmo bairro |
|---|---|---|
| RELATORIO | RELATORIO | proibido |
| RELATORIO | AMBOS | proibido |
| AMBOS | AMBOS | proibido |
| OPERACAO | RELATORIO/AMBOS | **permitido** |
| OPERACAO | OPERACAO | **permitido** |

Setor `OPERACAO` fica de fora da regra porque fica de fora dos indicadores: o
filtro por `FINALIDADES_ANALITICAS` acontece antes de qualquer classificação de
coleta, então uma malha operacional pode recortar o território de outro jeito
sem contaminar universo algum. A exclusividade é por **Pesquisa**: outra onda
tem universo próprio e pode reutilizar o mesmo bairro.

### A regra vive no serviço, não num UNIQUE

A exclusividade depende de `setores.finalidade` — coluna de **outra tabela** —,
o que nenhum `UNIQUE` simples expressa. A validação é transacional:

1. trava as linhas de `territorio_eleitoral` envolvidas (`FOR UPDATE`,
   `ORDER BY id` para não deadlockar quando dois conjuntos se cruzam);
2. confere conflito **dentro da mesma transação**;
3. só então substitui os vínculos.

Sem o passo 1, duas requisições simultâneas leem "sem conflito" e ambas gravam.
O `UNIQUE(setor_id, territorio_eleitoral_id)` cobre outra coisa: o mesmo bairro
repetido no mesmo setor.

### Substituição integral e transacional

`PUT .../setores/{id}/territorios` **substitui** a composição — não acumula.
Idempotente: reenviar o mesmo conjunto deixa o mesmo resultado; lista vazia
limpa; ids repetidos são deduplicados. Qualquer recusa (tipo errado, base
errada, conflito) deixa a composição anterior **intacta** — nunca meio salva.

### A mudança de finalidade é porta lateral

Promover um setor de `OPERACAO` para `RELATORIO`/`AMBOS` transforma sobreposição
legítima em dupla contagem. Por isso a promoção é barrada enquanto houver
conflito, no mesmo caminho que altera a finalidade. Rebaixar para `OPERACAO` e
mudar entre finalidades já analíticas seguem livres.

### Risco conhecido: vínculo e base substituída

Trocar a Base principal do Projeto **não** revalida os vínculos existentes: eles
continuam apontando para territórios da base anterior. A exposição é idêntica à
de `lideranca_territorio_eleitoral`, que valida a base no momento da escrita e
não relê depois — e para a qual não existe política. Fica registrado como risco;
não há backfill nem revalidação automática, que seriam decisão de domínio, não
detalhe de implementação.

## ADR-032 — Universo eleitoral do Setor é derivado, nunca persistido

O universo eleitoral de um Setor é a soma do `eleitorado_apto` das unidades
**explicitamente vinculadas** à sua composição. Na Base atual essas unidades são
BAIRROS.

```text
Setor -> setor_territorio_eleitoral -> BAIRRO.eleitorado_apto
                                       SUM (por id único)
```

Calculado **em leitura**, em `setor_territorio.obter_universo_eleitoral_setor`,
que é a fonte única — endpoint, frontend e analytics consomem daqui e ninguém
repete a soma.

Não existe coluna `setores.eleitorado_apto`, tabela de cache nem agregado
gravado. O valor depende de três coisas que mudam por conta própria — a
composição, o `eleitorado_apto` da Base e a Base principal do Projeto — então
persistir a soma criaria um número que envelhece sem avisar ninguém. A soma de
198 inteiros não é o gargalo que justificaria esse risco.

### Ausência não é zero

Todo estado indisponível devolve `eleitorado_apto: null` com motivo explícito.
Zero eleitores é um **resultado**; "não dá para calcular" é outra coisa, e
confundir os dois faria um setor não configurado parecer um setor vazio.

| Estado | Condição | `eleitorado_apto` |
|---|---|---|
| `DISPONIVEL` | base VALIDADA + ≥1 unidade, todas da base atual e com eleitorado | soma |
| `SEM_COMPOSICAO_ELEITORAL` | nenhum vínculo | `null` |
| `COMPOSICAO_BASE_DESATUALIZADA` | ≥1 unidade de Base anterior | `null` |
| `BASE_ELEITORAL_NAO_CONFIGURADA` | projeto sem Base principal | `null` |
| `BASE_ELEITORAL_NAO_VALIDADA` | Base principal fora de `VALIDADA` | `null` |
| `ELEITORADO_TERRITORIO_INDISPONIVEL` | ≥1 unidade com `eleitorado_apto` nulo | `null` |

A precedência vai do contexto para o detalhe — base ausente, base inválida,
composição vazia, composição desatualizada, eleitorado ausente. A Base vem
primeiro porque é a precondição de tudo: sem ela nem dá para julgar se a
composição está desatualizada. Validade é `status == VALIDADA`, a mesma
definição que o resto do motor eleitoral usa; nenhum conceito novo.

### Troca de Base invalida, não apaga

Trocar a Base principal deixa os vínculos anteriores **intactos no banco** e o
universo **indisponível** até reconfiguração explícita pelo usuário.

Os vínculos são preservados por auditabilidade — apagá-los destruiria o registro
do que estava configurado e a chance de o usuário ver o que precisa refazer. E
não há remapeamento automático entre Bases: "Centro" da Base A e "Centro" da
Base B são registros diferentes, e casá-los por nome produziria vínculo
plausível e não verificável.

Basta **uma** unidade da Base anterior para invalidar a composição inteira.
Somar apenas as unidades atuais entregaria um universo parcial com cara de
completo — pior que não responder, porque o número pareceria confiável.
`quantidade_territorios` continua reportando os vínculos reais, o que permite à
UI separar "não configurado" (0) de "configurado e desatualizado" (N).

### Universo individual existe para qualquer finalidade

`OPERACAO`, `RELATORIO` e `AMBOS` têm universo individual. A finalidade não
bloqueia a soma de um setor isolado.

O que **não** existe é consolidação: somar os universos de vários setores seria
outra regra, porque setores `OPERACAO` podem sobrepor a malha analítica e o
mesmo eleitorado entraria duas vezes. Fica para fase própria, com decisão
explícita sobre quais finalidades entram na conta.

### O que o universo ainda não é

`eleitorado_apto` é o único número eleitoral desta fase. Não há cobertura de
liderança, percentual do universo, votos válidos projetados, pressão de cotas
nem ocorrências para meta. A projeção
(`aptos × comparecimento × votos válidos`) pertence a fase posterior — e a soma
aqui é de eleitores aptos, não de votos.

## ADR-033 — Cobertura eleitoral da Liderança é a interseção com o Setor

A cobertura responde: **quanto do universo eleitoral do Setor de referência está
coberto pelos bairros da Liderança**.

```text
L = territórios da Liderança
S = composição eleitoral do Setor da onda

eleitorado_coberto     = SUM(eleitorado_apto de L ∩ S)
cobertura_percentual   = eleitorado_coberto / universo_setor × 100
```

A interseção usa `territorio_eleitoral.id`. Nunca nome, `nome_normalizado`,
município, geometria ou centroide — "Centro" existe em 16 municípios da Base
atual, e casar por nome produziria cobertura plausível e errada.

Derivada em runtime, nada persistido. O denominador vem de
`obter_universo_eleitoral_setor` (ADR-032), fonte única: a soma dos bairros do
Setor não foi copiada para dentro da análise de Lideranças.

### Território fora do Setor não é erro

A Liderança pertence ao **Projeto** e pode atuar além de um Setor específico
daquela onda. Bairros dela fora da composição do Setor simplesmente não entram
naquele denominador.

```text
Liderança: A + B + C + D        Setor: B + C + E
Interseção: B + C               A e D ficam de fora
```

Consequência direta: a cobertura **nunca passa de 100%**, porque o numerador é
subconjunto do denominador por construção. Por isso não há `min(100, x)` — um
percentual acima de 100 seria sinal de defeito de integridade, e escondê-lo
atrás de um clamp transformaria bug em número apresentável.

### Interseção vazia é 0%, ausência é null

A distinção é o coração desta fase:

| Situação | Resultado |
|---|---|
| Liderança tem bairros, Setor tem composição, nenhum em comum | `0` e `0,00%` — **DISPONIVEL** |
| Liderança sem bairros configurados | `null` — `SEM_TERRITORIO_ELEITORAL_LIDERANCA` |
| Liderança sem Setor de referência na onda | `null` — `SEM_SETOR_REFERENCIA` |
| Base inconsistente de qualquer lado | `null` — motivo específico |

Zero é um **resultado**: a informação existe e a cobertura naquele Setor é
efetivamente nenhuma. `null` diz que não há informação para medir. Exibir 0%
para o segundo caso faria "não sei" parecer "não cobre nada".

Sem Setor não há denominador territorial, e município, projeto ou a soma dos
próprios bairros da Liderança responderiam **outra pergunta**.

### Precedência: denominador antes do numerador

```text
1. sem Setor de referência        -> SEM_SETOR_REFERENCIA
2. universo do Setor indisponível -> propaga o motivo do Setor
3. Liderança sem territórios      -> SEM_TERRITORIO_ELEITORAL_LIDERANCA
4. território de Base anterior    -> TERRITORIO_LIDERANCA_BASE_DESATUALIZADA
5. eleitorado ausente na Liderança-> ELEITORADO_TERRITORIO_LIDERANCA_INDISPONIVEL
6. universo do Setor igual a zero -> UNIVERSO_ELEITORAL_ZERO
7. calcula a interseção
```

O denominador vem primeiro porque é a precondição: não adianta reclamar do
numerador enquanto o Setor não tiver universo. Quando o problema é do Setor, o
motivo dele é **propagado** em vez de ganhar um sinônimo — a causa é a mesma e a
UI já sabe explicá-la. Não existem `COBERTURA_SEM_COMPOSICAO` e afins.

`UNIVERSO_ELEITORAL_ZERO` existe porque é alcançável: ADR-032 aceita bairro com
`eleitorado_apto = 0` como valor válido, e um Setor só de bairros zerados fecha
com universo 0.

### Numerador e denominador na mesma Base

Ambos os lados precisam pertencer à Base principal **atual** do Projeto. Um
único território da Liderança em Base anterior invalida o numerador inteiro —
somar só os atuais entregaria cobertura parcial com cara de completa. Os
vínculos permanecem no banco; não há remapeamento automático nem exclusão.

### Correção incorporada: Gap/Plus não mistura mais Bases

A análise de Lideranças somava `eleitorado_apto` de territórios de **qualquer**
Base e projetava com os parâmetros da Base atual — eleitorado de uma Base
multiplicado pelo comparecimento de outra, silenciosamente.

A partir daqui, território fora da Base principal torna o valor eleitoral da
Liderança indisponível (`TERRITORIO_LIDERANCA_BASE_DESATUALIZADA`), o que zera
por consequência `votos_validos_projetados`, `gap_plus`, `status` e
`atingimento_percentual` **naquele cenário específico**. Onde os territórios já
pertenciam à Base principal — a totalidade dos casos atuais — nada muda.

### O que a cobertura ainda não é

`eleitorado_coberto` conta **eleitores aptos**, não votos. Projeção de votos
válidos, pressão de cotas, ocorrências para meta e território disputado
pertencem a fases posteriores.

`universo_eleitoral.eleitorado_apto` (todos os bairros da Liderança) e
`cobertura_eleitoral.eleitorado_coberto` (só a interseção com o Setor) são
grandezas **diferentes** e continuam expostas separadamente.

## ADR-034 — Usuário global com ACL multiempresa/multiprojeto

**Contexto.** `usuarios.company_id` era, ao mesmo tempo, a empresa do cadastro e
o universo inteiro de autorização do usuário. Uma pessoa que atendia dois
clientes precisava ter a empresa trocada manualmente a cada projeto — ou ganhar
um segundo login (`joao+empresa-b@…`), duplicando a mesma identidade.

**Decisão.** O usuário passa a ser uma **identidade global** com ACL explícita:

```
usuario_empresa_acessos  (usuario_id, company_id, acesso_todos_projetos, ativo, principal)
usuario_projeto_acessos  (usuario_id, projeto_id, ativo)
```

Regra de autorização, centralizada em `services/acessos.py`:

```
Superadmin
OU (vínculo ATIVO com a Company do Projeto
    E (acesso_todos_projetos OU projeto explicitamente autorizado))
```

`usuarios.company_id` **permanece**, com semântica reduzida a **empresa
principal/default** (branding, contexto inicial da UI, compatibilidade de
contrato). Não é mais autoridade de autorização.

**Consequências.**

- O tenant continua pertencendo ao **recurso**: `Projeto.company_id` é a única
  verdade sobre de quem é o dado. `usuario_projeto_acessos` não duplica
  `company_id` — isso criaria uma segunda verdade sobre o mesmo fato.
- Dado operacional deriva o tenant do recurso, nunca do usuário:
  `coleta.company_id = Pesquisa → Projeto → Company`. Um agente multiempresa
  coletando no projeto da Empresa B grava como Empresa B, mesmo com empresa
  principal A.
- A ACL é lida do **banco a cada request**. Revogar acesso vale imediatamente,
  com o mesmo JWT — sem esperar expiração e sem novo login.
- Não há "tenant ativo" nem troca de contexto: um único token identifica a
  pessoa; o servidor decide recurso a recurso.
- Perfil (`perfil_id`) continua **global** nesta fase. RBAC por tenant fica para
  evolução futura.
- Negar acesso continua respondendo **404**, nunca 403: não se revela a
  existência de recurso de outro tenant.
- Backfill preserva o comportamento legado (`acesso_todos_projetos=true` na
  própria empresa) — e não cria linha por projeto, senão o passado ficaria
  congelado e projetos futuros da empresa ficariam de fora.

**Limitação registrada (patch subsequente).** `POST /projetos/` ainda infere a
empresa a partir do usuário. Com múltiplas empresas isso é ambíguo. A correção
prevista é um contexto explícito (`POST /empresas/{company_id}/projetos/` com
`assegurar_acesso_empresa`), **não** aceitar `company_id` em payload operacional.
Enquanto isso, criar projeto continua usando a empresa principal.

**Complemento — Base Eleitoral em contexto de Projeto.**

A Base Eleitoral privada continua pertencendo a um tenant
(`BaseEleitoral.company_id`; `NULL` = oficial). Depois da ADR-034, porém, a
empresa principal do usuário deixou de ser o tenant do recurso acessado, e o
módulo ainda comparava a Base com `current_user.company_id` em três pontos
(base principal do Projeto, candidatas e vínculo). Efeito: o Superadmin
(empresa Admin) abria o Projeto 14 (Empresa A) e via "Nenhuma Base
vinculada"; o modal do Projeto 15 vinha vazio. Regra fixada:

- **Autorização do usuário** (ACL/`filtro_projeto_acessivel`) decide se ele
  acessa o Projeto.
- **Tenant do Projeto** (`Projeto.company_id`) decide quais Bases servem a
  ele: oficiais + privadas com `BaseEleitoral.company_id == Projeto.company_id`.
- `current_user.company_id` **nunca** determina a Base do Projeto. O mesmo
  Projeto mostra a mesma Base principal para qualquer usuário autorizado.

Helpers: `filtro_elegibilidade_para_projeto(projeto)`,
`listar_bases_eleitorais_disponiveis_para_projeto(db, projeto)`,
`obter_base_eleitoral_elegivel_para_projeto(db, projeto, base_id)`;
`obter_base_principal_projeto_opcional` não filtra mais pelo usuário e valida a
integridade contra o tenant do Projeto (vínculo inconsistente → 404, nunca Base
de outro tenant). Endpoint novo `GET /projetos/{id}/bases-eleitorais-disponiveis`
alimenta o modal; `GET /base-eleitoral/` segue significando "o que o usuário vê"
(oficiais + privadas da própria empresa), que é outra pergunta.

Exemplos: Superadmin `company=Admin` → Projeto `company=A` → Base privada
`company=A` → **permitida**. Usuário principal `A` com ACL no Projeto `B` →
Base privada `B` → **permitida no Projeto B**; a Base privada `A` **não** é
candidata no Projeto B só por ser da empresa principal dele (404). Base privada
de outro tenant no vínculo → 404 inclusive para Superadmin: não é autorização
do usuário, é incompatibilidade de tenant entre Projeto e Base.

**Complemento — Cotas por Perfil e leitura da Base pelo Projeto.**

- `services/cota_perfil.py` gravava o plano com `company_id =
  current_user.company_id` e lia com o mesmo filtro. Corrigido: o plano
  pertence ao tenant do **Projeto**; a ACL (`_pesquisa_do_tenant`) já decide
  quem acessa. Plano legado gravado com a empresa do usuário é corrigido no
  próximo PUT. Testes `CP_B21` (Superadmin de outra empresa) e `CP_B22`
  (Gerente principal A com ACL no Projeto B: perguntas B, base B, municípios
  B, plano B; município da base A → 404).
- Visibilidade de LEITURA da Base (`filtro_visibilidade_para_usuario`): oficial,
  privada da empresa principal, ou principal de um Projeto acessível pela ACL.
  Sem isso, quem abre o Projeto por outra empresa não conseguia listar os
  municípios da Base dele (territórios → 404). A escrita não muda
  (`assegurar_permissao_escrita_base`). Teste `test_13`.
- Painel Web de Cotas por Perfil (Configuração Analítica): o Web só
  parametriza e lê; Backend continua o motor. Sem migration.

## ADR-035-B — Setor operacional vinculado a Município para integração de Cotas

**Contexto.** Duas regras independentes coexistem na Pesquisa: **cota
territorial** (unidade = Setor, "onde e quantas entrevistas?") e **cota por
perfil** (unidade = Município, "qual composição Sexo × Idade?"). Até aqui o
Município de um Setor era *derivado* na leitura, pela composição eleitoral
(`resolver_municipios_setores`: bairro → `municipio_id`), sem registro formal —
Setor sem composição não alimentava a cota de perfil, e a Base do DEV nem
tem geometrias municipais.

**Decisão.**

- `setores.municipio_territorio_id` (FK `territorio_eleitoral.id`, MUNICIPIO da
  Base principal do Projeto; nullable; índice) = **município operacional de
  referência**. Migration `b5c6d7e8f9a0` (aditiva) + backfill **só de setores**
  cuja composição resolve exatamente um município (ambíguo/sem composição →
  NULL, nunca "o primeiro"). Coletas/respostas não são tocadas; o progresso é
  calculado dinamicamente, então nada é reprocessado.
- **Fontes, em ordem** (`services/setor_municipio.py`): manual (validado contra
  a Base principal e a geometria — nunca aceito às cegas) › geometria (PostGIS:
  fração de área do polígono em cada MUNICIPIO com geometria, em `geography`,
  SRID 4326; ativa só quando a base tem geometrias municipais) › composição
  eleitoral. `resolver_municipios_setores` passa a preferir o valor persistido.
- **Setor operacional (OPERACAO/AMBOS) ambíguo → 422** ("Este setor
  intercepta mais de um município…"); nunca o maior pedaço em silêncio. Setor
  RELATORIO pode ser multi-municipal (NULL) e fica fora do contexto de perfil.
- Polígono/composição/finalidade alterados → referência **recalculada** no
  PATCH; a composição é a segunda fonte de validação (inconsistência é
  sinalizada, não corrigida).
- **A cota continua municipal.** `CotaPerfil.territorio_id` não muda;
  `diagnostico.meta_territorial` passa a ser `SUM(meta)` dos setores
  operacionais do município (com `setores_operacionais` e `diferenca`), e
  `GET …/cotas-perfil/contexto-territorial` alimenta a etapa "Municípios" do
  painel. Nenhuma constraint `SUM(cotas) == SUM(metas)`: só conferência.
- Uma coleta com `setor_id` incrementa o realizado do **Setor** e, pelo vínculo
  formal, a célula do **Município** (`classificar_coletas` já resolve por
  setor; provado por `test_setor_municipio` 51–53).
- Mobile: o contrato da missão já trazia `municipio` por setor e
  `prioridades_perfil` do município — **não alterado**.
- ADR-034 preservada: Base/municípios do **Projeto**; usuário principal A com
  ACL no Projeto B resolve pela Base B (teste 54).

**Regra registrada.** *Cota territorial é setorial. Cota de perfil é
municipal. Setor operacional deve resolver para um Município da Base
Eleitoral. A mesma coleta alimenta ambos os controles.*

## ADR-035 — Conta nasce por convite, com token de uso único

**Contexto.** A criação de usuário exigia que o administrador escolhesse uma
senha inicial e a transmitisse por fora do sistema (WhatsApp, telefone, e-mail
manual). Toda senha inicial conhecida por terceiros é uma credencial vazada por
construção.

**Decisão.** O cadastro passa a ser um **convite**: a conta nasce `ativo=false`
e o próprio usuário define a primeira senha por um link de uso único.

- Token: `secrets.token_urlsafe(32)` — aleatório criptográfico.
- O banco guarda **apenas o SHA-256** do token (`user_activation_tokens`). O
  token puro existe só no e-mail. SHA-256 é adequado **para o token** (segredo
  de alta entropia e vida curta); **senha continua em bcrypt/Passlib**.
- Uso único por `usado_em` (não DELETE: apagar perderia a evidência do consumo).
- Validade de 24h; gerar novo convite invalida o anterior ainda aberto.
- Ativação é atômica: senha + `ativo=true` + token consumido no mesmo commit.

**Consequências.**

- `usuarios.ativo`, que já existia, continua sendo o estado de ativação —
  nenhuma coluna redundante foi criada.
- `senha` na criação virou **opcional**: preenchida mantém o fluxo anterior
  (compatibilidade com scripts e testes); ausente dispara o convite.
- Conta convidada recebe `senha_hash` de um segredo aleatório de 48 bytes até a
  ativação: `senha_hash` é NOT NULL, e um marcador conhecido seria adivinhável.
- Não há login automático após ativar. A pessoa entra pelo `/login`, o que
  mantém um único caminho de autenticação — mais simples de auditar.
- Falha de SMTP **não** derruba a criação: o convite fica gravado e o reenvio
  resolve. Perder o cadastro por indisponibilidade de e-mail seria pior.
- Sem `SMTP_HOST` configurado o envio fica em modo registro (log), o que mantém
  DEV e a suíte de testes funcionando sem servidor de e-mail.

## ADR-036 — O link de convite é devolvido uma vez, nunca armazenado

**Contexto.** O convite chega por e-mail, e e-mail cai em spam. Sem uma saída, o
administrador ficava sem como entregar o acesso — e a tentação seria guardar o
link (ou o token) para reexibir depois.

**Decisão.** O `activation_url` é devolvido **na resposta de quem acabou de
criar ou renovar o convite** e em nenhum outro lugar. O painel oferece copiar e
compartilhar por WhatsApp (`wa.me` sem número — o administrador escolhe o
destinatário; nada de API paga nem cadastro de telefone).

**O que continua valendo:** o banco guarda apenas o SHA-256 do token. Não
existem colunas `activation_url`, `token_original` ou `token_plaintext`, e não
há endpoint para reler um link antigo — isso exigiria armazenar o segredo de
forma reversível, trocando um problema operacional por um risco permanente.

**Consequências.**

- Link perdido = **gerar novo convite**, que invalida o anterior. É o único
  caminho, e é o correto.
- O link vale como credencial temporária: fica só no estado do componente,
  some ao fechar o modal e nunca vai para storage, store global, log ou
  analytics.
- Nunca aparece em `GET /usuarios/` nem em nenhuma listagem — a criação tem
  schema próprio (`UsuarioCriadoResponse`) justamente para isso.
- `email_enviado: false` não é falha do convite: o token está gravado e o link
  segue por outro meio. O painel diz exatamente isso ao administrador.

## ADR-037 — Autorização por Perfil e Projeto

**Contexto.** Depois da ADR-034 (ACL multiempresa/multiprojeto), o sistema sabia
*de quem* era cada recurso e *quem podia vê-lo* — mas não *quem podia alterá-lo*.
Das 125 rotas registradas, ~95 exigiam apenas `get_current_user`: qualquer
usuário autenticado do tenant, inclusive um agente de campo, podia criar,
editar ou excluir projeto, pesquisa, pergunta, setor, geofence e liderança
chamando a API direto.

Além disso, o banco só tinha três perfis (Agente, Gerente, Superadmin).
Coordenador, Supervisor e Cliente existiam na regra de negócio, não no sistema.

**Decisão.** Quatro perguntas independentes decidem cada request:

```
QUEM É?            autenticação  → get_current_user
DE QUAL EMPRESA?   tenant        → Projeto.company_id
PODE VER ESTE?     escopo/ACL    → services.acessos      (ADR-034)
PODE FAZER ISTO?   capacidade    → core.rbac             (esta ADR)
```

- **Perfil define capacidade; ACL define escopo.** São ortogonais: um Cliente com
  `RELATORIO_VER` continua vendo só os projetos autorizados a ele; um Gerente com
  acesso ao projeto continua sem administrar empresas.
- A matriz vive em **código** (`core/rbac.py`), não em tabela: as capacidades são
  poucas, estáveis e precisam passar por code review. Tabela editável de
  permissões seria um IAM completo — outro projeto.
- O papel vem do **nome** do perfil, nunca do `perfil_id`: ids variam por
  instalação. Perfil desconhecido não recebe capacidade nenhuma — na dúvida,
  negar.
- A verificação entra como `dependencies=[Depends(require_permissao(...))]`, sem
  alterar a assinatura de ~100 endpoints.
- **Backend decide; Frontend representa.** O Web lê `permissions` de
  `/usuarios/me/` e só esconde o que não pode. Esconder botão é UX; chamar a API
  por fora continua batendo em 403.

**Status HTTP.**

| Situação | Código | Porquê |
|---|---|---|
| Recurso de outro tenant, ou projeto sem ACL | **404** | negar não pode revelar que o recurso existe |
| Recurso visível, operação proibida ao perfil | **403** | o usuário já sabe que o recurso existe; não há vazamento |

**Consequências.**

- Migration `e2f3a4b5c6d7` **semeia** Coordenador, Supervisor e Cliente. É seed,
  não estrutura: nenhuma tabela de permissões foi criada.
- `GET /usuarios/me/` ganhou `papel` e `permissions` (aditivos). Sem isso, a
  matriz seria duplicada no React — duas verdades sobre a mesma regra.
- Correção encontrada no caminho: `GET /projetos/{id}/pesquisas/` devolvia 200
  com lista vazia para projeto fora da ACL; agora responde 404, como o resto.
- `/upload` é usado pelo aplicativo durante a coleta, então aceita
  `COLETA_ENVIAR` além de `TERRITORIO_GERENCIAR`.
- Perfil continua **global** (não por tenant). RBAC por empresa fica para
  evolução futura.

## ADR-038 — Documentação OpenAPI restrita por ambiente

**Contexto.** `/docs`, `/redoc` e `/openapi.json` usavam os defaults do FastAPI e
ficavam públicos em qualquer ambiente. `/openapi.json` sozinho já entrega a
estrutura completa da API — todas as rotas, parâmetros e schemas — para quem
apenas souber a URL. O projeto também não tinha nenhuma noção de ambiente:
nem `APP_ENV`, nem `ENVIRONMENT`, nem `DEBUG`.

**Decisão.** Em produção as três rotas **não são registradas**:

```python
app = FastAPI(..., **ambiente.opcoes_documentacao())
# produção: docs_url=None, redoc_url=None, openapi_url=None
```

- A defesa é a **ausência da rota**, não uma senha ou um perfil na frente dela.
  Documentação protegida por RBAC continuaria sendo superfície exposta; assim,
  não há o que atacar.
- As três caem **juntas**: desligar só `/docs` e `/redoc` deixaria o JSON
  público, e com ele a API inteira continua enumerável.
- Introduzida **uma única** variável, `APP_ENV`, porque nenhuma existia.
  Aceita `production`/`producao`/`prod` (case-insensitive); qualquer outro valor,
  vazio ou ausente = desenvolvimento.
- **Nada de inferência** por hostname, IP, domínio, porta ou presença de Docker —
  há teste que analisa a AST do módulo e falha se algo além de `APP_ENV` for lido.

**Default: `development`.** Hoje a aplicação serve a documentação sem
configuração alguma; inverter isso em silêncio tiraria o Swagger de todo
desenvolvedor. O preço é explícito e precisa constar do runbook:

> **O deploy de produção tem de definir `APP_ENV=production`.** Sem isso, a
> documentação continua pública.

**Consequências.**

- Desligar OpenAPI **não** desliga endpoint nenhum: as 125 rotas continuam
  registradas, `/login/token` responde e rota protegida sem token segue em 401.
- Nenhuma alteração em CORS, JWT, RBAC, ACL, convite ou banco.
- **Reverse proxy:** não há configuração de Nginx versionada neste repositório,
  então nenhuma defesa adicional foi implementada — apenas recomendada. A
  proteção primária vive na aplicação, o que mantém a documentação desligada
  mesmo se a API for publicada diretamente por engano.

## ADR-039 — Auditoria central de segurança e acesso

**Contexto.** Depois de ACL (ADR-034), convite (035/036), RBAC (037) e Swagger
desligado (038), o sistema *decide* corretamente quem pode o quê — mas não
*lembra* de nada. Não havia como responder "quem tentou entrar", "quem foi
barrado", "houve tentativa cross-tenant", "quem acessou este projeto e de onde".

**Decisão.** Tabela append-only `audit_events` alimentada pelo **Backend, no
ponto em que cada decisão é tomada**:

| Evento | Onde nasce | Severidade |
|---|---|---|
| `LOGIN_SUCCESS` / `LOGIN_FAILED` | `login.py` (motivo interno: inexistente / senha inválida) | INFO / WARNING |
| `ACCOUNT_INACTIVE_LOGIN` | `login.py`: credencial **correta** + usuário ou empresa inativa (`is_user_access_active`). Cliente continua vendo o mesmo 401 genérico | WARNING |
| `TOKEN_EXPIRED` | `get_current_user`: `ExpiredSignatureError` do python-jose | WARNING |
| `TOKEN_INVALID` | `get_current_user`: `JWTError`/`ValidationError` (assinatura, estrutura, refresh usado como access) | WARNING |
| `TOKEN_REJECTED` | `get_current_user`: token **bom**, mas usuário inexistente ou inativo | WARNING |
| `RBAC_DENIED` | `core/rbac.py` — **403 funcional**: o perfil não tem a capacidade (ex.: Cliente com ACL faz PATCH) | WARNING |
| `ACCESS_DENIED` | `acessos.assegurar_acesso_projeto` e `GET /projetos/{id}` — **404 de ACL**: projeto do próprio tenant sem ACL (`sem_acl`) ou inexistente | WARNING |
| `CROSS_TENANT_ACCESS_ATTEMPT` | idem, quando o projeto é de **outra empresa** (404 externo idêntico) | **HIGH** |
| `PROJECT_ACCESS` | `GET /projetos/{id}` (a rota por onde o Web entra no projeto), **deduplicado por 15 min** | INFO |
| `ACCOUNT_ACTIVATED` / `ACL_CHANGED` | ativação de conta / `PUT /admin/usuarios/{id}/acessos` | INFO |

Três regras sustentam o desenho:

1. **Sessão própria.** O evento é gravado numa sessão independente da request.
   Um 403/404 aborta a transação da request; se o evento estivesse nela, a
   negação — o caso que mais importa — não deixaria rastro.
2. **Nunca derruba a request.** Falha ao auditar vira `logger.warning`, não 500.
   A auditoria é testemunha, não guarda.
3. **Nunca guarda segredo.** `details` é filtrado por chave (`senha`, `token`,
   `authorization`, `*_hash`…); sem corpo de requisição, sem dado de entrevistado.

**Contexto HTTP.** Um middleware gera `request_id` (devolvido em
`X-Request-ID`) e publica ip / user-agent / método / caminho num `ContextVar`,
para que qualquer dependency ou serviço audite sem receber `Request`.
`X-Forwarded-For` só é honrado com `AUDIT_TRUST_PROXY=true` — o uvicorn do
projeto roda sem `--proxy-headers`, e sem proxy declarado o header seria forjável.

**Consequências.**

- O cliente continua vendo 401/403/404 genéricos; o **motivo** fica só na trilha.
- A distinção *inexistente / sem ACL / outro tenant* é interna: o 404 externo
  não muda (ADR-037 preservada).
- FKs nullable e **sem cascade**: apagar usuário ou projeto não apaga a trilha.
  A aplicação nunca faz `UPDATE`/`DELETE` em `audit_events`.
- `GET /admin/auditoria/eventos` é a **fonte** para o painel de segurança e
  para a notificação ao Gerente — que são as próximas fases, não esta.

**Complemento (fechamento do PROMPT 04).**

- **`PROJECT_ACCESS` = entrada lógica, não requisição.** Chave de deduplicação
  `(PROJECT_ACCESS, user_id, project_id)`, janela de **15 minutos**
  (`JANELA_PROJECT_ACCESS`). O último acesso é lido da própria `audit_events`
  (sem Redis/cache), coberto pelo índice composto
  `(event_type, user_id, project_id, occurred_at)` da migration `a4b5c6d7e8f9`
  (`down_revision = f3a4b5c6d7e8`, que não foi alterada). O `company_id` do
  evento é o **tenant do projeto**, não `current_user.company_id`: para um
  Superadmin (sem empresa) em projeto de outro tenant, a pergunta útil é "qual
  tenant foi acessado"; o ator continua identificado por `user_id`.
- **`ACCOUNT_INACTIVE_LOGIN` existe** porque `is_user_access_active` já
  distingue "senha errada" de "credencial certa em conta/empresa inativa" sem
  mexer na autenticação. A distinção é só interna: status e mensagem externos
  não mudam e o cliente não descobre que a conta existe.
- **`TOKEN_EXPIRED` × `TOKEN_INVALID` × `TOKEN_REJECTED`.** O python-jose já
  levanta `ExpiredSignatureError` separado de `JWTError` no fluxo existente de
  `get_current_user`; a distinção é natural, sem segundo parse do token e sem
  alterar o JWT. `TOKEN_REJECTED` permanece para o caso restante: token válido
  cujo usuário não existe mais ou está inativo.
- **`RBAC_DENIED` × `ACCESS_DENIED` não se sobrepõem.** `RBAC_DENIED` é o 403
  de *capacidade* (ADR-037: o recurso pode ser visível, a operação não é do
  perfil). `ACCESS_DENIED` é o 404 de *ACL* (ADR-034: o recurso não existe para
  aquele usuário). Uma negação gera **um** evento — a ordem das dependencies
  (RBAC antes da ACL) garante isso. Ambos são necessários: respondem a perguntas
  diferentes ("perfil mal configurado?" × "tentativa de acesso indevido?").
- **Leitura por tenant.** Superadmin consulta globalmente (`company_id` é
  filtro real). Gerente consulta **somente o próprio tenant**: o `company_id`
  efetivo vem exclusivamente de `current_user.company_id`, e um
  `?company_id=<outro>` é **ignorado como seletor de escopo** (opção A) — mesmo
  padrão silencioso do Admin, que já filtra listagens pelo tenant do usuário
  sem 403. Coordenador, Supervisor, Cliente e Agente recebem 403 na dependency
  `require_manager_or_superadmin`, sem depender do frontend.
- **Filtros e paginação.** `event_type`, `severity`, `user_id`, `project_id`,
  `ip_address`, `data_inicio`/`data_fim` (sobre `occurred_at`, timezone-aware).
  `limit` default 50, **máximo 100** (`le=100` → 422 acima disso), `offset`
  preservado; resposta `{items, total, limit, offset}` reutilizando o formato de
  `TerritorioEleitoralPage`. Ordenação fixa `occurred_at DESC, id DESC`.
- **Segredos nunca persistem** — provado por teste que serializa a tabela e
  procura literalmente senha usada, JWT emitido, `Authorization: Bearer …`,
  token de ativação, `activation_url` e query string (`?token=…`): 0 ocorrências.
- O Web **não** emite eventos de segurança. Um sinal de UX de "entrada no
  projeto" pode existir no futuro, mas nunca será a única fonte.

## ADR-040 — Notificação de acesso a projeto ao Gerente responsável

**Contexto.** A ADR-039 tornou o acesso a um projeto um fato auditado
(`PROJECT_ACCESS`, deduplicado por 15 min). O Gerente que responde pelo projeto
ainda precisava abrir a trilha para saber que alguém entrou nele. Queremos que
ele seja avisado — sem transformar cada requisição HTTP em e-mail.

**Diagnóstico do destinatário.** Não existe `gerente_id`/`responsavel_id`.
Existe `projetos.coordenador_id` (NOT NULL, FK `usuarios.id`), e sua semântica
real é a de **responsável pelo projeto**: `crud.validate_project_coordinator`
só aceita usuário **ativo, do mesmo tenant, com perfil Gerente ou Superadmin**
— o perfil *Coordenador* da ADR-037 **não** é admitido nessa coluna, e o campo
é obrigatório na criação e validado na atualização. A ACL da ADR-034
(`usuario_empresa_acessos` / `usuario_projeto_acessos`) responde "quem pode
ver", não "quem responde pelo projeto"; vários Gerentes podem ver o mesmo
projeto, mas só um é o `coordenador_id`. Logo o destinatário é determinístico
e **nenhuma migration** foi necessária. O nome legado da coluna é mantido
(renomear quebraria Web e contratos); a semântica fica registrada aqui.

**Decisão.**

1. **O gatilho é o evento persistido, não o GET.** `registrar_acesso_projeto`
   já devolve `True` só quando houve INSERT real; o endpoint chama
   `notificacoes.notificar_acesso_projeto` apenas nesse caso. A deduplicação
   de 15 min do `PROJECT_ACCESS` é a **única** janela — não há segunda janela,
   fila, retry ou cache. 1 evento ⇒ no máximo 1 tentativa de e-mail.
2. **Destinatário** = `projeto.coordenador` (Gerente responsável). Nunca "todos
   os Gerentes do tenant", nunca Superadmins, nunca quem tem `PROJETO_VER`.
3. **Ator = destinatário ⇒ suprimido** (`self_access`): o Gerente abrindo o
   próprio projeto não recebe e-mail sobre si mesmo; o `PROJECT_ACCESS`
   continua auditado.
4. **Superadmin acessando ⇒ notifica.** É o acesso administrativo
   extraordinário que o Gerente mais quer conhecer. `company_id` do evento é o
   tenant do projeto (ADR-039).
5. **Sem destinatário utilizável ⇒ suprimido, nunca 500**: `no_project_manager`
   (defensivo — a FK é NOT NULL), `inactive_recipient`, `missing_recipient_email`,
   `smtp_not_configured` (sem `SMTP_HOST` o e-mail já fica em modo registro na
   ADR-035; não é falha).
6. **Falha de SMTP não bloqueia acesso**: envio síncrono e fail-soft via o
   único cliente `services/email.py` (que já não propaga), com `try/except`
   externo; resultado vira `PROJECT_ACCESS_NOTIFICATION_FAILED` (WARNING,
   nunca HIGH — indisponibilidade de e-mail não é incidente de segurança).
   Sem `BackgroundTasks`: não há worker/fila no projeto, o convite da ADR-035 já
   envia de forma síncrona, e o custo de uma sessão a mais não compensa.
7. **Resultado auditado em `audit_events`** (Preferência A): `…_SENT` /
   `…_SUPPRESSED` (INFO) / `…_FAILED` (WARNING), `details` só com
   `recipient_user_id` e `reason`. Não há tabela de notificações nem conteúdo
   do e-mail persistido.
8. **Conteúdo**: projeto, ator, perfil (`rbac.papel_nome`), empresa do
   projeto, data/hora em **UTC explicitado** (o projeto não tem timezone
   operacional configurada; converter para zona arbitrária no Backend mentiria
   para outras implantações), IP e User-Agent **já normalizados pela ADR-039**
   (respeita `AUDIT_TRUST_PROXY`; UA truncado a 160 chars, sem biblioteca de
   parsing), e link `WEB_BASE_URL/projetos/{id}` (rota real do Web, nunca a
   API). **Nunca** JWT, Authorization, senha, token, corpo de requisição ou
   dado de pesquisa/entrevistado.
9. **Sem flag global nova**: não há necessidade operacional de desligar; o
   próprio `SMTP_HOST` vazio já suprime. Preferências por projeto/Gerente
   (resumo diário, só Cliente, horário) ficam para uma fase futura.
10. **Web e Mobile intocados.** O Web não emite sinal de acesso; o
    `GET /projetos/{id}` já é o evento lógico.

**Consequências.**

- Nenhum contrato HTTP mudou; nenhuma migration; head Alembic segue
  `a4b5c6d7e8f9`.
- Sem retry: um retry ingênuo sem idempotência persistida duplicaria e-mails.
  A falha fica visível na trilha para evolução futura.
- Evolução natural: preferências de notificação e o painel de segurança
  consomem os mesmos eventos.

## ADR-041 — Painel administrativo de segurança

**Contexto.** As ADRs 034–040 tornaram o sistema capaz de decidir, lembrar
(`audit_events`) e avisar. Faltava **ver**: o administrador precisava consultar
a trilha à mão para responder "o que aconteceu, quando, quem, de onde, em qual
empresa/projeto, foi permitido ou bloqueado, com qual severidade".

**Decisão.** Um painel que **lê e interpreta visualmente fatos já registrados
pelo Backend**, sem criar eventos, sem decidir o que é incidente e sem executar
ação contra usuário ou IP.

1. **`audit_events` permanece a fonte da verdade.** O painel não gera, altera
   nem apaga eventos; consultar o resumo ou a lista não deixa rastro na trilha
   (`test_PS20/PS22`).
2. **Agregação no Backend.** `GET /admin/auditoria/resumo` calcula totais,
   ranking de IPs, contas mais tentadas e série temporal com
   `COUNT`/`SUM(CASE)`/`GROUP BY`/`MAX`, portável entre PostgreSQL e o SQLite
   dos testes (`date_trunc`/`to_char` × `strftime`). Nunca carrega
   `AuditEvent` em Python, e o Web nunca conta eventos da página para compor
   indicador. Os índices simples existentes (`occurred_at`, `event_type`,
   `severity`, `company_id`, `project_id`, `user_id`) cobrem os filtros;
   **nenhuma migration**.
3. **Mesma matriz de acesso de `/eventos`.** Superadmin: global, `company_id`
   como filtro real. Gerente: `company_id` efetivo vem só de
   `current_user.company_id`; um `company_id` externo é ignorado. Coordenador,
   Supervisor, Cliente e Agente: 403 na dependency — o Web só esconde o menu
   e redireciona (`PermissionRoute` com `USUARIO_GERENCIAR`, a capacidade que
   espelha `require_manager_or_superadmin`).
4. **Eventos sem `company_id` não entram na visão gerencial.** Um
   `LOGIN_FAILED` contra e-mail desconhecido não tem tenant; o Gerente não o vê
   (não se adivinha tenant pelo domínio do e-mail). O Superadmin vê tudo.
5. **Cards com semântica fixa:** login falho = `LOGIN_FAILED` +
   `ACCOUNT_INACTIVE_LOGIN`; negados = `RBAC_DENIED` + `ACCESS_DENIED`;
   cross-tenant separado (HIGH, destaque proporcional, sem animação); acessos
   a projeto = `PROJECT_ACCESS` **já deduplicado** (acessos lógicos, não
   requests); falhas de notificação = só `…_FAILED` — `SUPPRESSED` é
   comportamento esperado (autoacesso, SMTP não configurado…) e mantém a
   semântica da ADR-040.
6. **Ranking de IP não implica classificação.** Considera só eventos de
   segurança (login falho, token, negados, cross-tenant); `LOGIN_SUCCESS`,
   `PROJECT_ACCESS` e `NOTIFICATION_*` ficam fora. A UI diz "IP com mais
   eventos" e avisa que NAT/VPN/proxy concentram usuários legítimos. Nunca
   "malicioso", "hacker" ou "atacante".
7. **Ranking de contas** agrupa `attempted_email` dos logins falhos, sob a
   mesma regra de tenant; nunca senha.
8. **Nenhuma ação automática ou manual.** Sem rate limiting, bloqueio,
   banimento, desativação, MFA, CAPTCHA, alertas, exportação, retenção ou SIEM
   nesta fase: primeiro a segurança fica visível e auditável.
9. **Separado do monitoramento de campo.** É módulo administrativo; não usa
   coletas, agentes, GPS, cotas nem setores.
10. **UI acessível e defensiva.** Nomes amigáveis mapeados no Web (o
    `event_type` do Backend não muda); severidade sempre com texto e marcador;
    `details` renderizado chave/valor por `sanitizeDetails`, que descarta
    chaves sensíveis mesmo aninhadas — defesa de apresentação além da
    sanitização do Backend. Paginação `limit/offset` com máximo 100; ordenação
    `occurred_at DESC`. Datas na timezone do navegador, ISO UTC no detalhe.

**Consequências.**

- Web ganha rota, menu, service, types, lib pura e componentes; Backend ganha
  um endpoint de leitura; Mobile e contratos existentes intocados.
- O menu administrativo passa a ser por capacidade: o Gerente entra nele só
  pela Segurança (e vê "Usuarios" apontando ao painel do tenant).
- Evolução natural: preferências, exportação e ações (bloqueio, alertas) —
  sempre como decisão do Backend, nunca do painel.
## ADR-042 — Monólito modular e entitlement comercial

**Decisão.** O Pesquisa360 evolui no backend existente como monólito modular.
Multitenancy, entitlement comercial e ACL/RBAC permanecem conceitos e camadas
distintas. O entitlement só é avaliado depois de o tenant do recurso ter sido
validado e nunca amplia acesso a dados.

## ADR-043 — Entitlements aditivos e features explícitas

**Decisão.** Licenças de Empresa, Projeto e Pesquisa são aditivas. Uma licença
suspensa mais específica não nega uma licença ampla ativa; não existe DENY
implícito. Toda feature contratada possui vínculo explícito, sem wildcard, para
que capacidades futuras não sejam concedidas a contratos antigos por acidente.

## ADR-044 — Catálogo planejado não equivale a capacidade utilizável

**Decisão.** `potencial_crescimento` é pré-cadastrada com `ativo=false`, estado
planejado/indisponível, porque o motor não existe nesta Sprint. O resolvedor só
expõe módulos e funcionalidades ativas e efetivamente licenciadas.

## ADR-045 — Fundação sem gating

**Decisão.** O Prompt 01 cria dados, serviço e leitura, mas não liga enforcement
em nenhuma rota existente. FeatureGate Web, ACL por feature, administração de
licenças e o motor de Potencial de Crescimento ficam planejados.

## ADR-046 — Backend é autoridade final de enforcement comercial

**Decisão.** O cliente pode ocultar interface, mas somente o Backend decide se
uma capacidade contratada pode executar. Frontend nunca substitui o gate.

## ADR-047 — Multitenancy e recurso precedem entitlement

**Decisão.** Projeto/Pesquisa são autenticados e autorizados antes da consulta
comercial. Recurso não autorizado retorna 404, sem revelar licenciamento.

## ADR-048 — Ausência de entitlement retorna 403

**Decisão.** Para recurso já autorizado e capacidade ativa, módulo ou feature
sem concessão efetiva retorna 403. Suspensão e janela temporal inválida têm a
mesma semântica.

## ADR-049 — Catálogo inativo não pode ser concedido

**Decisão.** Módulo ou feature inativo é capacidade indisponível (404 genérico),
mesmo que exista vínculo manual. `potencial_crescimento` continua inativa.

## ADR-050 — Gates comerciais são reutilizáveis

**Decisão.** `require_module` e `require_feature` são factories independentes
de produto. A lógica SQL permanece em `services/modulos.py`; endpoints apenas
compõem o contexto de recurso e a dependency.

## ADR-051 — Tenant comercial vem do contexto autorizado

**Decisão.** Sem recurso, usa-se a empresa principal autenticada. Com Projeto ou
Pesquisa, usa-se a empresa do recurso previamente autorizado pela ACL, o que
preserva usuários multiempresa. Query, body e headers não selecionam tenant.

## ADR-053 — Somente administração global gerencia entitlements

**Decisão.** Apenas `require_superadmin` autoriza leitura administrativa e
mutação de licenças. Tenant não pode se autolicenciar.

## ADR-054 — Empresa explícita somente em contexto administrativo

**Decisão.** `company_id` no path é permitido apenas nas rotas administrativas
Superadmin, com empresa/recursos validados e auditoria.

## ADR-055 — Catálogo técnico é read-only no painel

**Decisão.** Módulos/features representam capacidades do software; o painel não
cria, renomeia, ativa ou exclui catálogo.

## ADR-056 — Entitlement não é apagado fisicamente

**Decisão.** O ciclo usa ATIVO/SUSPENSO/CANCELADO; não existe DELETE.

## ADR-057 — Módulo e escopo são imutáveis

**Decisão.** PATCH altera somente status/validade. Erro de módulo/escopo exige
cancelar a licença histórica e criar outra.

## ADR-058 — Feature inativa não pode ser concedida

**Decisão.** UI desabilita, mas Backend rejeita request manipulado com 422.

## ADR-059 — Administração de licenças é auditável

**Decisão.** Cada mutação grava ator, empresa, entitlement e estados before/after
em `audit_events`, na mesma transação da alteração.

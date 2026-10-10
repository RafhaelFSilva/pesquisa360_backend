# Status atual

## Apuração TSE — ingestor automático (2026-10-04, ADR-087 e ADR-088)

| Item | Estado |
|---|---|
| Worker `python -m pesquisa360.services.tse.worker`, processo separado da API | IMPLEMENTADO; VALIDADO em container (mesma imagem da API) contra PostgreSQL de QA |
| Singleton por origem (PostgreSQL advisory lock), compartilhado com a CLI | IMPLEMENTADO; VALIDADO com dois workers e CLI concorrentes |
| Descoberta do pleito pelo EA11 (sem código fixo) | IMPLEMENTADO; VALIDADO (3220 no oficial, 17801 no simulado) |
| Parada por SIGTERM/SIGINT com rollback, healthcheck por heartbeat | IMPLEMENTADO; VALIDADO (`docker stop` → código 0; `healthy`) |
| Serviço `tse_ingestor` no compose de staging e de DEV (profile `tse`); override para PROD em `deploy/tse_ingestor/` | IMPLEMENTADO; o compose de PROD não é versionado — o override precisa ser conferido no deploy |
| `httpx` declarado em `pyproject.toml` | IMPLEMENTADO; imagem construída do zero instala `httpx 0.28.1` |
| Worker com apuração oficial **com votos** | VALIDADO em QA (04/10/2026, 17:22): AP com 32/1914 seções, votos reais em UF, Macapá e zonas, histórico real de 3 totalizações. Em PRODUÇÃO: PENDENTE |

O worker nasce **desligado** (`TSE_INGESTION_ENABLED=false`): sobe ocioso e
não consulta o TSE até ser habilitado. Operação em `11-apuracao-tse.md`,
seção 25. Nenhuma migration nova: o head continua `0f1b3b6c572f`.

## Relatórios — resposta espontânea sem texto vira NS/SR (2026-10-04)

`resolve_reportable_response_value` devolve `NS/SR` quando a resposta
espontânea não tem letra nem dígito (vazia, só espaços, só pontuação ou só
emoji). Antes essas respostas inflavam "Não categorizada", que é a fila de
trabalho da apuração espontânea. Texto válido segue a categorização de sempre;
perguntas não espontâneas não mudam. O total de cada pergunta é preservado.
Afeta relatório simples, crosstab, cruzamentos, mapas, filtros de universo,
Gestão de Lideranças e Potencial de Crescimento, que usam a mesma função.

Origem: a correção existia em PROD como patch não versionado em
`pesquisa360/crud.py`. Foi **reimplementada** a partir da regra descrita na
auditoria de PROD, sem cópia do arquivo — a equivalência linha a linha com o
patch de PROD deve ser conferida no preflight.

## Apuração TSE — API analítica e Web (2026-10-04, ADR-085 e ADR-086)

Sobre a fundação de dados abaixo foram entregues a API e as telas. Migration
`0f1b3b6c572f` (painéis), filha de `cd95ebf79450`, **novo head único**.

| Item | Estado |
|---|---|
| API de leitura `/apuracao/tse/...` (eleições, resumo, cargo, nominatas, candidato, território, evolução) | IMPLEMENTADO; VALIDADO por HTTP real no banco de QA |
| Painéis personalizados `/apuracao/paineis` (multitenant) | IMPLEMENTADO; VALIDADO (Empresa B recebe 404 no painel da Empresa A) |
| Web: Central, Majoritário, Proporcional/Nominata, Meus painéis, Painel, Candidato (territorial até Zona) | IMPLEMENTADO; VALIDADO por E2E real em Chrome headless (10 passos) com dados do **simulado** |
| Atualização automática (polling de 15 s, só Backend) | IMPLEMENTADO |
| Estado "oficial zerado" | VALIDADO com a apuração oficial ainda não iniciada |
| Telas com apuração oficial **parcial** e com votos reais | PENDENTE — o backend já foi validado com votos oficiais em QA; as telas ainda não foram vistas com eles |
| Evolução com mais de uma totalização real | PENDENTE — só há uma totalização por abrangência na base |
| Filtros territoriais Município → Zona na aba Proporcional (API + Web) | IMPLEMENTADO; VALIDADO por E2E real com a apuração oficial do AP |
| Filtros territoriais Município → Zona na Central, no Majoritário e em Meus painéis (recorte único na URL) | IMPLEMENTADO; VALIDADO por E2E real com a apuração oficial do AP (fase 2, **não commitado**) |
| Painel de Distribuição Territorial (candidatos e nominatas de um cargo por município e por zona) | IMPLEMENTADO; VALIDADO por E2E real; migration `bcab956ae48e` aplicada só no QA descartável (fase 2, **não commitado**) |
| Detalhamento por seção (Boletim de Urna) | IMPLEMENTADO na fase 3; VALIDADO em QA com os 1.914 BUs oficiais do AP e E2E real (**não commitado**) — ver seção "Apuração TSE — fase 3" abaixo |
| Entitlement comercial do módulo | PENDENTE — o acesso hoje é só por permissão (`INTELIGENCIA_VER`) |
| Execução agendada da ingestão | entregue na rodada seguinte (worker `tse_ingestor`, seção acima) |

Presidente aparece com o resultado **na UF** (EA20 de abrangência UF); o total
nacional não é ingerido.

## Apuração TSE — fase 5: nome e endereço dos locais (2026-10-06, ADR-092)

IMPLEMENTADO e VALIDADO no DEV; **não commitado, não deployado**. Migration
`a7c3e91b5d24` (tabela `tse_locais_votacao`) aplicada no DEV. Detalhes em
`11-apuracao-tse.md` §31.

| Item | Estado |
|---|---|
| Fonte: CSV "Eleitorado por local de votação – 2026" (Dados Abertos do TSE) | REGISTRADA (URL, SHA-256, schema); o site do TRE-AP bloqueia acesso automatizado |
| Conciliação BU × CSV pela chave oficial (`NR_LOCAL_VOTACAO_ORIGINAL`) | 376/376 MATCH, 0 BU_ONLY, 0 ambíguos; 8 só no CSV (locais de seções agregadas) |
| Importador idempotente (`scripts/tse_locais.py`) | IMPLEMENTADO; DEV: 384 locais do AP gravados, repetição sem alteração |
| API territorial com nome, endereço e bairro | IMPLEMENTADO; 376/376 locais do BU com nome e endereço; 19 sem bairro |
| Web: busca do local por nome, código ou endereço; nome no filtro, cartões, breadcrumb e distribuição | IMPLEMENTADO; VALIDADO por E2E real (12 passos) |
| Votos | INALTERADOS — conferido antes × depois (hash das linhas de voto e 1.880 totais por local) |
| Coordenadas do local | NÃO IMPORTADAS (existem na fonte para o local utilizado; fora do escopo) |

O vínculo seção → local continua sendo o código do Boletim de Urna; o CSV só
fornece metadado.

## Apuração TSE — fase 4: Local de votação (2026-10-06, ADR-091)

IMPLEMENTADO e VALIDADO no DEV; **não commitado, não deployado**. Sem migration:
o head continua `d2f4a9c17e36`. Detalhes em `11-apuracao-tse.md` §30.

| Item | Estado |
|---|---|
| Vínculo seção → local pelo código oficial do BU (`identificacaoSecao.local`) | IMPLEMENTADO; AP: 376 locais, 1.914 principais, 57 agregadas, 0 seção sem local |
| API: `local_votacao` em território, resumo, cargo, nominatas, candidato e distribuição | IMPLEMENTADO; VALIDADO por HTTP real (376 locais × 5 cargos = soma SQL independente) |
| Web: filtro Local entre Zona e Seção, reset em cascata, URL | IMPLEMENTADO; VALIDADO por E2E real (11 passos) |
| Distribuição UF → Município → Zona → Local → Seção | IMPLEMENTADO; VALIDADO |
| Nome, endereço e coordenadas do local | NÃO DISPONÍVEIS nesta fonte; a tela mostra "Local 1234" |

O total por local é **agregado pelo Pesquisa360 a partir dos Boletins de Urna
oficiais** — não é um resultado EA20 do TSE.

## Apuração TSE — fase 3: Boletim de Urna e resultado por seção (2026-10-04, ADR-094)

IMPLEMENTADO e VALIDADO em QA; **não commitado, não deployado**. Detalhes em
`11-apuracao-tse.md` §29 e `tse-bu-2026-inspecao.md`.

| Item | Estado |
|---|---|
| Especificação ASN.1 2026 (`bu.asn1`, SHA-256 `ef64bf72…04ab81`) | IDENTIFICADA e registrada; cópia verificada no pacote |
| Decoder isolado (`bu_decoder.py`, `asn1tools`, BER) | IMPLEMENTADO; VALIDADO com 1.914 BUs oficiais (0 falhas) |
| Persistência (`tse_boletins_urna`, `tse_bu_cargos`, `tse_bu_votos`, `tse_bu_controle`) | IMPLEMENTADO; migration `d2f4a9c17e36` aplicada só no QA descartável |
| Ingestão incremental EA18 → BU, isolada por boletim | IMPLEMENTADO; VALIDADO (1.914/1.914 urnas do AP, 0 erros) |
| Worker: lote por ciclo, desligado por padrão (`TSE_INGESTION_BU_ENABLED`) | IMPLEMENTADO; VALIDADO em QA (120 urnas por ciclo, ~2 min) |
| Conferência BU × EA20 por zona (somente leitura) | IMPLEMENTADO; AP: 67 MATCH, 23 PARTIAL, 0 DIVERGENT |
| API: `secao` em resumo, cargo, nominatas, candidato e distribuição | IMPLEMENTADO; VALIDADO por HTTP real |
| Web: seletor de Seção habilitado, cartões do boletim, Zona → Seções | IMPLEMENTADO; VALIDADO por E2E real (10 passos) |
| Seção agregada (resultado do grupo, sem repartir votos) | IMPLEMENTADO; VALIDADO com a seção 0096 (agregada à 0095) de Macapá |
| Verificação da assinatura digital do BU | PENDENTE — exige certificados da urna e dependências extras |
| Ingestão de BU em PRODUÇÃO | NÃO HABILITADA — flag desligada por padrão; exige a migration |

O EA20 continua sendo a fonte de UF, município e zona. O BU é a fonte só da
seção: nenhuma soma de BUs substitui um total do EA20.

## Apuração TSE — fundação de dados (2026-10-04, ADR-076 a ADR-084)

Novo domínio independente em `pesquisa360/services/tse/`, com tabelas `tse_*`
globais (sem `company_id`). Migration `cd95ebf79450`, filha de `be8036a45b28`.
Detalhes em `11-apuracao-tse.md`.

| Item | Estado |
|---|---|
| Ingestão EA11, EA12, EA14, EA15, EA16, EA20 (UF, município, zona) | IMPLEMENTADO; VALIDADO no ambiente oficial de **simulação** do TSE |
| Snapshots, histórico append-only, idempotência | IMPLEMENTADO; idempotência VALIDADA em ingestão real (simulado); histórico com mudança real só em teste automatizado |
| Reconciliação zonas × município e municípios × UF | IMPLEMENTADO; VALIDADO (simulado) |
| Separação `OFICIAL` × `SIMULADO` | IMPLEMENTADO |
| EA18 | PARCIAL — contrato com arquivos visto só em 2024; simulado 2026 vem sem arquivos |
| BU / votos por seção | IMPLEMENTADO na fase 3 com o `bu.asn1` oficial de 2026 (decoder, tabelas `tse_bu_*`, ingestão incremental); **não commitado** |
| Apuração **oficial** 2026 com votos reais | VALIDADO em QA na rodada do worker (seção acima); em produção, pendente |
| API e dashboards | entregues na rodada seguinte (seção acima) |
| Polling contínuo do TSE (ingestão agendada) | FUTURO |

Ingestão: somente manual, via `scripts/tse_apuracao.py`. Mobile não foi alterado.

**Ambientes de banco.**

- **QA descartável (`p360_tse_qa`, PostgreSQL 15.4 / PostGIS 3.3.4): REFERÊNCIA
  PARA NOVAS MIGRATIONS TSE.** Sobe do zero até `a7c3e91b5d24`
  (`bcab956ae48e` = `apuracao_paineis.tipo`, fase 2; `d2f4a9c17e36` = tabelas do
  Boletim de Urna, fase 3; ambas ainda não commitadas).
- **DEV local (`pesquisa360_db`): NÃO CANÔNICO, PENDENTE DE RECONCILIAÇÃO.** `alembic_version`
  está em `e8f9a0b1c2d3`, revisão que não existe mais no repositório, e a
  migration sintética `c6d7e8f9a0b1` nunca foi aplicada nele (faltam em
  `coletas`: `is_synthetic`, `seed_run_id`, `synthetic_source`,
  `synthetic_operator_id`, a FK, o CHECK e o índice parcial). `alembic stamp`
  e `alembic upgrade` estão proibidos nesse banco até decisão entre
  reconstrução, reparo controlado ou substituição. As migrations TSE e de
  painéis **não** foram aplicadas nele.
- **PROD:** não recebeu nenhuma alteração e o deploy não foi feito.
  O patch local de `pesquisa360/crud.py` (NS/SR) foi reimplementado no Git;
  conferir a equivalência no preflight antes de qualquer pull/rebuild. O
  `AGENTS.md` modificado em PROD é instrução operacional local e não entra
  no repositório. `main` remoto diverge da feature.
  Procedimento de ingestão e lista de bloqueadores: `11-apuracao-tse.md`,
  seções 25 e 26.

## Hardening P0 — Integridade histórica Setor × Cenário (ADR-075)

Corrigido em 2026-09-16: a FK `lideranca_cenario_setores.setor_id` deixou de
ser `ON DELETE CASCADE` (migration corretiva `be8036a45b28`, filha de
`0d497634c513`, head único) e passou a `ON DELETE RESTRICT`; a exclusão
física de Setor passa por `assegurar_setor_excluivel` (coletas OU histórico
em cenário — RASCUNHO, ATIVO ou ARQUIVADO — respondem 409 com mensagem de
negócio; setor nunca usado segue excluível). Upgrade, downgrade e re-upgrade
validados em SQLite descartável e no Postgres local do compose. Web não
exigiu alteração (`mensagemErroExcluirSetor` já exibe o `detail` do 409).
Soft delete de Setor permanece evolução futura.

## Gestão de Lideranças — Cenários de Base Eleitoral Operacional (ADR-075)

Implementado em 2026-09-16: `lideranca_cenarios` + `lideranca_cenario_setores`
(migration `0d497634c513`, filha de `98d2bd125070`, head único), serviço
`lideranca_cenario.py` com ciclo RASCUNHO → ATIVO → ARQUIVADO, duplicação,
ativação transacional com índice parcial único de um ATIVO por onda, snapshot
da referência oficial e o resolvedor único `resolver_base_calculo`, consumido
pela análise de lideranças (`base_calculo.modo` = PADRAO sem cenário;
CENARIO_OPERACIONAL com cenário ativo; `CENARIO_SETOR_NAO_CONFIGURADO` sem
fallback). Rotas em `api/endpoints/lideranca_cenarios.py` sob
`/projetos/{id}/liderancas/cenarios`. Web: aba "Base operacional" na Gestão
de Lideranças com editor em lote, indicador "Base de cálculo" em todas as
views e origem do eleitorado no detalhe. Base Eleitoral oficial, Setor global
e Mobile intocados; nenhum cenário criado para dados existentes.

Validação (Python 3.13.14): suíte backend completa 1755 passed, 14 skipped,
0 falhas (após alinhar as constantes de head em `test_migration_chain`,
`test_fluxo_campo_integrado` e `test_acl_multiempresa`);
`test_lideranca_cenarios.py` 66 testes. Upgrade aplicado no Postgres local do
compose (`alembic current` = `0d497634c513`). Web: `tsc -b` limpo,
`npm run build` OK, 1261/1261 testes (1235 baseline + 26 novos), lint com os
mesmos 10 erros preexistentes.

## Sprint 0 — Fundação da Modularização (Prompt 01)

Implementado em 2026-09-01: catálogo `modulos`, catálogo
`modulo_funcionalidades`, licenças `modulo_entitlements`, concessões explícitas
`modulo_entitlement_funcionalidades`, resolução temporal/aditiva por Empresa,
Projeto e Pesquisa e `GET /usuarios/me/modulos/`.

Migration: `98d2bd125070`, filha de `c6d7e8f9a0b1`, novo head validado.
Validação executável em Python Windows 3.13: upgrade, downgrade e re-upgrade em
SQLite descartável passaram; `test_modulos_entitlements.py` passou (18) e a
suíte backend passou (1481 passed, 12 skipped, 81 warnings). O banco do `.env`
não foi acessado: Docker não estava disponível e o host `db` não é local.
Nenhum tenant recebeu licença no seed. O gating das rotas existentes **não foi
ativado**. Web e Mobile não foram alterados.

## Sprint 0 — Enforcement Backend (Prompt 02)

Implementado em 2026-09-01: gates reutilizáveis `require_module` e
`require_feature`, com contextos de Empresa, Projeto e Pesquisa. Projeto e
Pesquisa passam primeiro pela autorização de recurso/ACL; somente depois o
Backend verifica catálogo ativo, entitlement efetivo e concessão explícita de
feature. Ausência de capacidade contratada retorna 403; recurso não autorizado
ou capacidade inexistente/inativa retorna 404.

Nenhuma rota produtiva foi ligada aos gates nesta fase. Projetos, Pesquisas,
Relatórios, Monitoramento, Lideranças, Inteligência Territorial, Controle de
Campo e Mobile continuam sem gating comercial. `potencial_crescimento`
permanece planejada e inativa.

## Sprint 0 — Contexto / Gates Web (Prompt 03)

Implementado em 2026-09-02: infraestrutura Web de capacidades comerciais em
`zustand` com `GET /usuarios/me/modulos/`, `ModuleGate`, `FeatureGate`,
`ModuleRoute`, `FeatureRoute`, registry central de módulos/features e reset
explícito em logout/troca de usuário. A UX usa fail-closed para loading/error
sem decidir segurança; o Backend continua sendo a autoridade final de
entitlement e ACL. Nenhum módulo legado foi ativado; o fluxo atual mantém o
cliente apenas ocultando opções e bloqueando acesso de rota quando o backend
não concede a capacidade.

## Sprint 0 — Administração de Licenças (Prompt 04)

Implementada a administração global de catálogo e entitlements por Superadmin,
com escopos Empresa/Projeto/Pesquisa, validade, status, features explícitas,
efeito imediato nas capabilities e auditoria persistente before/after. O Web
ganhou a página `/admin/modulos`, protegida por `SuperadminRoute`, com seleção
de empresa, criação, suspensão, reativação, cancelamento e gestão de features.
Catálogo permanece read-only; `potencial_crescimento` permanece inativa.

Validação: Backend 1517 passed, 12 skipped, 0 failures e 84 warnings; Web
1119/1119 testes e build de produção aprovados. Lint mantém 10 erros
preexistentes, nenhum em arquivo novo do Prompt 04. Nenhuma migration criada;
head `98d2bd125070`.

## Sprint 0 — baseline final validada (Prompts 05B/05C/05D)

Validação encerrada em 2026-09-02 com Python 3.13.14. A regressão Backend
completa passou com 1529 testes, 13 skips e zero falhas; o Web passou 1119/1119
testes e build. O lint mantém 10 erros preexistentes, sem erro novo da Sprint 0.

Em PostgreSQL real descartável, a lineage completa passou por `upgrade head`,
`downgrade base` e novo `upgrade head`. Foram corrigidos somente os downgrades
históricos `28f012bafc15` e `91fbe6db1f17`; seus `upgrade()`, `revision` e
`down_revision` não mudaram. A lineage permanece única em `98d2bd125070`.

O hardening confirmou IDOR administrativo, rejeição de mass assignment,
atomicidade entre mutação e auditoria, 404 antes de 403, ausência de
autoelevação e fail-closed Web na troca de empresa e na falha da API de módulos.

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 01

Concluído em 2026-09-02 como rodada exclusivamente de modelagem: auditoria da
estrutura real de dados (entidades, tipos de pergunta, respostas, pesos,
denominadores dos relatórios, espontânea, território) e proposta de contrato
de domínio em
`doc/14-inteligencia-eleitoral-potencial-crescimento-modelagem.md`.

Resultado: **GO para revisão metodológica humana** (questões Q01–Q15 e matriz
de decisão no documento). Nenhum código funcional foi alterado; nenhuma
migration criada; head permanece `98d2bd125070`. A feature
`inteligencia_eleitoral.potencial_crescimento` permanece planejada e inativa;
nenhum tenant recebeu entitlement.

**Atualização (Prompt 02):** a modelagem foi APROVADA como baseline
metodológica do MVP 1 — decisões D01–D15 registradas no doc/14 (incluindo a
correção da D11: no modo não ponderado, `weighted_base` é indisponível/null,
nunca `n_bruto` disfarçado).

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 02

Implementado em 2026-09-02: contrato de configuração canônico
`GrowthAnalysisConfiguration` no novo package
`pesquisa360/inteligencia_eleitoral/` (`config.py` + `validation.py`), com
enums do domínio, `TargetCandidacy` (bindings explícitos por pergunta),
`ElectoralScenario` (SINGLE/MULTIPLE/ORDERED_MULTIPLE), taxonomia explícita
de intenção, `EligibilityPolicy` (D01 invariável), sinais opcionais
(rejeição/segunda opção/decisão do voto), 1–2 dimensões de perfil, território
NONE/SETOR/MUNICIPIO, filtros compatíveis com `filtros_universo`, políticas
de ponderação (só NAO_PONDERADO), base mínima sem default, referência
ELIGIBLE_UNIVERSE, incerteza WILSON_AAS_APPROX e qualidade de espontânea.
Validação em duas camadas (estrutural Pydantic + domínio contra dados reais,
com erros/warnings tipados e normalização canônica de valores).
Documentação: `doc/15-inteligencia-eleitoral-potencial-crescimento-configuracao.md`.

Sem migration (head `98d2bd125070`), sem tabela, sem endpoint, sem
persistência da configuração, sem motor estatístico, sem alteração em
`models.py`, Web e Mobile intocados. A feature `potencial_crescimento` segue
inativa e sem entitlement. Testes: `tests/test_growth_analysis_configuration.py`
(63 casos, C01–C65) passando; regressão focada e completa sem falhas novas.

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 03

Implementado em 2026-09-02: motor estatístico
(`pesquisa360/inteligencia_eleitoral/`: `results.py`, `statistics.py`,
`engine.py`) com interface pública
`analyze_growth_potential(db, current_user, configuration) -> GrowthAnalysis`.
Revalida a configuração (erro tipado `GrowthAnalysisConfigurationError`),
congela o universo analítico (filtros estruturais aplicados uma vez),
classifica intenção por ballot mode (SINGLE/MULTIPLE/ORDERED_MULTIPLE, com
exclusão conservadora `MIXED_SPECIAL_ELIGIBILITY`), exclui apoiadores atuais
(D01), segmenta apenas nos cruzamentos configurados (perfil × território pela
regra territorial oficial `ST_Covers`), calcula sinais com denominadores
explícitos por entrevista (`coleta_id` únicos), base mínima por segmento E
por sinal, delta pp, lift (referência zero → null), IC de Wilson AAS
aproximado (Decimal), qualidade de espontânea como condição
(`SIGNAL_UNAVAILABLE_QUALITY`) e snapshot com `GROWTH_ENGINE_VERSION="1.0"`,
`configuration_hash` e `input_fingerprint` determinísticos. Sem score, sem
ranking agregado, sem projeção de votos (`eleitorado_apto` apenas contexto),
`weighted_base` sempre null. Parser de múltipla escolha extraído para
`services/response_values.py` (o motor multidimensional consome via alias,
sem mudança de comportamento).

Sem endpoint, sem persistência, sem migration (head `98d2bd125070`),
`models.py` intacto, Web e Mobile intocados; feature `potencial_crescimento`
segue inativa. Testes: `tests/test_growth_analysis_engine.py` (46 casos,
E01–E112, incl. caso oráculo manual) passando em Python 3.13.14; regressão
focada e completa sem falhas novas. Documentação:
`doc/16-inteligencia-eleitoral-potencial-crescimento-motor.md`.

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 04

Implementado em 2026-09-02: API do produto — router
`pesquisa360/api/endpoints/potencial_crescimento.py` com três rotas sob
`/projetos/{id}/pesquisas/{id}/inteligencia-eleitoral/potencial-crescimento`
(`GET opcoes-configuracao`, `POST validar-configuracao`, `POST analisar`),
cadeia de segurança em ordem garantida (recurso/ACL 404 — inclusive
cross-tenant — antes de `INTELIGENCIA_VER` 403 e do gate comercial
`require_feature`), camada DTO/mapper separada do domínio
(`inteligencia_eleitoral/http_contract.py`: Decimal interno → JSON number na
borda, sem arredondamento, null preservado) e opções reais de configuração
(`inteligencia_eleitoral/options.py`: compatibilidades por regra técnica,
valores canônicos, territórios oficiais, constraints sem defaults
metodológicos). Validação retorna HTTP 200 `valid=false` para configuração
semanticamente inválida (formulário) e o motor não roda; análise é síncrona
e efêmera com 422 tipados (`GROWTH_CONFIGURATION_INVALID`,
`SEGMENT_LIMIT_EXCEEDED`). Motor, statistics e results intocados.

**API implementada ≠ produto liberado**: a feature `potencial_crescimento`
permanece INATIVA no catálogo real (rotas respondem 404 até ativação
formal); nenhum entitlement real, nenhum seed/migration alterado (head
`98d2bd125070`), `models.py` intacto, Web e Mobile intocados, nenhuma
persistência. Testes: `tests/test_growth_analysis_api.py` (36 casos,
A01–A87 + E2E) passando em Python 3.13.14; regressão focada e completa sem
falhas novas. Documentação:
`doc/17-inteligencia-eleitoral-potencial-crescimento-api.md` e doc/02
atualizado.

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 05

Implementado em 2026-09-02 (repo `pesquisa360-web`): experiência Web
funcional ponta a ponta. Rota
`/projetos/:projectId/pesquisas/:surveyId/inteligencia-eleitoral/potencial-crescimento`
protegida por `ModuleRoute` + `FeatureRoute` (primeiro uso real do
FeatureRoute da Sprint 0; fail-closed), card gated na Central de
Inteligência, service tipado (`growthPotentialService`), DTOs sem `any`,
lógica pura testável (`lib/growthPotential.ts`: reducer com estados
explícitos, dirty state, guarda de corrida por pesquisa, builders de payload
com conversão %→fração, formatadores rate/pp/lift/interval com null→"—") e
página em 7 etapas orientada por `compatible_as` (nenhuma heurística
textual), com validação-formulário (errors/warnings tipados,
VALUE_NORMALIZED informado), execução da configuração NORMALIZADA e
resultado básico (funil analítico, limitações metodológicas, diagnostics
separados, segmentos na ordem do motor, evidências com
numeradores/bases/intervalos/delta/lift/direção observada). Sem
score/ranking/projeção/persistência; contexto eleitoral apenas como
contexto.

Validação: suíte Web 1175/1175 (baseline 1119 + 56 novos, incl. ajuste de
duas contagens congeladas em `leadershipEntrypoint.test.mjs` para `>= 2`
preservando a intenção), build de produção PASS, lint com os mesmos 10 erros
preexistentes (zero novo). Backend NÃO alterado nesta rodada; Mobile
intacto; feature `potencial_crescimento` segue inativa. Documentação:
`doc/18-inteligencia-eleitoral-potencial-crescimento-web.md` e doc/04
atualizado.

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 06

Implementado em 2026-09-02 (repo `pesquisa360-web`): camada de leitura
analítica sobre o resultado — visão executiva com barra metodológica sempre
visível, seletor de sinal com direção favorável explicada (rejeição:
menor=favorável, sem inversão do delta), gráfico de diferença vs referência
(delta_pp real, zero line, suprimido/null nunca vira barra 0), scatter
escala × diferença (X=participação, Y=delta, sem quadrantes semânticos nem
limiar de escala), comparação segmento × universo elegível com IC de Wilson
e denominadores, **interpretação determinística por templates rastreáveis
(sem LLM — ADR-073)** com leitura do segmento em listas (sem contagem
"3 de 4", sem conclusão global), warnings por segmento/evidência com
fallback para códigos desconhecidos, seleção única de segmento entre
tabela/gráficos/mapa, ordenação explícita (default: ordem do motor) e
filtros que alteram somente a visualização (uma única chamada de análise).
Leitura territorial com regra antiagregação (combinação completa de perfil
obrigatória; um finding por território) e **mapa Leaflet de SETOR** via
contratos existentes (`getSetores` + `anelExternoParaLatLng`), escala de cor
contínua centrada em 0 pp; **mapa de MUNICÍPIO adiado por contrato de
geometria ausente** no Web (gap registrado; leitura tabular disponível).

Validação: suíte Web **1229/1229** (baseline 1175 + 54 novos, incl. oráculos
de narrativa: segunda opção 30%/15%/+15 pp e rejeição 12%/22%/−10 pp sem
"88% de aceitação"), build PASS, lint com os mesmos 10 erros preexistentes
(zero novo). Backend e Mobile intocados; feature `potencial_crescimento`
segue inativa. Documentação:
`doc/19-inteligencia-eleitoral-potencial-crescimento-visualizacoes.md`;
doc/04 e doc/18 atualizados.

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 07

Executado em 2026-09-02: QA independente completo. Ambiente PostgreSQL/
PostGIS DESCARTÁVEL (Docker, migrations `upgrade head` = `98d2bd125070`),
feature ativada e entitlement concedido SOMENTE nesse banco (catálogo real
intacto), dataset oráculo de 20 entrevistas somado à mão + cenários ballot/
stress. **112/112 verificações via HTTP real**: universos/elegibilidade/
denominadores exatos, rejeição múltipla contando entrevista, Wilson validado
por implementação INDEPENDENTE do QA (+âncoras de literatura), zero≠ausência,
lift ref-zero, borda territorial provada em PostGIS (`ST_Covers` true onde
`ST_Contains` é false), sobreposição/sem-coordenada diagnosticados,
determinismo/hash/fingerprint (incl. insensibilidade a resposta
não-analítica), cotas/eleitorado provados como NÃO-pesos, 404-antes-de-403,
mass assignment, SEGMENT_LIMIT tipado. **E2E Web em browser real: PASS**
(fluxo completo com funil idêntico ao oráculo e números na tela; troca A→B
fail-closed; zero 4xx/5xx). Stress: 2.400 coletas/100 segmentos ~260 ms
(síncrono confortável, ADR-070 mantida).

Correções permitidas aplicadas (Web): **F1/Q1 pré-existente** — crash de
runtime da ProjectsPage (`user` órfão desde a extração do Header na Sprint
0); **F2/Q2 pré-existente** — deep link em rotas gated expulsava usuário
licenciado (status `'idle'` tratado como não-ready em ModuleRoute/
FeatureRoute); **F3/Q2** — tipagem do onClick do Recharts v3 no delta chart.
Achados registrados para o Prompt 08: **F4** — `npm run build` não
typechecka (tsc bare em tsconfig solution-style) e o typecheck real revela
38 erros TS pré-existentes fora da feature; **F5** — EmailStr rejeita
domínios especiais (.local). Nenhum achado Q3/Q4/Q5. Backend/motor
INTOCADOS nesta rodada. Regressões: Web 1229/1229, build PASS, lint nos 10
preexistentes; Backend suíte completa verde em 3.13.

Validade interna APROVADA; validade externa LIMITADA por construção (sem
ponderação — declarada na experiência). **GO para o Prompt 08** — detalhes e
matrizes em `doc/20-inteligencia-eleitoral-potencial-crescimento-qa.md`.

## Inteligência Eleitoral — MVP 1 (Potencial de Crescimento) — Prompt 08

Executado em 2026-09-03: hardening e checkpoint final. **Potencial de
Crescimento: MVP TECNICAMENTE VALIDADO / FEATURE INATIVA.**

**F4 resolvido por completo (ADR-074):** o script `build` do Web rodava `tsc`
a seco sobre tsconfig solution-style (checava ZERO arquivos). O comando real
`tsc -b` revelou 39 erros pré-existentes de baseline (nenhum no código do
MVP 1), todos corrigidos mecanicamente sem flexibilizar o tsconfig (zero
`any` novo, zero `@ts-ignore`); dois arquivos mortos `* copy.tsx` removidos;
`tsconfig.node.json` corrigido (apontava `vite.config.ts` inexistente).
`build` agora executa `tsc -b` de verdade e há `npm run typecheck` canônico.
**Typecheck real: PASS (0 erros).**

Consolidação: fixes F1/F2/F3 do QA cobertos por guarda estática
(`tests/hardening.test.mjs`); ferramentas de QA promovidas para
`scripts/qa/growth_qa_seed.py` e `scripts/qa/growth_qa_run.py` (dados 100%
sintéticos, `P360_QA_DATABASE_URL` obrigatória sem default); E2E formalizado
como opt-in `npm run qa:growth-e2e` com credenciais SOMENTE via env.
Varreduras de segurança/linguagem limpas; feature `potencial_crescimento`
confirmada semeada `ativo=false` (migration `98d2bd125070`, head único);
nenhum entitlement real criado; `GROWTH_ENGINE_VERSION` permanece `1.0`.

Regressão definitiva: Backend suíte completa verde em Python 3.13; Web
typecheck 0 erros, suíte 1233/1233 (1229 baseline + 4 guardas), build PASS,
lint com os mesmos 10 erros preexistentes (zero novo). Mobile intocado.
Higiene: `.env` do backend NÃO é rastreado pelo git (confirmado por
`git ls-files --error-unmatch`); nenhum segredo/dado pessoal nos commits.
Checkpoint completo, commits e HEADs:
`doc/21-checkpoint-mvp1-potencial-crescimento.md`.

## Hardening do ambiente QA local + correção do seed (Prompt 15 — Backend)

Executado em 2026-09-06. Defeito **QA-BE-001** reproduzido e corrigido:
`scripts/seed_qa_dataset.py` gravava CNPJ com máscara (18 caracteres) em
`companies.cnpj`, `VARCHAR(14)` desde a migration `e7f9a2b3c4d5`, falhando com
`StringDataRightTruncation`. Causa raiz: seed desatualizado (schema e
validador corretos; nada foi alargado). O seed passou a usar CNPJs sintéticos
válidos sem máscara, validados pelo mesmo `schemas.normalize_cnpj` da API e
checados contra o limite lido do modelo; a senha embutida foi removida
(`QA_DEFAULT_PASSWORD` obrigatória); perfis `Gerente`/`Agente` são criados se
faltarem; setores ganharam vínculo N:N em `setor_agentes` e três abordagens de
campo determinísticas. Regressão em `tests/test_seed_qa_dataset.py`
(SQLite com `CHECK` real + seed completo duas vezes em PostGIS descartável,
opt-in por `P360_QA_DATABASE_URL`).

Ambiente QA local reproduzível documentado em `doc/22-qa-local.md`
(compose `db`+`api`, API na porta host 8000, migrations, seed, `adb reverse`,
`API_BASE_URL` por ambiente). O banco local do compose estava em
`b5c6d7e8f9a0` e foi levado ao head `98d2bd125070`. Smoke após o seed:
`/login/token`, `/usuarios/me/`, `/projetos/`, `/controle-campo` e `/setores`
com 200 para o Gerente QA (com `CAMPO_MONITORAR`) e 403 para o Agente QA.
Nenhuma regra de produção, contrato de API, schema ou migration alterados.

## Staging por `git push` — infraestrutura versionada (Prompt 17 — Backend)

Executado em 2026-09-06. Sem servidor nem hostname de staging aprovados
(decisão do time nesta rodada) e sem acesso SSH não interativo ao VPS de
produção, nada foi provisionado remotamente: **STAGING HOST = BLOQUEADO**.
O que foi entregue, versionado: `docker-compose.staging.yml` (projeto
`pesquisa360_staging` isolado, API só em `127.0.0.1:8010`, Postgres sem porta
publicada), `.env.staging.example`, `deploy/staging/deploy.sh` (up → healthy
→ `alembic upgrade head` → seed QA por env → smoke), vhost nginx modelo e o
workflow `.github/workflows/deploy-staging.yml` (deploy por push na branch
`staging`, gated por secrets). Ensaio local completo em `doc/23-staging.md`:
migrations do zero ao head, seed idempotente, RBAC e isolamento de tenant.
Defeito **INFRA-001** encontrado e corrigido: a imagem Docker não era
autocontida (`static/`, `migrations/`, `alembic.ini` e `scripts/` faltavam;
só funcionava com o bind mount do compose de DEV) — regressão em
`tests/test_dockerfile_packaging.py`. RC-BLK-02/03/04 permanecem bloqueados
para staging; o QA local (doc/22) continua válido.
